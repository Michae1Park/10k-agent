"""Deterministic checks that a number or quote appears in a chunk, with unit normalization.

Used by the verify_citation tool and by the V4 post-pass over every answer:
- a reported number is *verified* when a cited chunk contains it;
- a calculated number is *calculated* when re-running its calculation reproduces it and
  each input is found in a cited chunk;
- anything else is *unverified*, and shown as such rather than hidden.
"""

import re
from dataclasses import dataclass

from tenk_agent.calculator import CalculatorError, calculate
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
        word = stated.lower().split()[-1].rstrip("s")
        return [WORD_SCALES.get(word, 1.0), 1.0]
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


def find_text(quote: str, chunk: ChunkRecord) -> bool:
    def normalize(text: str) -> str:
        return re.sub(r"[\s|$]+", " ", text).strip().lower()

    return normalize(quote) in normalize(chunk.text)


def verify_claim(claim: dict, store: Store) -> dict:
    """Set a claim's status (verified / calculated / cited / unverified) and the reason."""
    chunks = [c for cid in claim.get("chunk_ids") or [] if (c := store.get_chunk(cid))]
    missing = [cid for cid in claim.get("chunk_ids") or [] if not store.get_chunk(cid)]
    calculation = claim.get("calculation")
    value = claim.get("value")

    if calculation:
        status, reason = _check_calculation(claim, calculation, chunks)
    elif value is not None and _as_float(value) is None:
        status, reason = "unverified", f"value {value!r} is not a number"
    elif value is not None:
        spans = [(c.id, span) for c in chunks if (span := find_value(value, claim.get("unit"), c))]
        if spans:
            status, reason = "verified", f"found '{spans[0][1]}' in {spans[0][0]}"
        else:
            status, reason = "unverified", "value not found in the cited chunks"
    elif chunks:
        status, reason = "cited", ""
    else:
        status, reason = "unverified", "no valid citation"

    if missing:
        reason = (reason + "; " if reason else "") + f"unknown chunk IDs {missing}"
    return claim | {"status": status, "status_reason": reason}


def _check_calculation(claim: dict, calculation: dict, chunks: list[ChunkRecord]):
    try:
        result = calculate(
            calculation.get("op"), calculation.get("expression"), calculation.get("inputs")
        )
    except (CalculatorError, AttributeError, TypeError) as e:  # model-written, may be malformed
        return "unverified", f"calculation does not run: {e}"
    value = claim.get("value")
    if value is not None and _as_float(value) is None:
        return "unverified", f"value {value!r} is not a number"
    if value is not None and not _close(
        abs(_as_float(value)), abs(result.result), 0.0, _rounding(_decimals(value), 1.0)
    ):
        return "unverified", f"calculation gives {result.result}, claim says {value}"
    input_unit = calculation.get("input_unit")
    unsourced = [
        name
        for name, number in result.inputs.items()
        if name not in ("years",) and not any(find_value(number, input_unit, c) for c in chunks)
    ]
    if unsourced:
        return "unverified", f"inputs not found in cited chunks: {unsourced}"
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
