"""KPI and guidance extraction from earnings releases (V4 design doc §4.6), layer 1.

One LLM call per release: the release's chunks (tables first, within a character budget) and
the company's registry (kpis.yaml) in; KPI and guidance rows, each with a verbatim quote, out.
Code then decides what is stored:

  1. the chunk id is one the model was given, and the quote occurs in that chunk verbatim
     (whitespace normalized);
  2. the value (guidance: low and high) is found in the quote, unit-aware (verify.find_value);
  3. the metric and segment are in the registry.

Rows that fail get one repair turn listing what failed; rows that still fail become exceptions
(data gaps), never stored values. The model may be a local vLLM model or Claude (models.py).
"""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from tenk_agent.generation.verify import find_value
from tenk_agent.models import Model, parse_json
from tenk_agent.prompts import PROMPTS
from tenk_agent.store import ChunkRecord, Store

REGISTRY = Path(__file__).with_name("kpis.yaml")
PROMPT = PROMPTS / "extract.md"
MAX_CHARS = 40_000  # ~10k tokens of release text: the 12k input budget (§8) less the registry


def registry() -> dict:
    return yaml.safe_load(REGISTRY.read_text())


@dataclass
class Extraction:
    ticker: str
    period: str  # the reported quarter, 2026Q2
    kpis: list[dict] = field(default_factory=list)
    guidance: list[dict] = field(default_factory=list)
    exceptions: list[dict] = field(default_factory=list)  # rows that failed validation
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


def release_chunks(store: Store, ticker: str, period: str) -> list[ChunkRecord]:
    """The release's chunks, tables first, cut to the character budget."""
    prefix = f"{ticker}-{period}-ER-"
    chunks = [c for c in store.chunks(ticker) if c.id.startswith(prefix)]
    ordered = sorted(chunks, key=lambda c: (c.kind != "table", c.id))
    kept, size = [], 0
    for c in ordered:
        if size + len(c.text) > MAX_CHARS:
            continue
        kept.append(c)
        size += len(c.text)
    return sorted(kept, key=lambda c: c.id)


def _squash(text: str) -> str:
    """Whitespace collapsed; zero-width spaces (in image-based releases' text) dropped."""
    return re.sub(r"\s+", " ", text.replace("\u200b", "")).strip()


def validate(row: dict, kind: str, chunks: dict[str, ChunkRecord], entry: dict) -> str | None:
    """Why a model row can't be stored, or None."""
    chunk = chunks.get(row.get("chunk_id", ""))
    if chunk is None:
        return f"unknown chunk_id {row.get('chunk_id')!r}"
    quote = _squash(str(row.get("quote") or ""))
    if not quote or quote not in _squash(chunk.text):
        return f"quote not found verbatim in {chunk.id}"
    spec = entry.get("kpis" if kind == "kpi" else "guidance", {}).get(row.get("metric"))
    if spec is None:
        return f"metric {row.get('metric')!r} is not in the registry"
    segment = row.get("segment") or "total"
    if kind == "kpi" and segment not in spec.get("segments", ["total"]):
        return f"segment {segment!r} is not one of {spec.get('segments', ['total'])}"
    probe = ChunkRecord(
        chunk.id, chunk.ticker, chunk.fiscal_year, chunk.item, chunk.kind, quote, chunk.units, []
    )
    names = ("value",) if kind == "kpi" else ("low", "high")
    if kind == "guidance" and row.get("high") is None:
        names = ("low",)  # open-ended: "at least 35.5%"
    for name in names:
        if row.get(name) is None or not find_value(row[name], spec["unit"], probe):
            return f"{name} {row.get(name)!r} ({spec['unit']}) is not in the quote"
    if kind == "kpi" and (later := growth_pair(quote)) and _number(row["value"]) == later[0]:
        return f"value {row['value']!r} is the earlier column; the quote shows {later[1]} after it"
    return None


def definition_version(spec: dict, period: str) -> int:
    """The KPI's definition version for a period: the latest `definitions` entry at or before
    it (periods compare as text, 2025Q4 < 2026Q2), else 1."""
    versions = [v for start, v in (spec.get("definitions") or {}).items() if str(start) <= period]
    return max(versions, default=1)


def _number(value) -> float | None:
    try:
        return abs(float(str(value).replace(",", "").replace("$", "")))
    except ValueError:
        return None


