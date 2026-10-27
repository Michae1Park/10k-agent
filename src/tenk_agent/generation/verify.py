"""Deterministic checks that a claim's number appears in a chunk it cites, units normalized.

Run over every claim of every answer (answers.finalize):
- a reported number is *verified* when a cited chunk contains it;
- a calculated number is *calculated* when re-running its calculation reproduces it and
  each input is found in a cited chunk;
- a claim without a number is *cited* when its chunk IDs exist;
- anything else is *unverified*, and shown as such rather than hidden.

On a parent-child database a number is also checked against the company: a claim about MSFT
citing only AAPL chunks that show its value is unverified.

V3 (D-036): a claim may also cite XBRL facts by their `xbrl:` id; a value equal to a cited
fact of the claim's company is verified too, and calculation inputs may come from them. The
facts are read from the SEC cache (`sec`), never fetched. A claim about a filing (a date, a
form) may cite its accession number, checked against the cached EDGAR submissions.

V4: ids are parsed and resolved by research/refs.py, one grammar for every source kind.
"""

import re
from dataclasses import dataclass

from tenk_agent.corpus import resolve_company
from tenk_agent.generation.calculator import CalculatorError, calculate
from tenk_agent.research.refs import FactSource, Resolver, parse
from tenk_agent.store import ChunkRecord, Store

SCALES = {
    "usd": 1.0,
    "usd thousands": 1e3,
    "usd millions": 1e6,
    "usd billions": 1e9,
    "count": 1.0,
    "count thousands": 1e3,
    "count millions": 1e6,
    "shares millions": 1e6,
    "usd per share": 1.0,  # per-share amounts are in plain dollars (EPS)
    "usd/share": 1.0,
    "usd/shares": 1.0,
}
WORD_SCALES = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
NUMBER = re.compile(
    r"(?P<open>\()?\$?\s?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?"
    r"(?P<close>\))?\s?(?P<suffix>%|percent\b|thousand\b|million\b|billion\b|trillion\b)?",
    re.IGNORECASE,
)
CHUNK_UNITS = re.compile(r"in (thousands|millions|billions)", re.IGNORECASE)


@dataclass
class Number:
    value: float  # as displayed, sign from parentheses
    decimals: int
    scale: float | None  # from a trailing word ("billion"); None when the context decides
    percent: bool
    span: str


def numbers_in(text: str) -> list[Number]:
    found = []
    for m in NUMBER.finditer(text):
        raw = m.group("num")
        value = float(raw.replace(",", ""))
        if m.group("open") and m.group("close"):
            value = -value
        suffix = (m.group("suffix") or "").lower()
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        found.append(
            Number(
                value=value,
                decimals=decimals,
                scale=WORD_SCALES.get(suffix),
                percent=suffix in ("%", "percent"),
                span=m.group(0).strip(),
            )
        )
    return found


def unit_scale(unit: str | None) -> float | None:
    """Absolute scale of a unit ('USD millions' -> 1e6); None for percentages or unknown."""
    if not unit:
        return None
    return SCALES.get(unit.strip().lower())


def chunk_scales(chunk: ChunkRecord) -> list[float]:
    """Candidate scales for bare numbers in a chunk: its stated units, else every scale."""
    stated = chunk.units or (m.group(0) if (m := CHUNK_UNITS.search(chunk.text)) else None)
    if stated:
        # "in millions, except per-share amounts" is millions; "net income in millions and
        # shares in thousands" is both
        words = re.findall(r"\b(thousand|million|billion|trillion)s?\b", stated.lower())
        return [*dict.fromkeys(WORD_SCALES[w] for w in words), 1.0]
    return [1.0, 1e3, 1e6, 1e9]


