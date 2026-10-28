"""Walk-forward backtest (V4 design doc §13.2), layer 3: forecast next quarter, score it later.

  taskset -c 16-31 uv run python -m tenk_agent.evaluation.backtest --label defaults [TICKERS]

For each company and each earnings release from Q4 2024 on whose next quarter has reported:
D = the day after the release's EDGAR acceptance (US Eastern), the model forecasts the next
quarter at D (research/forecast.py), and it is scored against the next quarter's first-reported
value (as visible the day after that quarter's release), on revenue and the company's main KPI
(companies.yaml `main_kpi`). Baselines: seasonal naive (same quarter last year × the latest y/y
growth) and the guidance midpoint where the company guided that line.

Leakage probe: every forecast's model must pass no_future_input (each input filed before D) and
must not hold the target quarter in its history; either failure stops the run.

Gate (spec §5 layer 3): MAPE below seasonal naive on revenue and on the main KPI, pooled over
all forecasts; bias reported with its sign (positive = forecast too high). Writes
evaluation/results/backtest-<label>/: forecasts.csv, summary.json and report.md.
"""

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from scipy.stats import wilcoxon

from tenk_agent.asof import AsOf
from tenk_agent.corpus import companies_path
from tenk_agent.research import datapack
from tenk_agent.research.forecast import forecast, knobs, next_quarter
from tenk_agent.research.model.checks import no_future_input
from tenk_agent.research.model.graph import load_template
from tenk_agent.research.model.history import TEMPLATE_OF
from tenk_agent.store import Store

ROOT = Path(__file__).resolve().parents[3]
FIRST_RELEASE = "2024Q4"
EASTERN = ZoneInfo("America/New_York")


@dataclass
class Row:
    ticker: str
    as_of: str
    issued: str  # the release the forecast follows
    target: str  # the quarter forecast
    metric: str  # revenue or the main KPI's model line
    kpi: str  # how companies.yaml names it
    actual: float
    forecast: float | None
    seasonal_naive: float | None
    guidance_mid: float | None
    anchored: bool  # the forecast was solved to a guide


def run_day(release) -> str:
    """The day after the release's acceptance in US Eastern time (filing date if unknown)."""
    if release.accepted_at:
        accepted = datetime.fromisoformat(release.accepted_at.replace("Z", "+00:00"))
        if accepted.tzinfo is None:
            accepted = accepted.replace(tzinfo=ZoneInfo("UTC"))
        day = accepted.astimezone(EASTERN).date()
    else:
        day = date.fromisoformat(release.filing_date)
    return (day + timedelta(days=1)).isoformat()


def releases(store: Store, ticker: str) -> dict[str, object]:
    return {f"{f.fiscal_year}{f.fiscal_period}": f for f in store.filings()
            if f.ticker == ticker and f.form == "ER"}


def metrics_for(ticker: str) -> list[tuple[str, str]]:
    """(model line, KPI name) for revenue and the main KPI."""
    import yaml

    main = yaml.safe_load(companies_path().read_text())["companies"][ticker]["main_kpi"]
    lines, _ = load_template(TEMPLATE_OF[ticker], ticker)
    out = [("revenue", "revenue")]
    if main != "revenue":
        line = next(x.name for x in lines if x.kind == "input" and x.source == f"kpi:{main}")
        out.append((line, main))
    return out


def backtest(db: Path, ticker: str, settings: dict) -> list[Row]:
    today = Store(db, AsOf.today())
    by_period = releases(today, ticker)
    rows = []
    for period, release in sorted(by_period.items()):
        target = next_quarter(period)
        if period < FIRST_RELEASE or target not in by_period:
            continue
        as_of = run_day(release)
        store = Store(db, AsOf.of(as_of))
        f = forecast(store, ticker, settings)
        model = f.model
        if not model.forecast or model.forecast[0] != target:
            raise RuntimeError(f"{ticker} at {as_of}: forecast starts {model.forecast[:1]}, "
                               f"not {target}")
        leak = no_future_input(model)
        if leak.status == "fail" or target in model.history:
            raise RuntimeError(f"leak: {ticker} at {as_of}: {leak.failures[:3]}")
        actual_store = Store(db, AsOf.of(run_day(by_period[target])))
        for line, kpi in metrics_for(ticker):
            spec = model.line(line)
            pack = datapack.build(actual_store, ticker, {s for s in (spec.source, spec.fallback)
                                                         if s})
            point = pack[spec.source].get(target) or (
                pack.get(spec.fallback, {}).get(target) if spec.fallback else None)
            if point is None:
                continue
            anchor = next((a for a in f.anchors if a.line == line and a.reached), None)
            rows.append(Row(ticker, as_of, period, target, line, kpi, point.value,
                            model.value(line, target), _seasonal_naive(model, line, target),
                            next((a.target for a in f.anchors if a.line == line), None),
                            anchor is not None))
    return rows


def _seasonal_naive(model, line: str, target: str) -> float | None:
    """Same quarter last year × (1 + the last reported quarter's y/y growth)."""
    quarters = model.quarters
    i, last = quarters.index(target), quarters.index(model.history[-1])
    if i < 4 or last < 4:
        return None
    base, now, then = (model.value(line, quarters[k]) for k in (i - 4, last, last - 4))
    if None in (base, now, then) or not then:
        return None
    return base * now / then


# --- Scoring -------------------------------------------------------------------------------


