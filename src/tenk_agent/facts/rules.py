"""Rules-based KPI extraction (design doc §13.2 ablation: `extract.method: rules`).

A label-pattern table-row matcher over the registry, no LLM: for each KPI label (kpis.yaml), the
first release table row whose first cell starts with it; the reported quarter's column is the
later of a growth pair (extract.growth_pair), else the row's first number. A KPI whose labels
map one to one onto its segments (TRIP's revenue) gets a row per segment; otherwise only the
consolidated value ("total"). Guidance is prose and is not attempted. Rows then pass the same
validation as the LLM's (extract.validate), so they are compared on equal terms.
"""

import re

from tenk_agent.facts.extract import (
    Extraction,
    _cell_number,
    definition_version,
    growth_pair,
    registry,
    release_chunks,
    validate,
)
from tenk_agent.store import Store


def extract_rules(store: Store, ticker: str, period: str) -> Extraction:
    entry = registry()[ticker]
    chunks = release_chunks(store, ticker, period, entry)
    by_id = {c.id: c for c in chunks}
    out = Extraction(ticker, period)
    for metric, spec in entry.get("kpis", {}).items():
        labels = spec.get("labels", [])
        segments = spec.get("segments", ["total"])
        pairs = (list(zip(labels, segments, strict=True)) if len(labels) == len(segments) > 1
                 else [(label, "total") for label in labels])
        found = set()
        for label, segment in pairs:
            if segment in found:
                continue
            row = _row(chunks, label)
            if row is None:
                continue
            chunk_id, line = row
            value = _value(line)
            if value is None:
                continue
            candidate = {"metric": metric, "segment": segment, "value": value,
                         "unit": spec["unit"], "chunk_id": chunk_id, "quote": line}
            if (why := validate(candidate, "kpi", by_id, entry)) is not None:
                out.exceptions.append({"kind": "kpi", "row": candidate, "reason": why})
                continue
            candidate["definition_version"] = definition_version(spec, period)
            out.kpis.append(candidate)
            found.add(segment)
    return out


def _row(chunks, label: str) -> tuple[str, str] | None:
    pattern = re.compile(r"^\|\s*" + re.escape(label) + r"\b[^|]*\|", re.IGNORECASE)
    for c in chunks:
        for line in c.text.splitlines():
            if pattern.match(line) and any(_cell_number(x.strip()) for x in line.split("|")):
                return c.id, line.strip()
    return None


def _value(line: str) -> float | None:
    pair = growth_pair(line)
    if pair:
        return pair[1]
    for cell in line.strip("| ").split("|")[1:]:
        number = _cell_number(cell.strip())
        if number:
            return -number[0] if number[1] else number[0]
    return None