def find_value(value, unit: str | None, chunk: ChunkRecord) -> str | None:
    """The span in the chunk showing this value (after unit normalization), or None.

    Two numbers match when they agree to the precision either one is written with:
    "$31.4 billion" matches 31,370 (in millions), but 31,500 doesn't.
    """
    claimed = _as_float(value)
    if claimed is None:
        return None
    is_percent = (unit or "").lower() in ("percent", "%")
    claim_scale = unit_scale(unit)
    if is_percent or claim_scale is None:
        claim_scale = None  # percentages and unknown units compare as displayed
    claim_rounding = _rounding(_decimals(value), claim_scale or 1.0)
    for number in numbers_in(chunk.text):
        if claim_scale is None:
            if _close(
                abs(claimed), abs(number.value), _rounding(number.decimals, 1.0), claim_rounding
            ):
                return number.span
            continue
        if number.percent:
            continue
        scales = [number.scale] if number.scale else chunk_scales(chunk)
        target = abs(claimed) * claim_scale
        for scale in scales:
            shown = abs(number.value) * scale
            if _close(target, shown, _rounding(number.decimals, scale), claim_rounding):
                return number.span
    return None


def _rounding(decimals: int, scale: float) -> float:
    """Half a unit in the last written digit: 1.2 billion -> ±0.05 billion."""
    return 0.5 * 10**-decimals * scale


def _close(target: float, shown: float, shown_rounding: float, claim_rounding: float) -> bool:
    slack = max(shown_rounding, claim_rounding) + 1e-9 * max(abs(target), 1.0)  # float noise
    return abs(target - shown) <= slack


def verify_claim(claim: dict, store: Store, sec: FactSource | None = None) -> dict:
    """Set a claim's status (verified / calculated / cited / unverified) and the reason."""
    ids = claim.get("chunk_ids") or []
    resolver = Resolver(store, sec)
    resolved = {i: resolver.resolve(i) for i in ids}
    groups = {i: _group(i) for i in ids}
    facts = [(i, r.fact if (r := resolved[i]) else None) for i in ids if groups[i] == "xbrl"]
    filings = [
        (i, r.filing if (r := resolved[i]) else None) for i in ids if groups[i] == "accession"
    ]
    cited = [(i, r.chunk if (r := resolved[i]) else None) for i in ids if groups[i] == "chunk"]
    chunks = [c for _, c in cited if c]
    company = resolve_company(str(claim.get("company") or ""))
    found_facts = [(i, f) for i, f in facts if f and (not company or f["ticker"] == company.ticker)]
    found_filings = [i for i, f in filings if f]
    missing = (
        [cid for cid, c in cited if not c]
        + [i for i, f in facts if not f]
        + [i for i, f in filings if not f]
    )
    calculation = claim.get("calculation")
    value = claim.get("value")

    direct = _direct(claim, chunks, found_facts, store) if value is not None else None
    if calculation:
        # Inputs may be any company's cited facts: a comparison's calculation spans companies.
        all_facts = [(i, f) for i, f in facts if f]
        status, reason = _check_calculation(claim, calculation, chunks, all_facts)
        if status == "unverified" and direct and direct[0] == "verified":
            # A value the sources show as is, with a needless calculation (a unit conversion).
            status, reason = direct
    elif value is not None and _as_float(value) is None:
        status, reason = "unverified", f"value {value!r} is not a number"
    elif value is not None:
        status, reason = direct
    elif chunks or found_facts or found_filings:
        status, reason = "cited", ""
    else:
        status, reason = "unverified", "no valid citation"

    if missing:
        reason = (reason + "; " if reason else "") + f"unknown chunk IDs {missing}"
    return claim | {"status": status, "status_reason": reason}


def _group(ref_id: str) -> str:
    """How the claim check counts an id: an XBRL fact (malformed ones too), a filing, or a
    chunk (every other kind, until its data is built)."""
    if ref_id.startswith("xbrl:"):
        return "xbrl"
    return "accession" if parse(ref_id).kind == "accession" else "chunk"


