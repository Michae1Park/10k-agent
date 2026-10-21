"""Preprocessing step 1, deterministic: 10-K HTML → blocks (paragraphs and tables) with features.

Only rules that hold for every 10-K; anything that needs judgment is left to the structure agent
(structure.py). Where each rule lives:

  D1  drop hidden elements               _walk (_hidden)
  D2  inline-XBRL text-block spans       _continuation_names, _xbrl_context
  D3  filing metadata from dei: tags     cover_metadata
  D4  clean text                         _clean
  D5  split into paragraphs and tables   _walk
  D6  rejoin split table cells           _row_cells
  D7  features for the LLM               _emphasis, _is_link, Block.features
  D8  page of each block                 read_blocks

Spec: experiments/V0_PARSING_CHUNKING.md §2.1.
"""

import json
import re
from dataclasses import dataclass, field
from itertools import groupby
from pathlib import Path

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

ITEM_LABEL = re.compile(r"^items?\s+\d{1,2}[a-c]?\b", re.I)  # "Item 1A", "Items 10 and 11"
TEXT_BLOCK_TAGS = {"ix:nonnumeric", "ix:continuation"}  # tags that can carry an XBRL text block
BLOCK_TAGS = {"div", "p", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "table", "center"}
INTERNAL_LINK = re.compile("^#")  # href to a place in the same document
COVER_TAGS = [  # D3
    "TradingSymbol",
    "DocumentFiscalYearFocus",
    "DocumentPeriodEndDate",
    "EntityRegistrantName",
    "DocumentType",
]


@dataclass
class Block:
    """One paragraph or table. Field names match the D7 feature names in the spec."""

    id: str = ""  # b0001… in reading order, set by read_blocks
    kind: str = "paragraph"  # "paragraph" or "table"
    text: str = ""  # tables: one "a | b" line per row
    rows: list[list[str]] = field(default_factory=list)  # tables: cells per row
    emph: str = "-"  # emphasis of the whole text: B, I, BI, "-" (none), or "runin"
    runin: int = 0  # when emph == "runin": length of the emphasized lead
    xbrl: str | None = None  # innermost XBRL text block the block sits in
    xbrl_start: str | None = None  # "note" / "policy" / "table": an XBRL text block starts here
    pb: bool = False  # a page break follows
    link: bool = False  # the text is a link within the document (e.g. a TOC entry)
    page: int = 0  # D8: 1 + page breaks before the block; metadata, not shown to the LLM

    def features(self) -> str:
        """The `features` field of the block's line in the LLM input (system.md # Input)."""
        parts = [
            f"emph=runin:{self.runin}" if self.emph == "runin" else f"emph={self.emph}",
            f"chars={len(self.text)}",
        ]
        if ITEM_LABEL.match(self.text):
            parts.append("item_label")
        if self.xbrl_start:
            parts.append(f"xbrl={self.xbrl_start}")
        if self.pb:
            parts.append("pb")
        if self.link:
            parts.append("link")
        return " ".join(parts)


def read_blocks(html: str) -> list[Block]:
    """D1–D8: the filing's blocks in reading order, with ids, features and pages."""
    soup = BeautifulSoup(html, "lxml")
    blocks: list[Block] = []
    _walk(soup.body or soup, blocks, _continuation_names(soup))
    page = 1
    for i, b in enumerate(blocks, start=1):
        b.id = f"b{i:04d}"
        b.page = page
        page += b.pb
    return blocks


def cover_metadata(full_filing: Path) -> dict:
    """D3: filing metadata from the dei: cover tags, plus accession and URL from metadata.json."""
    html = full_filing.read_text(encoding="utf-8")
    tags: dict = {}
    for name in COVER_TAGS:
        m = re.search(rf'name="dei:{name}"[^>]*>(.*?)</ix:', html, re.S)
        tags[name] = _clean(BeautifulSoup(m.group(1), "lxml").get_text(" ")) if m else None
    assert tags["DocumentType"] == "10-K", tags["DocumentType"]
    manifest = json.loads((full_filing.parent / "metadata.json").read_text())
    return {
        "ticker": tags["TradingSymbol"],
        "registrant": tags["EntityRegistrantName"],
        "fiscal_year": int(tags["DocumentFiscalYearFocus"]),
        "period_end": manifest.get("period_end_date"),
        "accession": manifest.get("accession_no"),
        "source_url": manifest.get("source_url"),
    }


# ---------- D5: blocks ----------


def _walk(element: Tag, blocks: list[Block], names: dict[str, str]) -> None:
    """D5: append the element's paragraphs and tables to `blocks`. A tag with no block-level
    descendants is one paragraph; a container is walked into. D1: hidden tags are skipped."""
    for child in element.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            # Loose text beside block tags is its own paragraph.
            text = _clean(str(child))
            if text and element.find(BLOCK_TAGS):
                blocks.append(Block(text=text))
            continue
        if not isinstance(child, Tag) or _hidden(child):
            continue
        style = (child.get("style") or "").replace(" ", "").lower()
        if "page-break-before:always" in style and blocks:
            blocks[-1].pb = True
        if child.name == "table":
            if table := _table_block(child, names):
                blocks.append(table)
        elif child.find(BLOCK_TAGS):
            _walk(child, blocks, names)
        elif text := _clean(child.get_text(" ")):
            blocks.append(_paragraph_block(child, text, names))
        if "page-break-after:always" in style and blocks:
            blocks[-1].pb = True


def _table_block(tag: Tag, names: dict[str, str]) -> Block | None:
    """Every <table> is one block (empty rows dropped); data vs layout is the LLM's call."""
    rows = [cells for tr in tag.find_all("tr") if (cells := _row_cells(tr))]
    if not rows:
        return None
    xbrl, xbrl_start = _xbrl_context(tag, names)
    return Block(
        kind="table",
        text="\n".join(" | ".join(r) for r in rows),
        rows=rows,
        xbrl=xbrl,
        xbrl_start=xbrl_start,
        link=bool(tag.find("a", href=INTERNAL_LINK)),
    )


