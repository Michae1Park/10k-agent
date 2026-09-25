"""Turn parsed sections into retrievable chunks.

- Chunks never cross section boundaries, so every chunk cites exactly one Item.
- Each data table is its own chunk, with its caption and unit line, and is never
  split mid-row. Oversized tables are split into row groups that repeat the header rows.
- Prose is packed into ~600-token chunks (approximated as 4 characters per token), and
  each chunk starts with the previous chunk's last paragraph when that paragraph is short.
"""

import re
from dataclasses import dataclass, field

from tenk_agent.parse import Block, Section

MAX_CHARS = 2400  # ~600 tokens
MAX_TABLE_CHARS = 3 * MAX_CHARS
MAX_OVERLAP_CHARS = MAX_CHARS // 4
HEADER_ROWS = 2
UNITS = re.compile(r"in (millions|thousands|billions)", re.IGNORECASE)
YEAR = re.compile(r"\b(20[0-3]\d)\b")


@dataclass
class Chunk:
    id: str
    item: str
    seq: int
    kind: str  # "prose" or "table"
    text: str
    units: str | None = None
    years_covered: list[int] = field(default_factory=list)


def chunk_sections(prefix: str, sections: list[Section]) -> list[Chunk]:
    """Chunk every section. IDs look like '<prefix>-<item>-<seq>', e.g. 'AAPL-FY2025-7-012'."""
    chunks = []
    for section in sections:
        for seq, (kind, text) in enumerate(_section_chunks(section)):
            chunk = Chunk(f"{prefix}-{section.item}-{seq:03d}", section.item, seq, kind, text)
            if kind == "table":
                # Units and years live in the caption and header rows at the top.
                top = "\n".join(text.splitlines()[:6])
                chunk.units = _units(top)
                chunk.years_covered = sorted({int(y) for y in YEAR.findall(top)})
            chunks.append(chunk)
    return chunks


def _section_chunks(section: Section):
    """Yield (kind, text) in reading order."""
    prose: list[str] = []
    for i, block in enumerate(section.blocks):
        if block.kind == "paragraph":
            prose.extend(_split_long(block.text))
            continue
        yield from _flush(prose)
        prose = []
        caption = _caption(section.blocks[:i])
        for text in _table_texts(block, caption):
            yield "table", text
    yield from _flush(prose)


def _flush(paragraphs: list[str]):
    """Pack paragraphs into prose chunks of at most MAX_CHARS, with a one-paragraph overlap."""
    current: list[str] = []
    for paragraph in paragraphs:
        if current and len("\n".join(current + [paragraph])) > MAX_CHARS:
            yield "prose", "\n".join(current)
            last = current[-1]
            current = [last] if len(last) <= MAX_OVERLAP_CHARS else []
        current.append(paragraph)
    if current:
        yield "prose", "\n".join(current)


def _table_texts(table: Block, caption: str) -> list[str]:
    rows = ["| " + " | ".join(row) + " |" for row in table.rows]
    head = ([caption] if caption else []) + rows[:HEADER_ROWS]
    body = rows[HEADER_ROWS:]
    if len("\n".join(head + body)) <= MAX_TABLE_CHARS:
        return ["\n".join(head + body)]
    texts, group = [], []
    for row in body:
        if group and len("\n".join(head + group + [row])) > MAX_TABLE_CHARS:
            texts.append("\n".join(head + group))
            group = []
        group.append(row)
    return texts + (["\n".join(head + group)] if group else [])


def _caption(preceding: list[Block]) -> str:
    """Up to two short paragraphs right before a table, e.g. a title and '(In millions)'."""
    lines = []
    for block in reversed(preceding[-2:]):
        if block.kind != "paragraph" or len(block.text) > 300:
            break
        lines.insert(0, block.text)
    return "\n".join(lines)


def _split_long(text: str) -> list[str]:
    """Split a paragraph longer than MAX_CHARS at sentence boundaries."""
    if len(text) <= MAX_CHARS:
        return [text]
    parts, current = [], ""
    for sentence in re.split(r"(?<=[.;])\s+", text):
        if current and len(current) + len(sentence) + 1 > MAX_CHARS:
            parts.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
    return parts + ([current] if current else [])


def _units(text: str) -> str | None:
    match = UNITS.search(text)
    return f"USD {match.group(1).lower()}" if match else None