def growth_pair(quote: str) -> tuple[float, float] | None:
    """(earlier, later) when a table row shows two values and the growth between them, else
    None: the later one is the reported quarter. Either column order occurs: UBER prints prior
    then current ("| Trips | 3,268 | 3,867 | 18% |"), TRIP and EXPE current then prior
    ("| Viator | $294 | $270 | 9% |"), so the sign of the growth decides. Losses (parenthesized
    values) have no reliable sign convention; for them the order is taken as prior, current."""
    cells = [c.strip() for c in quote.strip("| ").split("|")]
    values = [v for c in cells if (v := _cell_number(c)) is not None]
    growth = [_growth(m.group(1)) for c in cells if (m := re.fullmatch(r"(\(?-?[\d.]+\)?)\s?%", c))]
    for i in range(len(values) - 1):
        (a, loss_a), (b, loss_b) = values[i], values[i + 1]
        if not a or not b:
            continue
        if loss_a or loss_b:
            if any(abs(abs(b / a - 1) * 100 - abs(g)) <= 1.0 for g in growth):
                return a, b
            continue
        if any(abs((b / a - 1) * 100 - g) <= 1.0 for g in growth):
            return a, b
        if any(abs((a / b - 1) * 100 - g) <= 1.0 for g in growth):
            return b, a
    return None


def _growth(cell: str) -> float:
    number = float(cell.strip("()"))
    return -abs(number) if cell.startswith("(") else number


def _cell_number(cell: str) -> tuple[float, bool] | None:
    """(magnitude, is a loss) of a numeric cell."""
    if "%" in cell or not re.fullmatch(r"\(?\$?\s?[\d,]+(\.\d+)?\)?", cell):
        return None
    return float(re.sub(r"[^\d.]", "", cell)), cell.startswith("(")


def _user(ticker: str, period: str, entry: dict, chunks: list[ChunkRecord]) -> str:
    quarter = f"Q{period[-1]} {period[:4]}"
    excerpts = "\n\n".join(f"[{c.id}]\n{c.text}" for c in chunks)
    return (
        f"Company: {ticker}\nReported quarter: {quarter}\n\n"
        f"Registry:\n{yaml.safe_dump(entry, sort_keys=False, width=120)}\n"
        f"Release excerpts:\n\n{excerpts}"
    )


def extract(
    model: Model, store: Store, ticker: str, period: str, max_tokens: int = 6000
) -> Extraction:
    """Extract one release's KPIs and guidance (store: bound to an as-of after the release)."""
    entry = registry()[ticker]
    chunks = release_chunks(store, ticker, period)
    by_id = {c.id: c for c in chunks}
    out = Extraction(ticker, period)
    messages = [{"role": "user", "content": _user(ticker, period, entry, chunks)}]
    system = PROMPT.read_text()
    pending: list[tuple[str, dict, str]] = []
    for attempt in range(2):  # the call, then one repair turn
        response = model.complete(messages, system=system, max_tokens=max_tokens)
        out.calls += 1
        out.input_tokens += response.input_tokens
        out.output_tokens += response.output_tokens
        try:
            reply = parse_json(response.text)
        except (ValueError, json.JSONDecodeError) as e:
            reply, pending = {}, [("reply", {}, f"no JSON object: {e}")]
        else:
            pending = []
            for kind, key in (("kpi", "kpis"), ("guidance", "guidance")):
                for row in reply.get(key) or []:
                    if not isinstance(row, dict):
                        continue
                    if (why := validate(row, kind, by_id, entry)) is not None:
                        pending.append((kind, row, why))
                    elif kind == "kpi":
                        clean = _clean(row, kind)
                        spec = entry["kpis"][clean["metric"]]
                        clean["definition_version"] = definition_version(spec, period)
                        out.kpis.append(clean)
                    else:
                        out.guidance.append(_clean(row, kind))
        if not pending or attempt == 1:
            break
        problems = "\n".join(f"- {kind} {json.dumps(row)}: {why}" for kind, row, why in pending)
        messages += [
            {"role": "assistant", "content": response.text},
            {
                "role": "user",
                "content": "These rows failed the checks; return only corrected "
                f"versions of them (same JSON shape), or leave a row out. A quote must be one "
                f"unbroken piece of the chunk, without '...':\n{problems}",
            },
        ]
    out.exceptions = [{"kind": k, "row": r, "reason": why} for k, r, why in pending]
    out.kpis = _dedupe(out.kpis, ("metric", "segment"))
    out.guidance = _dedupe(out.guidance, ("metric", "segment", "target_period"))
    return out


def _clean(row: dict, kind: str) -> dict:
    keep = (
        ("metric", "segment", "value", "unit", "chunk_id", "quote")
        if kind == "kpi"
        else (
            "metric",
            "segment",
            "target_period",
            "low",
            "high",
            "unit",
            "basis",
            "chunk_id",
            "quote",
        )
    )
    clean = {k: row.get(k) for k in keep}
    clean["segment"] = clean.get("segment") or "total"
    return clean


def _dedupe(rows: list[dict], key: tuple[str, ...]) -> list[dict]:
    """The first row per key (a repair may repeat a row that already passed)."""
    seen: dict[tuple, dict] = {}
    for row in rows:
        seen.setdefault(tuple(row.get(k) for k in key), row)
    return list(seen.values())


def to_json(extraction: Extraction) -> dict:
    return asdict(extraction)