def _paragraph_block(tag: Tag, text: str, names: dict[str, str]) -> Block:
    emph, runin = _emphasis(tag, text)
    xbrl, xbrl_start = _xbrl_context(tag, names)
    return Block(
        text=text,
        emph=emph,
        runin=runin,
        xbrl=xbrl,
        xbrl_start=xbrl_start,
        link=_is_link(tag, text),
    )


# ---------- D7: features ----------


def _hidden(tag: Tag) -> bool:
    """D1: an element styled display:none."""
    return "display:none" in tag.get("style", "").replace(" ", "").lower()


def _clean(text: str) -> str:
    """D4: non-breaking spaces and runs of whitespace become one space."""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _row_cells(tr: Tag) -> list[str]:
    """D6: cell texts with EDGAR's split cells merged: '$' | '1,234' and '(3' | ')%' become one
    cell; negatives split by markup are joined: '( 7,763 )' -> '(7,763)'."""
    cells: list[str] = []
    for td in tr.find_all(["td", "th"]):
        text = _clean(td.get_text(" "))
        text = re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", text))
        if not text:
            continue
        if cells and (re.fullmatch(r"[)%]+|\)?%", text) or cells[-1] in {"$", "(", "$("}):
            cells[-1] += text
        else:
            cells.append(text)
    return cells


def _is_link(tag: Tag, text: str) -> bool:
    """An internal link covers at least half the paragraph's text."""
    link = tag.find("a", href=INTERNAL_LINK)
    return bool(link) and len(_clean(link.get_text(" "))) >= len(text) / 2


def _emphasis(tag: Tag, text: str) -> tuple[str, int]:
    """(emph, runin): B / I / BI when all the text shares it; ("runin", N) when only a leading
    run of N chars is emphasized (a run-in heading); ("-", 0) otherwise."""
    strings = [s for s in tag.find_all(string=True) if s.strip()]
    # Runs of consecutive strings with the same emphasis.
    runs = [
        (emph, "".join(map(str, group)))
        for emph, group in groupby(strings, key=lambda s: _emphasis_of(s, tag))
    ]
    styles = {emph for emph, run in runs if re.search(r"\w", run)}  # ignore punctuation-only runs
    if len(styles) == 1 and "-" not in styles:
        return styles.pop(), 0
    if len(runs) > 1 and runs[0][0] != "-":
        lead = _clean(runs[0][1])
        if lead and text.startswith(lead) and any(emph == "-" for emph, _ in runs[1:]):
            return "runin", len(lead)
    return "-", 0


def _emphasis_of(node: NavigableString, block: Tag) -> str:
    """B, I, BI or "-" for one string, from its tags and styles up to the block's tag."""
    bold = italic = False
    for parent in node.parents:
        if parent is block.parent:
            break
        style = (parent.get("style") or "").replace(" ", "").lower()
        bold |= parent.name in ("b", "strong") or bool(
            re.search(r"font-weight:(bold|[6-9]00)", style)
        )
        italic |= parent.name in ("i", "em") or "font-style:italic" in style
    return ("B" if bold else "") + ("I" if italic else "") or "-"


# ---------- D2: inline-XBRL text blocks ----------


def _continuation_names(soup: BeautifulSoup) -> dict[str, str]:
    """ix:continuation id → the TextBlock element it continues, following continuedAt chains."""
    names: dict[str, str] = {}
    for tag in soup.find_all("ix:nonnumeric", attrs={"name": re.compile("TextBlock$")}):
        next_id = tag.get("continuedat")
        while next_id and next_id not in names:
            names[next_id] = tag["name"]
            continuation = soup.find("ix:continuation", attrs={"id": next_id})
            next_id = continuation.get("continuedat") if continuation else None
    return names


def _xbrl_context(tag: Tag, names: dict[str, str]) -> tuple[str | None, str | None]:
    """(innermost text block the block sits in, kind of a text block that starts in it).

    A text block can wrap the block (an ancestor) or start inside it, e.g. mid-heading."""
    enclosing = [(t, name) for t in [tag, *tag.parents] if (name := _text_block_name(t, names))]
    if enclosing:
        innermost = enclosing[0][1]
        outer_tag, outer_name = enclosing[-1]
        # The outermost one starts here if it isn't a continuation and its text starts here.
        is_start_tag = outer_tag.name == "ix:nonnumeric"
        starts_here = is_start_tag and _first_text(outer_tag) is _first_text(tag)
        return innermost, (_text_block_kind(outer_name) if starts_here else None)
    for child in tag.find_all("ix:nonnumeric"):
        if name := _text_block_name(child, names):
            return name, _text_block_kind(name)
    return None, None


def _text_block_name(tag, names: dict[str, str]) -> str | None:
    """The *TextBlock element name the tag carries (directly or as a continuation), if any."""
    if not isinstance(tag, Tag) or tag.name not in TEXT_BLOCK_TAGS:
        return None
    name = tag.get("name") if tag.name == "ix:nonnumeric" else names.get(tag.get("id", ""))
    return name if name and name.endswith("TextBlock") else None


def _text_block_kind(name: str) -> str:
    """note, policy or table, from the element name (us-gaap:LeasesOfLesseeDisclosureTextBlock)."""
    local = name.split(":")[-1]
    if "Policy" in local:
        return "policy"
    return "table" if "Table" in local or local.startswith("Schedule") else "note"


def _first_text(tag: Tag):
    """The first non-blank string inside the tag."""
    return next((s for s in tag.find_all(string=True) if s.strip()), None)
