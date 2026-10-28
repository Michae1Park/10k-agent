"""S5 forecast (V4 design doc §6.1): history model → forecast quarters to research.yaml's horizon.

Per driver, never per total: a growth driver x = x[-4] × (1 + g), g starting at its last y/y
and fading by d each quarter; a ratio (take rate, margin) = its same quarter last year plus a
fading shift, kept within its last-8-quarter min/max ± a band. The assumptions those rules read
(`@name` in the templates) get their defaults here, from the history; the proposer (V4.4, LLM
or defaults) may move them within bounds. Then each guidance anchor in the template solves one
assumption so the first forecast quarter hits the guided midpoint (bookings, then revenue, then
EBITDA). An anchor the bounds keep from reaching its target is logged, never forced.

  taskset -c 16-31 uv run python -m tenk_agent.research.forecast UBER BKNG 2026-09-01
"""

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from tenk_agent.asof import AsOf
from tenk_agent.research.model import checks as layer2
from tenk_agent.research.model.graph import Model, build, evaluate_cells, load_template
from tenk_agent.research.model.history import history, write
from tenk_agent.store import Store

KNOBS = Path(__file__).resolve().parents[3] / "research.yaml"


@dataclass
class Anchor:
    guide: str
    line: str
    target_period: str
    target: float
    solve: str
    before: float  # the assumption's default
    after: float  # solved
    reached: bool  # the model hits the target within 0.1%
    issued_in: str
    guide_id: str


@dataclass
class Forecast:
    model: Model
    pack: dict
    defaults: dict[str, float]
    anchors: list[Anchor] = field(default_factory=list)
    exceptions: list[dict] = field(default_factory=list)


def knobs(path: Path = KNOBS) -> dict:
    return yaml.safe_load(path.read_text())


def quarters_through(first: str, last: str) -> list[str]:
    year, q = int(first[:4]), int(first[5])
    out = []
    while f"{year}Q{q}" <= last:
        out.append(f"{year}Q{q}")
        year, q = (year + 1, 1) if q == 4 else (year, q + 1)
    return out


def next_quarter(quarter: str) -> str:
    year, q = int(quarter[:4]), int(quarter[5])
    return f"{year + 1}Q1" if q == 4 else f"{year}Q{q + 1}"


def forecast(store: Store, ticker: str, settings: dict | None = None,
             overrides: dict[str, float] | None = None) -> Forecast:
    """The model with forecast columns: defaults, then `overrides` (the proposer's), then the
    guidance anchors."""
    settings = settings or knobs()
    base, pack, _, _ = history(store, ticker)
    horizon = settings["forecast"]["horizon"]
    future = quarters_through(next_quarter(base.quarters[-1]), horizon)
    model = build(ticker, base.template, base.as_of, base.quarters, pack, future, {})
    defaults = default_assumptions(model, settings)
    model.assumptions = defaults | (overrides or {})
    evaluate_cells(model, pack)
    result = Forecast(model, pack, defaults)
    _, template = load_template(model.template, ticker)
    result.anchors = anchor(model, pack, store, template.get("anchors") or [], settings, result)
    if overrides:
        result.exceptions += [{"severity": "info", "item": f"assumption {k} = {v:.4g} "
                               f"(default {defaults.get(k, float('nan')):.4g})"}
                              for k, v in overrides.items()]
    result.exceptions.append({"severity": "info", "item": "working capital held at 0 in the "
                              "forecast; interest and other non-operating items 0 (§5.2)"})
    return result


# --- Defaults ------------------------------------------------------------------------------


def referenced(model: Model) -> set[str]:
    return {a for x in model.lines for e in (x.expr, x.forecast, x.forecast_first, x.fy_forecast)
            if e for a in e.assumptions}


def default_assumptions(model: Model, settings: dict) -> dict[str, float]:
    f = settings["forecast"]
    _, template = load_template(model.template, model.ticker)
    bands = {r: f["ratio_bounds"][kind]
             for r, kind in ((template.get("forecast") or {}).get("ratios") or {}).items()}
    out: dict[str, float] = {}
    for name in sorted(referenced(model)):
        value = _default(model, name, f, bands)
        if value is not None:
            out[name] = value
    return out


