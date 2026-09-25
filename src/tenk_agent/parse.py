"""Parse a 10-K HTML document into sections of paragraphs and tables.

A document is flattened into blocks in reading order: paragraphs and data tables.
Item headings ("Item 7. Management's Discussion...") then split the blocks into
sections. Table-of-contents entries live inside tables, so headings are only
recognized in paragraphs outside tables.
"""

import re
import warnings
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, NavigableString, Tag, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

BLOCK_TAGS = {"div", "p", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "table", "center"}
ITEMS = {
    "1",
    "1A",
    "1B",
    "1C",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "7A",
    "8",
    "9",
    "9A",
    "9B",
    "9C",
    "10",
    "11",
    "12",
    "13",
    "14",
    "15",
    "16",
}
HEADING = re.compile(r"^item\s*(\d{1,2})\s*([a-c])?\s*\.\s*(.*)$", re.IGNORECASE)
STATEMENTS_INDEX = re.compile(r"^index to (consolidated )?financial statements", re.IGNORECASE)
AUDITOR_REPORT = re.compile(
    r"^report of independent registered public accounting firm", re.IGNORECASE
)
STATEMENTS_OPINION = re.compile(
    r"^opinions? on the (consolidated )?financial statements", re.IGNORECASE
)
# Page furniture repeated through a filing: running headers, page numbers, footers.
NOISE = re.compile(
    r"""^(
        table\ of\ contents
        | part\s+[iv]+                          # "PART II"
        | item\s+[\d,\ a-c]+                   # running header, e.g. "Item 1B, 1C"
        | \d{1,3}                              # page number
        | .{0,60}form\ 10-k\s*\|?\s*\d{1,3}     # footer, e.g. "Apple Inc. | 2025 Form 10-K | 22"
    )$""",
    re.IGNORECASE | re.VERBOSE,
)


@dataclass
class Block:
    kind: str  # "paragraph" or "table"
    text: str = ""
    rows: list[list[str]] = field(default_factory=list)
    from_table: bool = False  # a paragraph from a multi-row layout table (e.g. a contents list)


@dataclass
class Section:
    item: str
    title: str
    blocks: list[Block]


def parse_filing(html: str) -> list[Section]:
    soup = BeautifulSoup(html, "lxml")
    blocks = list(_blocks(soup.body or soup))
    return _split_sections(blocks)


def _blocks(element: Tag):
    """Yield paragraph and table blocks in reading order."""
    for child in element.children:
        if isinstance(child, NavigableString):
            text = _clean(str(child))
            if text and element.find(BLOCK_TAGS):
                yield Block("paragraph", text)
            continue
        if not isinstance(child, Tag) or _hidden(child):
            continue
        if child.name == "table":
            yield from _table_blocks(child)
        elif child.find(BLOCK_TAGS):
            yield from _blocks(child)
        elif text := _clean(child.get_text(" ")):
            yield Block("paragraph", text)


def _table_blocks(table: Tag):
    """A data table becomes a table block; a layout table (bullets, headings) becomes paragraphs."""
    rows = [cells for tr in table.find_all("tr") if (cells := _row_cells(tr))]
    has_numbers = any(re.search(r"\d", cell) for row in rows for cell in row[1:])
    if len(rows) > 1 and has_numbers:
        yield Block("table", rows=rows)
    else:
        # Some filers put each Item heading in its own one-row table, so only
        # multi-row layout tables are excluded from heading detection.
        for row in rows:
            yield Block("paragraph", " ".join(row), from_table=len(rows) > 2)


def _row_cells(tr: Tag) -> list[str]:
    """Cell texts with EDGAR's split cells merged: '$' | '1,234' and '(3' | ')%' become one cell."""
    cells: list[str] = []
    for td in tr.find_all(["td", "th"]):
        text = _clean_cell(td.get_text(" "))
        if not text:
            continue
        if cells and (re.fullmatch(r"[)%]+|\)?%", text) or cells[-1] in {"$", "(", "$("}):
            cells[-1] += text
        else:
            cells.append(text)
    return cells


def _split_sections(blocks: list[Block]) -> list[Section]:
    """Split blocks at Item headings. Content before the first heading (cover, contents) is dropped.

    If an item heading appears more than once, the occurrence with the most content wins.
    """
    sections: list[Section] = []
    current: Section | None = None
    for block in blocks:
        heading = _heading(block)
        if heading:
            current = Section(*heading, blocks=[])
            sections.append(current)
        elif current is not None and not (block.kind == "paragraph" and NOISE.match(block.text)):
            current.blocks.append(block)

    best: dict[str, Section] = {}
    for section in sections:
        if section.item not in best or len(section.blocks) > len(best[section.item].blocks):
            best[section.item] = section
    sections = [s for s in sections if best[s.item] is s]
    _relocate_financial_statements(sections)
    return sections


def _relocate_financial_statements(sections: list[Section]) -> None:
    """Move financial statements into Item 8 when Item 8 only points to them.

    Some filers (e.g. NVIDIA, Netflix) place the statements under Item 15 or after the
    signatures, and Item 8 incorporates them by reference. Indexing them under Item 8
    keeps "the financial statements" in one place for every filing.
    """
    item8 = next((s for s in sections if s.item == "8"), None)
    if item8 is None or any(b.kind == "table" for b in item8.blocks):
        return
    later = sections[sections.index(item8) + 1 :]
    # Prefer the statements index; otherwise the auditor's report on the statements
    # (Item 9A may hold a separate report on internal control, which must not match).
    for is_start in (_is_statements_index, _is_statements_audit_report):
        for section in later:
            for i in range(len(section.blocks)):
                if is_start(section.blocks, i):
                    item8.blocks.extend(section.blocks[i:])
                    del section.blocks[i:]
                    return


def _is_statements_index(blocks: list[Block], i: int) -> bool:
    return blocks[i].kind == "paragraph" and bool(STATEMENTS_INDEX.match(blocks[i].text))


def _is_statements_audit_report(blocks: list[Block], i: int) -> bool:
    if blocks[i].kind != "paragraph" or not AUDITOR_REPORT.match(blocks[i].text):
        return False
    return any(STATEMENTS_OPINION.match(b.text) for b in blocks[i + 1 : i + 5])


def _heading(block: Block) -> tuple[str, str] | None:
    if block.kind != "paragraph" or block.from_table or len(block.text) > 150:
        return None
    match = HEADING.match(block.text)
    if not match:
        return None
    item = match.group(1) + (match.group(2) or "").upper()
    title = match.group(3).strip()
    # A title must follow ("Item 7. Management's..."); "Item 5.02" is a cross-reference.
    if item not in ITEMS or not title or title[0].isdigit():
        return None
    return item, title


def _hidden(tag: Tag) -> bool:
    return "display:none" in tag.get("style", "").replace(" ", "").lower()


def _clean_cell(text: str) -> str:
    """Like _clean, and also joins negatives split by markup: '( 7,763 )' -> '(7,763)'."""
    text = _clean(text)
    return re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", text))


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
