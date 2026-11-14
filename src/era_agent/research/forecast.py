"""S5 forecast (V4 design doc §6.2): history model → forecast quarters to research.yaml's horizon.

Per driver, never per total: a growth driver x = x[-4] × (1 + g) in the first forecast quarter
(the one guidance anchors), then last fiscal year's total × (1 + g) × the quarter's seasonal
share (spec §3.2: the mean share of that quarter over the latest three complete fiscal years of
history, fewer where fewer exist, logged); g starts at its last y/y and fades by d each quarter;
a ratio (take rate, margin) = its same quarter last year plus a fading shift, kept within its
last-8-quarter min/max ± a band. The assumptions those rules read (`@name` in the templates) get
their defaults here, from the history; the proposer (V4.4, LLM or defaults) may move them within
bounds. Then each guidance anchor in the template solves one assumption so the first forecast
quarter hits the guided midpoint (bookings, then revenue, then EBITDA). An anchor the bounds
keep from reaching its target is logged, never forced.

Scenarios (§8.3): bear and bull move each of the template's `scenarios` drivers by ∓/±
shock_sigma × σ, σ the standard deviation of that driver's line over the history (a growth
rate's y/y values, a ratio's y/y changes), from the anchored base; weights from research.yaml.

Outside inputs (spec §3.2): research/outside.py moves mapped starting growth rates toward a
nowcast from TLC, BTS or FRED series before the proposer and the anchors (`forecast.outside`).

  uv run python -m era_agent.research.forecast UBER BKNG 2026-09-01
"""

import argparse
import json
import re
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from era_agent.asof import AsOf
from era_agent.research import outside
from era_agent.research.model import checks as layer2
from era_agent.research.model.graph import Model, build, evaluate_cells, load_template
from era_agent.research.model.history import history, write
from era_agent.store import Store

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
    proposals: list = field(default_factory=list)  # research/propose.py Proposal


def knobs(path: Path = KNOBS) -> dict:
    return yaml.safe_load(path.read_text())


def quarters_through(first: str, last: str) -> list[str]:
    year, q = int(first[:4]), int(first[5])
    out = []
    while f"{year}Q{q}" <= last:
        out.append(f"{year}Q{q}")
        year, q = (year + 1, 1) if q == 4 else (year, q + 1)
    return out


def relative_year(setting, as_of: str) -> int:
    """A research.yaml year: absolute (2027) or an offset from the as-of's year ("+1")."""
    s = str(setting)
    return int(as_of[:4]) + int(s) if s.startswith(("+", "-")) else int(s)


def horizon_quarter(setting, as_of: str) -> str:
    """The last forecast quarter: a quarter key (2027Q4), or Q4 of a relative year ("+1")."""
    s = str(setting)
    return s if "Q" in s else f"{relative_year(s, as_of)}Q4"


def next_quarter(quarter: str) -> str:
    year, q = int(quarter[:4]), int(quarter[5])
    return f"{year + 1}Q1" if q == 4 else f"{year}Q{q + 1}"


def forecast(store: Store, ticker: str, settings: dict | None = None,
             overrides: dict[str, float] | None = None) -> Forecast:
    """The model with forecast columns: defaults, then `overrides` (the proposer's), then the
    guidance anchors."""
    settings = settings or knobs()
    base, pack, _, _ = history(store, ticker)
    horizon = horizon_quarter(settings["forecast"]["horizon"], base.as_of)
    future = quarters_through(next_quarter(base.quarters[-1]), horizon)
    model = build(ticker, base.template, base.as_of, base.quarters, pack, future, {})
    defaults = default_assumptions(model, settings)
    model.assumptions = dict(defaults)
    evaluate_cells(model, pack)  # history growth lines, for the outside-input fit
    defaults, outside_log = outside.adjust(model, store, defaults, settings)
    model.assumptions = defaults | (overrides or {})
    evaluate_cells(model, pack)
    result = Forecast(model, pack, defaults, exceptions=list(outside_log))
    _, template = load_template(model.template, ticker)
    result.anchors = anchor(model, pack, store, template.get("anchors") or [], settings, result)
    if overrides:
        result.exceptions += [{"severity": "info", "item": f"assumption {k} = {v:.4g} "
                               f"(default {defaults.get(k, float('nan')):.4g})"}
                              for k, v in overrides.items()]
    for name in sorted(n for n in referenced(model) if n.endswith(".season")):
        line = name[:-len(".season")]
        _, years = seasonal_shares(model, line)
        if len(years) < SHARE_YEARS:
            result.exceptions.append({
                "severity": "info",
                "item": f"seasonal share of {line} from {len(years)} complete fiscal year(s) "
                        f"({', '.join(years) or 'none: even shares'}), not {SHARE_YEARS}"})
    result.exceptions.append({"severity": "info", "item": "working capital held at 0 in the "
                              "forecast; interest and other non-operating items 0 (§8.3)"})
    return result


