"""Layer-2 integrity checks on a built model (V4 design doc §5.4). Any FAIL stops the run.

  balance          assets = liabilities + mezzanine + equity, and assets = the reported
                   liabilities-and-equity total, every period, within $1M
  quarters_sum     the model's FY column (its quarters summed) = the reported fiscal year
  q4_ties          derived Q4 + 9M = the 10-K's FY (facts/quarters.py, per XBRL flow line)
  take_rate        each take rate recomputes from the reported revenue and gross bookings
  segments_sum     segment lines sum to the reported total (template `sums`)
  no_future_input  every input was filed before the as-of
  formula_only     forecast cells are formulas (V4.4 adds forecast columns)

A check that has nothing to test in a period (an input missing) skips it and counts the skip;
the skips and the model's other gaps are listed as exceptions, never hidden.
V4.4–V4.5 add wacc_gt_g, weights and bridge_ties.
"""

from dataclasses import asdict, dataclass, field

from tenk_agent.facts import quarters as xbrl_quarters
from tenk_agent.research.datapack import Point
from tenk_agent.research.model.graph import Model, load_template
from tenk_agent.store import Store

TOLERANCE = 1.0  # USD millions: figures printed in whole millions


@dataclass
class Check:
    name: str
    status: str  # pass, fail, skip
    tested: int = 0  # periods or lines tested
    failures: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    note: str = ""


def run(model: Model, pack: dict[str, dict[str, Point]], store: Store | None) -> list[Check]:
    """Every check; q4_ties reads the XBRL facts, so it needs the store."""
    _, template = load_template(model.template, model.ticker)
    return [
        balance(model), quarters_sum(model, pack),
        *([q4_ties(model, store)] if store is not None else []),
        take_rate(model, pack), segments_sum(model, template.get("sums") or {}),
        no_future_input(model), formula_only(model),
    ]


def exceptions(model: Model, checks: list[Check]) -> list[dict]:
    """What the model lacks or assumed: defaults used, inputs missing, points rescaled for a
    split, periods a check skipped. Severity `info`; a failed check is `error`."""
    out = [{"severity": "info", "item": f"default used: {d}"} for d in model.defaults]
    for line in model.lines:
        missing = [c for c in model.quarters
                   if line.kind == "input" and model.cells[(line.name, c)].kind == "missing"]
        if missing:
            out.append({"severity": "info", "item": f"{line.name} missing in {', '.join(missing)}"})
    for c in checks:
        if c.status == "fail":
            out.append({"severity": "error", "item": f"{c.name}: {'; '.join(c.failures)}"})
        if c.skipped:
            out.append({"severity": "info",
                        "item": f"{c.name} skipped {len(c.skipped)}: {', '.join(c.skipped)}"})
    return out


def _result(name: str, tested: int, failures: list, skipped: list, note: str = "") -> Check:
    status = "fail" if failures else "pass" if tested else "skip"
    return Check(name, status, tested, failures, skipped, note)


def balance(model: Model) -> Check:
    tested, failures, skipped = 0, [], []
    gaps = [g for g in ("balance_gap", "sides_gap") if any(x.name == g for x in model.lines)]
    for column in model.columns:
        for name in gaps:
            gap = model.value(name, column)
            if gap is None:
                skipped.append(f"{name} {column}")
                continue
            tested += 1
            if abs(gap) > TOLERANCE:
                failures.append(f"{name} {column} off by {gap:,.1f}")
    return _result("balance", tested, failures, skipped)


def quarters_sum(model: Model, pack) -> Check:
    tested, failures, skipped = 0, [], []
    for line in model.lines:
        if line.kind != "input" or line.fy != "sum" or not line.source.startswith("q:"):
            continue
        for column in (c for c in model.columns if c.startswith("FY")):
            reported = pack.get(line.source, {}).get(column)
            summed = model.value(line.name, column)
            if reported is None or summed is None:
                skipped.append(f"{line.name} {column}")
                continue
            tested += 1
            if abs(summed - reported.value) > TOLERANCE:
                failures.append(f"{line.name} {column}: quarters {summed:,.1f} vs reported "
                                f"{reported.value:,.1f}")
    return _result("quarters_sum", tested, failures, skipped)


