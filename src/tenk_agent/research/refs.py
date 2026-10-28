"""One reference grammar (V4 design doc §10.1): parse, format and resolve any citable id.

  chunk        UBER-2026Q2-ER-EX99-005 · AAPL-FY2025-8-107       text, from the store
  accession    0001543151-26-000032                              filing metadata (EDGAR cache)
  xbrl         xbrl:UBER:us-gaap:Revenues:<accession>:<end>      value, unit
               (V4: <start>/<end> for durations; V3 ids, end only, still resolve)
  ixbrl        ixbrl:<accession>:<fact id>
  quarter      q:UBER:revenue:2025Q4
  kpi, guide   kpi:UBER:gross_bookings.mobility:2026Q2 · guide:UBER:adj_ebitda:2026Q3
  price, macro px:UBER:2026-08-05 · macro:DGS10:2026-08-05
  model        model:UBER:2026-08-06:v3:adj_ebitda.FY2027 (or scratch-n)
  evidence     E-12 · X-03 (within one report)

The claim check (generation/verify.py) resolves through here. Data kinds (quarter, kpi, guide,
price, macro) resolve to `values`: {value, unit, ticker} in the row's own unit (a guide gives its
low and high). Kinds whose data V4 has not built yet (ixbrl, model, evidence) parse but don't
resolve, so a claim citing one is reported as citing an unknown id.
"""

import re
from dataclasses import dataclass, field
from typing import Protocol

from tenk_agent.store import ChunkRecord, Store

ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
EVIDENCE = re.compile(r"^[EX]-\d+$")
PREFIXED = {  # prefix → (kind, number of ':'-separated parts after it)
    "xbrl": ("xbrl", 5),
    "ixbrl": ("ixbrl", 2),
    "q": ("quarter", 3),
    "kpi": ("kpi", 3),
    "guide": ("guide", 3),
    "px": ("price", 2),
    "macro": ("macro", 2),
    "model": ("model", 4),
}


class FactSource(Protocol):  # agent/sec.py SecData
    def xbrl_fact(self, xbrl_id: str) -> dict | None: ...

    def filing(self, accession: str) -> dict | None: ...


@dataclass(frozen=True)
class Ref:
    kind: str  # chunk, accession, evidence, xbrl, ixbrl, quarter, kpi, guide, price, macro, model
    id: str  # as written
    parts: tuple[str, ...] = ()  # the ':'-separated fields after the prefix


@dataclass
class Resolved:
    ref: Ref
    chunk: ChunkRecord | None = None  # kind chunk
    fact: dict | None = None  # kind xbrl: {value, unit, ticker}
    filing: dict | None = None  # kind accession: {form, filingDate, reportDate, ticker}
    values: list[dict] = field(default_factory=list)  # data kinds: {value, unit, ticker}
    extra: dict = field(default_factory=dict)  # data kinds: the row


def parse(ref_id: str) -> Ref:
    """The kind and fields of an id. Anything without a known prefix is a chunk id."""
    ref_id = ref_id.strip()
    if ACCESSION.match(ref_id):
        return Ref("accession", ref_id)
    if EVIDENCE.match(ref_id):
        return Ref("evidence", ref_id)
    prefix, _, rest = ref_id.partition(":")
    if prefix in PREFIXED and rest:
        kind, count = PREFIXED[prefix]
        parts = tuple(rest.split(":"))
        if len(parts) == count:
            return Ref(kind, ref_id, parts)
        return Ref("malformed", ref_id, parts)
    return Ref("chunk", ref_id)


def kpi_id(ticker: str, metric: str, segment: str, period: str) -> str:
    """kpi:UBER:gross_bookings.mobility:2026Q2; the consolidated figure drops '.total'."""
    return f"kpi:{ticker}:{metric}{'' if segment == 'total' else '.' + segment}:{period}"


def guide_id(ticker: str, metric: str, segment: str, target: str) -> str:
    return f"guide:{ticker}:{metric}{'' if segment == 'total' else '.' + segment}:{target}"


def xbrl_id(ticker: str, taxonomy: str, concept: str, accession: str, end: str,
            start: str | None = None) -> str:
    """A V4 XBRL id: durations name their start, so 3- and 6-month facts don't collide."""
    period = f"{start}/{end}" if start else end
    return f"xbrl:{ticker}:{taxonomy}:{concept}:{accession}:{period}"


class Resolver:
    """Resolves ids against one store (bound to its AsOf) and the SEC cache."""

    def __init__(self, store: Store, sec: FactSource | None = None):
        self.store, self.sec = store, sec

    def resolve(self, ref: Ref | str) -> Resolved | None:
        ref = parse(ref) if isinstance(ref, str) else ref
        if ref.kind == "chunk":
            chunk = self.store.get_chunk(ref.id)
            return Resolved(ref, chunk=chunk) if chunk else None
        if ref.kind == "xbrl":
            fact = self.sec.xbrl_fact(ref.id) if self.sec else None
            return Resolved(ref, fact=fact) if fact else None
        if ref.kind == "accession":
            filing = self.sec.filing(ref.id) if self.sec else None
            return Resolved(ref, filing=filing) if filing else None
        if ref.kind in DATA and self.store.has_facts():
            return getattr(self, f"_{ref.kind}")(ref, *ref.parts)
        return None  # kinds whose data isn't built yet (§15)

    def _quarter(self, ref: Ref, ticker: str, metric: str, period: str) -> Resolved | None:
        from tenk_agent.facts.quarters import derive, metrics

        spec = metrics().get(metric)
        found = spec and next(
            (q for q in derive(self.store, ticker, metric, spec) if q.id == ref.id), None
        )
        if not found:
            return None
        return Resolved(ref, values=[_value(found.value, found.unit, ticker)],
                        extra={"derivation": found.derivation, "inputs": found.inputs})

    def _kpi(self, ref: Ref, ticker: str, key: str, period: str) -> Resolved | None:
        metric, _, segment = key.partition(".")
        rows = [r for r in self.store.kpis(ticker, metric)
                if r["period"] == period and r["segment"] == (segment or "total")]
        if not rows:
            return None
        row = rows[-1]  # the latest visible release reporting the period
        return Resolved(ref, values=[_value(row["value"], row["unit"], ticker)], extra=row)

    def _guide(self, ref: Ref, ticker: str, key: str, target: str) -> Resolved | None:
        metric, _, segment = key.partition(".")
        rows = [r for r in self.store.guidance(ticker, metric)
                if r["target_period"] == target and r["segment"] == (segment or "total")]
        if not rows:
            return None
        row = rows[-1]  # the latest guide for the target: an update replaces the first
        values = [_value(row[k], row["unit"], ticker)
                  for k in ("low", "high") if row[k] is not None]
        return Resolved(ref, values=values, extra=row)

    def _price(self, ref: Ref, ticker: str, day: str) -> Resolved | None:
        return self._series(ref, f"px:{ticker}:close", day, "USD", ticker)

    def _macro(self, ref: Ref, series: str, day: str) -> Resolved | None:
        return self._series(ref, f"macro:{series}", day, "", "")

    def _series(self, ref: Ref, name: str, day: str, unit: str, ticker: str) -> Resolved | None:
        found = [v for d, v in self.store.series(name, day) if d == day]
        return Resolved(ref, values=[_value(found[0], unit, ticker)]) if found else None


DATA = ("quarter", "kpi", "guide", "price", "macro")


def _value(value: float, unit: str, ticker: str) -> dict:
    return {"value": value, "unit": unit, "ticker": ticker}