def forecast_with_proposer(store: Store, ticker: str, settings: dict, search=None) -> Forecast:
    """research.yaml proposer.method: defaults → forecast(); llm → the proposer sets the
    drivers no guide anchors, from a base forecast, and the anchors run again over its values."""
    base = forecast(store, ticker, settings)
    if settings["proposer"]["method"] != "llm":
        return base
    from era_agent.research.propose import propose

    anchored = {a.solve: a.after for a in base.anchors}
    overrides, proposals, exceptions = propose(base.model, store, anchored, settings, search)
    result = forecast(store, ticker, settings, overrides)
    result.proposals, result.exceptions = proposals, result.exceptions + exceptions
    return result


# --- Scenarios ---------------------------------------------------------------------------------

SUMMARY = ("revenue", "adj_ebitda", "eps_diluted", "free_cash_flow")


def scenarios(f: Forecast, settings: dict) -> dict[str, Model]:
    """{bear, base, bull}: models with the scenario drivers shocked from the anchored base."""
    model = f.model
    _, template = load_template(model.template, model.ticker)
    k = settings["scenarios"]["shock_sigma"]
    shocks = {}
    for name in template.get("scenarios") or []:
        line = name.rsplit(".", 1)[0]
        line = f"{line}_shift" if name.endswith(".shift0") else line
        values = [v for v in (model.value(line, q) for q in model.history) if v is not None]
        if len(values) >= 3 and name in model.assumptions:
            shocks[name] = k * statistics.stdev(values)
    out = {"base": model}
    for scenario, sign in (("bear", -1), ("bull", 1)):
        assumptions = dict(model.assumptions)
        for name, shock in shocks.items():
            assumptions[name] += sign * shock
        out[scenario] = build(model.ticker, model.template, model.as_of, model.history, f.pack,
                              model.forecast, assumptions)
    return out


def scenario_summary(models: dict[str, Model], weights: dict[str, float]) -> dict:
    """Per scenario and weighted: the summary lines in each FY column with a forecast quarter."""
    years = sorted({c for c in models["base"].columns if c.startswith("FY")
                    and any(q.startswith(c[2:]) for q in models["base"].forecast)})
    out = {name: {line: {y: m.value(line, y) for y in years} for line in SUMMARY}
           for name, m in models.items()}
    out["weighted"] = {line: {y: (sum(weights[n] * out[n][line][y] for n in weights)
                                  if all(out[n][line][y] is not None for n in weights) else None)
                              for y in years} for line in SUMMARY}
    return out


# --- Defaults ------------------------------------------------------------------------------


def referenced(model: Model) -> set[str]:
    return {a for x in model.lines
            for e in (x.expr, x.forecast, x.forecast_first, x.fy_forecast, x.forecast_late)
            if e for a in e.assumptions}


def default_assumptions(model: Model, settings: dict) -> dict[str, float]:
    f = settings["forecast"]
    _, template = load_template(model.template, model.ticker)
    bands = {r: f["ratio_bounds"][kind]
             for r, kind in ((template.get("forecast") or {}).get("ratios") or {}).items()}
    out: dict[str, float] = {}
    for name in sorted(referenced(model)):
        if name.endswith(".season"):  # @x.season → the four shares @x.s1…@x.s4
            out |= {f"{name[:-len('season')]}s{i}": v
                    for i, v in enumerate(seasonal_shares(model, name[:-len(".season")])[0], 1)}
            continue
        value = _default(model, name, f, bands)
        if value is not None:
            out[name] = value
    return out


SHARE_YEARS = 3  # spec §3.2: the 3-year average seasonal share


def seasonal_shares(model: Model, line: str) -> tuple[list[float], list[str]]:
    """Each quarter's mean share of its fiscal year over the latest SHARE_YEARS complete years
    of history (all four quarters reported), and the years used. No complete year: an even
    0.25 each (the q-4 rule then carries the forecast where last year is incomplete)."""
    history = set(model.history)
    years = sorted({q[:4] for q in history}, reverse=True)
    used, shares = [], [[], [], [], []]
    for year in years:
        values = [model.value(line, f"{year}Q{i}") if f"{year}Q{i}" in history else None
                  for i in (1, 2, 3, 4)]
        if None in values or not sum(values):
            continue
        for i, v in enumerate(values):
            shares[i].append(v / sum(values))
        used.append(year)
        if len(used) == SHARE_YEARS:
            break
    if not used:
        return [0.25] * 4, []
    return [sum(x) / len(x) for x in shares], sorted(used)