def q4_ties(model: Model, store: Store) -> Check:
    """facts/quarters.py's q4_ties per XBRL flow line the model reads."""
    specs = xbrl_quarters.metrics()
    tested, failures, skipped = 0, [], []
    for line in model.lines:
        if line.kind != "input" or not line.source.startswith("q:"):
            continue
        metric = line.source[2:]
        if specs[metric]["kind"] != "flow":
            continue
        derived = xbrl_quarters.derive(store, model.ticker, metric, specs[metric])
        for result in xbrl_quarters.checks(store, model.ticker, metric, specs[metric], derived):
            if result["check"] != "q4_ties":
                continue
            if result["status"] == "pass":
                tested += 1
            elif result["status"] == "fail":
                tested += 1
                failures.append(f"{metric} {result['period']}: off by {result['gap'] / 1e6:,.1f}")
            else:  # restated: the year was recast, its quarters come from different filings
                skipped.append(f"{metric} {result['period']} (recast)")
    return _result("q4_ties", tested, failures, skipped)


def take_rate(model: Model, pack) -> Check:
    """Every `take_rate*` line against reported revenue / gross bookings read straight from
    the data pack (catches a template wired to the wrong series)."""
    tested, failures, skipped = 0, [], []
    for line in model.lines:
        if not line.name.startswith("take_rate"):
            continue
        suffix = line.name[len("take_rate"):]
        top, bottom = model.line(f"revenue{suffix}"), model.line(f"gross_bookings{suffix}")
        for column in model.quarters:
            rev = pack.get(top.source, {}).get(column)
            gb = pack.get(bottom.source, {}).get(column)
            value = model.value(line.name, column)
            if not rev or not gb or value is None:
                skipped.append(f"{line.name} {column}")
                continue
            tested += 1
            if abs(value - rev.value / gb.value) > 1e-9:
                failures.append(f"{line.name} {column}: {value:.4f} vs {rev.value / gb.value:.4f}")
    return _result("take_rate", tested, failures, skipped)


def segments_sum(model: Model, sums: dict[str, list[str]]) -> Check:
    tested, failures, skipped = 0, [], []
    for total, parts in sums.items():
        slack = max(TOLERANCE, 0.5 * len(parts))  # each part rounded to the million
        for column in model.columns:
            values = [model.value(p, column) for p in parts]
            reported = model.value(total, column)
            if reported is None or None in values:
                skipped.append(f"{total} {column}")
                continue
            tested += 1
            if abs(sum(values) - reported) > slack:
                failures.append(f"{total} {column}: parts {sum(values):,.1f} vs {reported:,.1f}")
    return _result("segments_sum", tested, failures, skipped)


def no_future_input(model: Model) -> Check:
    tested, failures = 0, []
    for (line, column), cell in model.cells.items():
        if cell.kind == "input":
            tested += 1
            if not cell.filed or cell.filed >= model.as_of:
                failures.append(f"{line} {column} filed {cell.filed}")
    return _result("no_future_input", tested, failures, [])


def formula_only(model: Model) -> Check:
    """Columns after the last reported quarter must hold formulas only (none before V4.4)."""
    last = max((c for (_, c), cell in model.cells.items() if cell.kind == "input"
                and not c.startswith("FY")), default=None)
    forecast = [c for c in model.quarters if last and c > last]
    failures = [f"{line} {column}" for (line, column), cell in model.cells.items()
                if column in forecast and cell.kind != "formula"]
    return Check("formula_only", "fail" if failures else "pass", len(forecast), failures,
                 note="" if forecast else "no forecast columns yet (V4.4)")


def to_json(checks: list[Check]) -> list[dict]:
    return [asdict(c) for c in checks]
