"""S1 data pack (V4 design doc §9): a company's reported history as of a date, with sources.

Each series is keyed as the model's templates name a source: `q:<metric>` for a core line from
XBRL (facts/quarters.py; quarters and fiscal years) and `kpi:<metric>[.<segment>]` for a release
KPI (facts/extract.py). Every point carries its citable id (research/refs.py) and filing date,
so the model's inputs keep their provenance and the no-future-input check can read it.
Money is in USD millions and share counts in millions, as the releases print them.

Stock splits: filings after a split restate earlier quarters, ones before don't, so a share or
per-share series can mix bases (BKNG's 25-for-1 of April 2026). Every point is put on the
basis of the latest filing visible at the as-of, scaled by the tagged split ratio only where
that brings it in line; such points say `split_adjusted`.
"""

from dataclasses import asdict, dataclass

from tenk_agent.facts.quarters import derive, metrics
from tenk_agent.research.refs import kpi_id
from tenk_agent.store import Store

SPLIT = "StockholdersEquityNoteStockSplitConversionRatio1"
CLASS_AXIS = "us-gaap:StatementClassOfStockAxis"
SCALE = {"USD": 1e6, "shares": 1e6}  # XBRL whole units → the model's millions
UNITS = {"USD": "USD millions", "shares": "count millions", "USD/shares": "USD per share"}


@dataclass
class Point:
    value: float
    unit: str
    id: str  # q:…, kpi:…
    filed: str
    derivation: str = "reported"  # facts/quarters.py: direct, ytd_diff, fy_minus_9m, instant


def build(store: Store, ticker: str, sources: set[str]) -> dict[str, dict[str, Point]]:
    """{source: {period: Point}} for the sources a model reads; periods are 2026Q2 or FY2025."""
    pack: dict[str, dict[str, Point]] = {}
    specs = metrics()
    kpis = {}
    for row in store.kpis(ticker):  # later releases' copies of a period replace earlier ones
        kpis[(row["metric"], row["segment"], row["period"])] = row
    for source in sorted(sources):
        kind, _, key = source.partition(":")
        series: dict[str, Point] = {}
        if kind == "q":
            for q in derive(store, ticker, key, specs[key]):
                period = f"FY{q.fiscal_year}" if q.period == "FY" else f"{q.fiscal_year}{q.period}"
                series[period] = Point(q.value / SCALE.get(q.unit, 1), UNITS.get(q.unit, q.unit),
                                       q.id, q.filed, q.derivation)
            if specs[key].get("class_of_stock"):
                _by_class(store, ticker, specs[key]["concepts"], series)
            if specs[key]["kind"] in ("per_share", "shares"):
                _split_basis(series, _splits(store, ticker), specs[key]["kind"])
        elif kind == "kpi":
            metric, _, segment = key.partition(".")
            for (m, seg, period), row in kpis.items():
                if m == metric and seg == (segment or "total"):
                    series[period] = Point(row["value"], row["unit"],
                                           kpi_id(ticker, m, seg, period), row["filed"])
            if any(p.unit == "USD per share" for p in series.values()):
                _split_basis(series, _splits(store, ticker), "per_share")
        else:
            raise ValueError(f"unknown source {source!r}: q:<metric> or kpi:<metric>[.segment]")
        pack[source] = series
    return pack


def _by_class(store: Store, ticker: str, concepts: list[str], series: dict) -> None:
    """Balance dates the series lacks, from inline XBRL facts tagged per class of stock only
    (CART's redeemable preferred): each class's latest visible value, summed."""
    latest: dict[tuple, dict] = {}
    for f in store.facts(ticker, concepts, any_dims=True):
        if f["start"] is None and set(f["dims"]) == {CLASS_AXIS}:
            key = (f["end"], f["dims"][CLASS_AXIS])
            if key not in latest or f["filed"] > latest[key]["filed"]:
                latest[key] = f
    by_end: dict[str, list[dict]] = {}
    for (end, _), f in latest.items():
        by_end.setdefault(end, []).append(f)
    for end, facts in by_end.items():
        month = int(end[5:7])
        if month not in (3, 6, 9, 12) or int(end[8:]) < 28:
            continue
        period = f"{end[:4]}Q{month // 3}"
        if period not in series:
            series[period] = Point(sum(f["value"] for f in facts) / 1e6, "USD millions",
                                   facts[0]["id"], max(f["filed"] for f in facts),
                                   f"sum of {len(facts)} class(es) of stock")


def _splits(store: Store, ticker: str) -> list[float]:
    """The split ratios the company has tagged, as visible at the store's as-of."""
    facts = store.facts(ticker, [SPLIT], any_dims=True)
    return sorted({f["value"] for f in facts if f["value"] > 1})


def _split_basis(series: dict[str, Point], ratios: list[float], kind: str) -> None:
    """Rescale points on a pre-split basis to the latest filing's, in place."""
    if not ratios or len(series) < 2:
        return
    latest = max(p.filed for p in series.values())
    basis = sorted(p.value for p in series.values() if p.filed == latest)
    reference = basis[len(basis) // 2]
    for period, point in series.items():
        for ratio in ratios:
            scaled = point.value * ratio if kind == "shares" else point.value / ratio
            if reference and abs(scaled / reference - 1) < abs(point.value / reference - 1) / 4:
                series[period] = Point(scaled, point.unit, point.id, point.filed,
                                       f"{point.derivation}, split_adjusted x{ratio:g}")
                break


def to_json(pack: dict[str, dict[str, Point]]) -> dict:
    return {source: {p: asdict(point) for p, point in series.items()}
            for source, series in pack.items()}