def score(rows: list[Row]) -> dict:
    out = {}
    for group in ("revenue", "main_kpi"):
        chosen = [r for r in rows if (r.metric == "revenue") == (group == "revenue")]
        if group == "main_kpi":  # TRIP's main KPI is revenue
            chosen += [r for r in rows if r.metric == "revenue" and r.kpi == "revenue"
                       and r.ticker == "TRIP"]
        out[group] = _errors(chosen)
    out["gate"] = all(out[g]["model_mape"] is not None and out[g]["naive_mape"] is not None
                      and out[g]["model_mape"] < out[g]["naive_mape"]
                      for g in ("revenue", "main_kpi"))
    out["by_company"] = {
        t: {g: _errors([r for r in rows if r.ticker == t and (r.metric == "revenue") == (
            g == "revenue")]) for g in ("revenue", "main_kpi")}
        for t in sorted({r.ticker for r in rows})
    }
    guided = [r for r in rows if r.guidance_mid is not None]
    out["guidance"] = {
        "forecasts": len(guided),
        "actual_above_mid": sum(r.actual > r.guidance_mid for r in guided),
        "actual_below_mid": sum(r.actual < r.guidance_mid for r in guided),
    }
    return out


def _errors(rows: list[Row]) -> dict:
    paired = [r for r in rows if r.forecast is not None and r.seasonal_naive is not None]
    model = [abs(r.forecast / r.actual - 1) for r in paired]
    naive = [abs(r.seasonal_naive / r.actual - 1) for r in paired]
    guided = [r for r in paired if r.guidance_mid is not None]
    p = None
    if len(paired) >= 6 and any(m != n for m, n in zip(model, naive, strict=True)):
        p = float(wilcoxon(model, naive).pvalue)
    return {
        "forecasts": len(paired),
        "model_mape": _mean(model), "naive_mape": _mean(naive),
        "model_median_ape": _median(model), "naive_median_ape": _median(naive),
        "model_bias": _mean([r.forecast / r.actual - 1 for r in paired]),
        "naive_bias": _mean([r.seasonal_naive / r.actual - 1 for r in paired]),
        "wilcoxon_p": p,
        "guided": len(guided),
        "guide_mid_mape": _mean([abs(r.guidance_mid / r.actual - 1) for r in guided]),
        "model_mape_where_guided": _mean([abs(r.forecast / r.actual - 1) for r in guided]),
        "missing_forecast": len([r for r in rows if r.forecast is None]),
    }


def _mean(values):
    return sum(values) / len(values) if values else None


def _median(values):
    s = sorted(values)
    if not s:
        return None
    return s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2


def report(label: str, rows: list[Row], result: dict) -> str:
    def pct(v):
        return "–" if v is None else f"{v:.2%}"

    lines = [f"# Backtest {label}", "",
             f"{len(rows)} forecasts; gate (MAPE below seasonal naive on revenue and main KPI): "
             f"**{'pass' if result['gate'] else 'fail'}**", "",
             "| | Forecasts | Model MAPE | Naive MAPE | Model bias | Naive bias | Wilcoxon p |"
             " Guide-mid MAPE (n) |", "|---|---|---|---|---|---|---|---|"]
    for g in ("revenue", "main_kpi"):
        e = result[g]
        p = "–" if e["wilcoxon_p"] is None else f"{e['wilcoxon_p']:.3f}"
        lines.append(f"| {g} | {e['forecasts']} | {pct(e['model_mape'])} | "
                     f"{pct(e['naive_mape'])} | {pct(e['model_bias'])} | {pct(e['naive_bias'])} | "
                     f"{p} | {pct(e['guide_mid_mape'])} ({e['guided']}) |")
    lines += ["", "## By company", "",
              "| Company | Revenue: model / naive MAPE | Main KPI: model / naive MAPE |",
              "|---|---|---|"]
    for t, e in result["by_company"].items():
        lines.append(f"| {t} | {pct(e['revenue']['model_mape'])} / "
                     f"{pct(e['revenue']['naive_mape'])} | {pct(e['main_kpi']['model_mape'])} / "
                     f"{pct(e['main_kpi']['naive_mape'])} |")
    g = result["guidance"]
    lines += ["", f"Guided forecasts: {g['forecasts']}; actual above the guided midpoint "
              f"{g['actual_above_mid']}, below {g['actual_below_mid']}."]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="*", default=sorted(TEMPLATE_OF))
    ap.add_argument("--label", default="defaults")
    ap.add_argument("--db", type=Path, default=ROOT / "data/v4/build/parent_child.db")
    ap.add_argument("--knobs", type=Path, help="research.yaml (default: the repo's)")
    args = ap.parse_args()
    settings = knobs(args.knobs) if args.knobs else knobs()
    rows = []
    for ticker in args.tickers:
        found = backtest(args.db, ticker, settings)
        print(f"{ticker}: {len(found)} forecasts")
        rows += found
    result = score(rows)
    out = ROOT / "evaluation" / "results" / f"backtest-{args.label}"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "forecasts.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(asdict(rows[0])))
        writer.writeheader()
        writer.writerows(asdict(r) for r in rows)
    (out / "summary.json").write_text(json.dumps(result, indent=1))
    (out / "report.md").write_text(report(args.label, rows, result))
    print((out / "report.md").read_text())


if __name__ == "__main__":
    main()