def _default(model: Model, name: str, f: dict, bands: dict) -> float | None:
    """None: the history can't give one (ABNB tags no D&A); its cells stay blank, logged."""
    if name == "fade":
        return f["fade"]
    if name == "gb_g_adj":
        return 0.0
    if m := re.fullmatch(r"(.+)\.g0", name):  # a growth rate starts at its last y/y
        return _last(model, m[1], default=0.0)
    if m := re.fullmatch(r"(.+)\.shift0", name):  # a ratio's change starts at its last y/y
        return _last(model, f"{m[1]}_shift", default=0.0)
    if m := re.fullmatch(r"(.+)\.(lo|hi)", name):
        values = [v for v in _recent(model, m[1], 8) if v is not None]
        if not values:
            return float("-inf") if m[2] == "lo" else float("inf")
        band = bands.get(m[1], 0.0)
        return min(values) - band if m[2] == "lo" else max(values) + band
    if m := re.fullmatch(r"(.+)\.ratio", name):  # a line's trailing share of revenue
        pairs = [(v, r) for v, r in zip(_recent(model, m[1], 4), _recent(model, "revenue", 4),
                                        strict=True) if v is not None and r]
        return sum(v for v, _ in pairs) / sum(r for _, r in pairs) if pairs else None
    if name == "tax_rate":
        tax = [v for v in _recent(model, "income_tax", 4) if v is not None]
        income = [v for v in _recent(model, "net_income", 4) if v is not None]
        rate = sum(tax) / (sum(tax) + sum(income)) if tax and income and sum(tax) + sum(
            income) else f["tax_rate_bounds"][0]
        lo, hi = f["tax_rate_bounds"]
        return min(max(rate, lo), hi)
    if name == "shares_last":
        return _last(model, "shares_diluted", default=0.0)
    if name == "share_change":
        shares = [(i, v) for i, v in enumerate(_recent(model, "shares_diluted", 5)) if v]
        if len(shares) < 2:
            return 0.0
        (i0, first), (i1, last) = shares[0], shares[-1]
        return (last / first) ** (1 / (i1 - i0)) - 1
    if name == "buybacks_q":
        values = [v for v in _recent(model, "buybacks", 4) if v is not None]
        return sum(values) / len(values) if values else 0.0
    raise ValueError(f"no default for assumption {name!r}")


def _recent(model: Model, line: str, n: int) -> list[float | None]:
    history = model.history
    return [model.value(line, q) for q in history[-n:]]


def _last(model: Model, line: str, default: float) -> float:
    return next((v for v in reversed(_recent(model, line, 8)) if v is not None), default)


# --- Guidance anchors ----------------------------------------------------------------------


def anchor(model: Model, pack, store: Store, anchors: list[dict], settings: dict,
           result: Forecast) -> list[Anchor]:
    """Solve each anchored assumption so the first forecast quarter hits the guide."""
    if not model.forecast:
        return []
    first = model.forecast[0]
    guides = _guides(store, model.ticker, first)
    point = settings["forecast"]["guidance_point"]
    out, solved = [], set()
    for spec in anchors:
        guide = guides.get(spec["guide"])
        if guide is None:
            continue
        if spec["solve"] in solved:  # a level and a growth guide of one metric: the first wins
            result.exceptions.append({"severity": "info", "item": f"anchor {spec['guide']} "
                                      f"{first} not used: {spec['solve']} already anchored"})
            continue
        solved.add(spec["solve"])
        mid = guide["high"] if point == "high" and guide["high"] is not None else (
            guide["low"] if guide["high"] is None else (guide["low"] + guide["high"]) / 2)
        if spec["basis"] == "growth_yoy":
            prior = model.value(spec["line"], model.quarters[model.quarters.index(first) - 4])
            if prior is None:
                continue
            target = prior * (1 + mid / 100)
        else:
            target = mid
        before = model.assumptions[spec["solve"]]
        after, reached = _solve(model, pack, spec["solve"], spec["line"], first, target)
        out.append(Anchor(spec["guide"], spec["line"], first, target, spec["solve"], before,
                          after, reached, guide["issued_in"],
                          f"guide:{model.ticker}:{spec['guide']}:{first}"))
        if not reached:
            got = model.value(spec["line"], first)
            result.exceptions.append({
                "severity": "warning",
                "item": f"anchor {spec['guide']} {first}: target {target:,.1f} not reached "
                        f"({spec['line']} {'blank' if got is None else f'{got:,.1f}'})"})
    return out