def _direct(claim: dict, chunks: list[ChunkRecord], facts: list, store: Store):
    """(status, reason) for a value read straight from the cited chunks or facts."""
    value, unit = claim.get("value"), claim.get("unit")
    spans = [(c.id, span) for c in chunks if (span := find_value(value, unit, c))]
    conflict = _lineage_conflict(claim, [c for c in chunks if c.id in dict(spans)], store)
    fact = next((i for i, f in facts if fact_matches(value, unit, f)), None)
    if fact:
        return "verified", f"matches XBRL fact {fact}"
    if spans and conflict:
        return "unverified", conflict
    if spans:
        return "verified", f"found '{spans[0][1]}' in {spans[0][0]}"
    return "unverified", "value not found in the cited chunks"


def fact_matches(value, unit: str | None, fact: dict) -> bool:
    """A claimed value equals an XBRL fact, to the precision the claim is written with
    (facts are in whole units: USD, shares)."""
    claimed = _as_float(value)
    scale = unit_scale(unit)
    if claimed is None or scale is None:
        return False
    target = abs(claimed) * scale
    return _close(target, abs(float(fact["value"])), 0.0, _rounding(_decimals(value), scale))


def _lineage_conflict(claim: dict, chunks: list[ChunkRecord], store: Store) -> str | None:
    """Why the chunks showing a claim's value don't support its company, or None. Only on a
    parent-child database."""
    if not chunks or not store.has_children():
        return None
    company = resolve_company(str(claim.get("company") or ""))
    if company and all(c.ticker != company.ticker for c in chunks):
        return f"value found only in {', '.join(sorted({c.ticker for c in chunks}))} filings"
    return None


def _check_calculation(
    claim: dict, calculation: dict, chunks: list[ChunkRecord], facts: list[tuple[str, dict]] = ()
):
    op = calculation.get("op")
    op = None if op == "expression" else op  # the mode's name written as the op (the agent's)
    try:
        result = calculate(op, calculation.get("expression"), calculation.get("inputs"))
    except (CalculatorError, AttributeError, TypeError) as e:  # model-written, may be malformed
        return "unverified", f"calculation does not run: {e}"
    value = claim.get("value")
    if value is not None and _as_float(value) is None:
        return "unverified", f"value {value!r} is not a number"
    # A percentage may come from a ratio: 8.3 percent from numerator / denominator = 0.083.
    percent = (claim.get("unit") or "").lower() in ("percent", "%")
    results = [result.result, result.result * 100] if percent else [result.result]
    # A sum or difference in the inputs' unit, claimed in another (USD millions -> USD).
    scale_in, scale_out = unit_scale(calculation.get("input_unit")), unit_scale(claim.get("unit"))
    if not percent and scale_in and scale_out and scale_in != scale_out:
        results.append(result.result * scale_in / scale_out)
    if value is not None and not any(
        _close(abs(_as_float(value)), abs(r), 0.0, _rounding(_decimals(value), 1.0))
        for r in results
    ):
        return "unverified", f"calculation gives {result.result}, claim says {value}"
    input_unit = calculation.get("input_unit")
    unsourced = [
        name
        for name, number in result.inputs.items()
        if name not in ("years",)
        and not any(find_value(number, input_unit, c) for c in chunks)
        and not any(fact_matches(number, input_unit, f) for _, f in facts)
    ]
    if unsourced:
        return "unverified", f"inputs not found in cited chunks or facts: {unsourced}"
    return "calculated", f"{result.formula} = {result.result}"


def _as_float(value) -> float | None:
    try:
        if isinstance(value, str):
            return float(value.replace(",", "").replace("$", "").replace("%", "").strip())
        return float(value)
    except (TypeError, ValueError):
        return None


def _decimals(value) -> int:
    """Digits after the point, as written (31370.0 from JSON counts as an integer)."""
    if isinstance(value, float) and value.is_integer():
        return 0
    text = str(value).replace(",", "").replace("%", "").strip()
    return len(text.split(".")[1]) if "." in text else 0