def _default(model: Model, name: str, f: dict, bands: dict) -> float | None:
    """None: the history can't give one (ABNB tags no D&A); its cells stay blank, logged."""
    if name == "fade":
        if f["fade"] != "fitted":
            return f["fade"]
        # fitted (ablation, §11.3): the median quarter-to-quarter ratio of the growth drivers'
        # y/y rates over the last 8 quarters, within fade_bounds
        ratios = []
        for line in (x.name for x in model.lines if x.name.endswith("_g")):
            rates = [v for v in _recent(model, line, 8) if v is not None]
            ratios += [b / a for a, b in zip(rates, rates[1:], strict=False) if a > 0 and b > 0]
        lo, hi = f["fade_bounds"]
        return min(max(statistics.median(ratios), lo), hi) if ratios else f["fade_bounds"][1]
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
        if not pairs and m[1] == "d_and_a":  # untagged (ABNB): the reported EBITDA-to-EBIT
            pairs = [(a - s - o, r) for a, s, o, r in zip(  # bridge, D&A and other items
                *(_recent(model, x, 4) for x in ("adj_ebitda", "sbc", "operating_income",
                                                 "revenue")), strict=True)
                if None not in (a, s, o) and r]
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
    """Solve each anchored assumption so the first forecast quarter hits the guide; an anchor
    with `period: fy` uses the guide for the first forecast quarter's fiscal year and solves
    the FY column (a full-year-only guide: ABNB's adj. EBITDA margin). Template order sets
    priority: quarterly anchors come first, and a driver already solved is not moved again."""
    if not model.forecast:
        return []
    first = model.forecast[0]
    fiscal_year = f"FY{first[:4]}"
    guides = {"quarter": _guides(store, model.ticker, first),
              "fy": _guides(store, model.ticker, fiscal_year)}
    point = settings["forecast"]["guidance_point"]
    out, solved = [], set()
    for spec in anchors:
        period = spec.get("period", "quarter")
        column = fiscal_year if period == "fy" else first
        if column not in model.columns:
            continue
        guide = guides[period].get(spec["guide"])
        if guide is None:
            continue
        if spec["solve"] in solved:  # a level and a growth guide of one metric: the first wins
            result.exceptions.append({"severity": "info", "item": f"anchor {spec['guide']} "
                                      f"{column} not used: {spec['solve']} already anchored"})
            continue
        solved.add(spec["solve"])
        mid = guide["high"] if point == "high" and guide["high"] is not None else (
            guide["low"] if guide["high"] is None else (guide["low"] + guide["high"]) / 2)
        if spec["basis"] == "growth_yoy":
            prior = model.value(spec["line"], f"FY{int(first[:4]) - 1}" if period == "fy"
                                else model.quarters[model.quarters.index(first) - 4])
            if prior is None:
                continue
            target = prior * (1 + mid / 100)
        elif spec["basis"] == "margin":  # guided in percent, the line is a fraction
            target = mid / 100
        else:
            target = mid
        before = model.assumptions[spec["solve"]]
        after, reached = _solve(model, pack, spec["solve"], spec["line"], column, target)
        out.append(Anchor(spec["guide"], spec["line"], column, target, spec["solve"], before,
                          after, reached, guide["issued_in"],
                          f"guide:{model.ticker}:{spec['guide']}:{column}"))
        if not reached:
            got = model.value(spec["line"], column)
            result.exceptions.append({
                "severity": "warning",
                "item": f"anchor {spec['guide']} {column}: target {target:,.4g} not reached "
                        f"({spec['line']} {'blank' if got is None else f'{got:,.4g}'})"})
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
    from era_agent.research.export import workbook

    store = Store(args.db, AsOf.of(args.as_of))
    settings = knobs()
    failed = False
    for ticker in args.tickers:
        f = forecast(store, ticker, settings)
        results = layer2.run(f.model, f.pack, store)
        exceptions = layer2.exceptions(f.model, results) + f.exceptions
        folder = write(args.out, f.model, f.pack, results, exceptions)
        models = scenarios(f, settings)
        (folder / "forecast.json").write_text(json.dumps(
            {"assumptions": f.model.assumptions, "defaults": f.defaults,
             "anchors": [asdict(a) for a in f.anchors],
             "scenarios": scenario_summary(models, settings["scenarios"]["weights"])}, indent=1))
        checked, differ = workbook.parity(f.model, workbook.write(f.model,
                                                                  folder / "workbook.xlsx"))
        bad = [c for c in results if c.status == "fail"]
        status = f"FAIL {[c.name for c in bad]}" if bad else "pass"
        print(f"{ticker} → {folder}  checks {status}  parity {checked - len(differ)}/{checked}")
        for a in f.anchors:
            print(f"  anchor {a.guide:<24} {a.target_period} target {a.target:>10,.4g} "
                  f"{a.solve} {a.before:+.4f} → {a.after:+.4f}"
                  f"{'' if a.reached else '  NOT REACHED'}")
        failed |= bool(bad) or bool(differ)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