def _guides(store: Store, ticker: str, target: str) -> dict[str, dict]:
    """The latest visible guide per metric for the target quarter (consolidated)."""
    out = {}
    for g in store.guidance(ticker):  # in filing order: a later guide replaces an earlier one
        if g["target_period"] == target and g["segment"] == "total":
            out[g["metric"]] = g
    return out


def _solve(model: Model, pack, name: str, line: str, column: str, target: float) -> tuple[
        float, bool]:
    """One assumption so the line hits the target: expand a bracket around the default until
    the gap changes sign, then bisect (a ratio's clamp makes the response flat at its bounds,
    which stalls a secant). Leaves the model at the best value found."""
    def gap(x: float) -> float | None:
        model.assumptions[name] = x
        evaluate_cells(model, pack)
        value = model.value(line, column)
        return None if value is None else value - target

    x0 = model.assumptions[name]
    g0 = gap(x0)
    best = (abs(g0), x0) if g0 is not None else (float("inf"), x0)
    bracket = None
    step = 1e-3
    while g0 is not None and bracket is None and step < 10:
        for x in (x0 - step, x0 + step):
            g = gap(x)
            if g is None:
                continue
            best = min(best, (abs(g), x))
            if (g < 0) != (g0 < 0):
                bracket = (x0, g0, x, g)
                break
        step *= 2
    if bracket:
        a, ga, b, _ = bracket
        for _ in range(60):
            mid = (a + b) / 2
            g = gap(mid)
            if g is None:
                break
            best = min(best, (abs(g), mid))
            if (g < 0) == (ga < 0):
                a, ga = mid, g
            else:
                b = mid
    gap(best[1])
    return best[1], best[0] <= 1e-3 * max(1.0, abs(target))


# --- CLI ---------------------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("as_of")
    ap.add_argument("--db", type=Path, default=Path("data/v4/build/parent_child.db"))
    ap.add_argument("--out", type=Path, default=Path("output/research"))
    args = ap.parse_args()
    from tenk_agent.research.export import workbook

    store = Store(args.db, AsOf.of(args.as_of))
    failed = False
    for ticker in args.tickers:
        f = forecast(store, ticker)
        results = layer2.run(f.model, f.pack, store)
        exceptions = layer2.exceptions(f.model, results) + f.exceptions
        folder = write(args.out, f.model, f.pack, results, exceptions)
        (folder / "forecast.json").write_text(json.dumps(
            {"assumptions": f.model.assumptions, "defaults": f.defaults,
             "anchors": [asdict(a) for a in f.anchors]}, indent=1))
        checked, differ = workbook.parity(f.model, workbook.write(f.model,
                                                                  folder / "workbook.xlsx"))
        bad = [c for c in results if c.status == "fail"]
        status = f"FAIL {[c.name for c in bad]}" if bad else "pass"
        print(f"{ticker} → {folder}  checks {status}  parity {checked - len(differ)}/{checked}")
        for a in f.anchors:
            print(f"  anchor {a.guide:<24} {a.target_period} target {a.target:>10,.1f} "
                  f"{a.solve} {a.before:+.4f} → {a.after:+.4f}"
                  f"{'' if a.reached else '  NOT REACHED'}")
        failed |= bool(bad) or bool(differ)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
