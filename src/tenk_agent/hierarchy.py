"""V7 parent-child chunks: small children are searched, their larger parents are read.

- **Parents** are what the model, the citations and verification see (the `chunks` table):
  each table whole, and prose packed into subsections of up to `parent_chars` that break at
  headings ("Note 4 — Leases") and never cross an Item.
- **Children** are what retrieval matches (the `children` table): ~200-token sentence windows
  of prose, one record per table row (group, row label, every column with its fiscal year,
  see tables.py), and one "table title" child with the caption and header rows.

Continuation tables (Apple's FY2024 segment block) and tables whose "(in millions)" sits at the
end of a long paragraph inherit the units the naive chunker misses.
"""

import json
import re
from dataclasses import dataclass

from tenk_agent.chunking import YEAR, Chunk, _caption, _split_long, _table_texts, _units
from tenk_agent.parse import Block, Section
from tenk_agent.tables import header_rows, table_rows

UNITS_TAIL = re.compile(r"\(?(?:dollars |amounts )?in (millions|thousands|billions)", re.I)
HEADING = re.compile(r"^(?:note \d+|[A-Z][^.!?]{2,100})$", re.IGNORECASE)


@dataclass(frozen=True)
class HierarchySizes:
    """config.yaml `ingest:` (parent_child layout)."""

    parent_chars: int = 6000
    child_chars: int = 800
    table_chars: int = 7200


DEFAULT_HIERARCHY = HierarchySizes()


@dataclass
class Child:
    id: str
    parent_id: str
    kind: str  # "prose", "row" or "table_title"
    text: str
    row: dict | None = None  # rows: {"group", "label", "cells": [[column, fiscal_year, value]]}

    def row_json(self) -> str | None:
        return json.dumps(self.row) if self.row else None


def build(
    prefix: str, sections: list[Section], sizes: HierarchySizes = DEFAULT_HIERARCHY
) -> tuple[list[Chunk], list[Child]]:
    """Parents (as Chunks, ids like the naive ones) and their children for one filing."""
    parents: list[Chunk] = []
    children: list[Child] = []
    for section in sections:
        for seq, (kind, text, block, units) in enumerate(_parents(section, sizes)):
            parent = Chunk(f"{prefix}-{section.item}-{seq:03d}", section.item, seq, kind, text)
            if kind == "table":
                top = "\n".join(text.splitlines()[:6])
                parent.units = units or _units(top)
                parent.years_covered = sorted({int(y) for y in YEAR.findall(top)})
                children += _table_children(parent, block)
            else:
                children += _prose_children(parent, sizes.child_chars)
            parents.append(parent)
    return parents, children


def _parents(section: Section, sizes: HierarchySizes):
    """Yield (kind, text, table block or None, inherited units) in reading order."""
    prose: list[str] = []
    previous: Block | None = None
    previous_units: str | None = None
    for i, block in enumerate(section.blocks):
        if block.kind == "paragraph":
            prose.extend(_split_long(block.text, sizes.parent_chars))
            previous = block
            continue
        for text in _pack(prose, sizes.parent_chars):
            yield "prose", text, None, None
        prose = []
        caption = _caption(section.blocks[:i])
        units = _units(caption)
        if units is None and previous is not None:
            if previous.kind == "table":
                units = previous_units  # a continuation: the block right after another table
            elif match := UNITS_TAIL.search(previous.text[-300:]):
                units = f"USD {match.group(1).lower()}"
        texts = _table_texts(block, caption, sizes.table_chars)
        units = units or _units("\n".join(texts[0].splitlines()[:6]))
        for text in texts:
            yield "table", text, block, units
        previous, previous_units = block, units
    for text in _pack(prose, sizes.parent_chars):
        yield "prose", text, None, None


def _pack(paragraphs: list[str], max_chars: int):
    """Paragraphs into parents of at most max_chars; a heading starts a new parent once the
    current one is a third full, so a subsection stays together."""
    current: list[str] = []
    for paragraph in paragraphs:
        size = len("\n".join(current + [paragraph]))
        heading = _is_heading(paragraph) and len("\n".join(current)) >= max_chars // 3
        if current and (size > max_chars or heading):
            yield "\n".join(current)
            current = []
        current.append(paragraph)
    if current:
        yield "\n".join(current)


def _is_heading(paragraph: str) -> bool:
    return (
        len(paragraph) <= 100 and bool(HEADING.match(paragraph)) and not paragraph.startswith("•")
    )


def _prose_children(parent: Chunk, max_chars: int) -> list[Child]:
    """Sentence windows of about max_chars; each starts with the previous window's last sentence."""
    sentences = [
        s for line in parent.text.splitlines() for s in re.split(r"(?<=[.;])\s+", line) if s
    ]
    windows: list[list[str]] = []
    current: list[str] = []
    for sentence in sentences:
        if current and len(" ".join(current + [sentence])) > max_chars:
            windows.append(current)
            current = [current[-1]] if len(current[-1]) < max_chars // 2 else []
        current.append(sentence)
    if current:
        windows.append(current)
    return [
        Child(f"{parent.id}.{n:03d}", parent.id, "prose", " ".join(window))
        for n, window in enumerate(windows)
    ]


def _table_children(parent: Chunk, block: Block) -> list[Child]:
    lines = parent.text.splitlines()
    caption = "\n".join(line for line in lines if not line.startswith("|"))
    title = (caption.splitlines() or [""])[0][:200]
    units = f" ({parent.units})" if parent.units else ""
    head = [" | ".join(row) for row in header_rows(block.rows)]
    children = [
        Child(f"{parent.id}.000", parent.id, "table_title", "\n".join([caption, *head]).strip())
    ]
    for n, row in enumerate(table_rows(block.rows, caption), 1):
        text = f"{title}{units}\n{row.text()}".strip()
        data = {
            "group": row.group,
            "label": row.label,
            "cells": [[col.label, col.fiscal_year, value] for col, value in row.cells],
        }
        children.append(Child(f"{parent.id}.{n:03d}", parent.id, "row", text, data))
    return children
