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

The claim check (generation/verify.py) resolves through here. Kinds whose data V4 has not built
yet parse but don't resolve, so a claim citing one is reported as citing an unknown id.
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
    extra: dict = field(default_factory=dict)


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
        return None  # kinds whose data isn't built yet (§15)
