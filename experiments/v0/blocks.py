"""V0 deterministic layer (D1–D7): 10-K HTML → blocks with features. Only rules that hold for
every 10-K; everything that needs judgment is left to the structure agent (structure.py).
Spec: experiments/V0_PARSING_CHUNKING.md §2.1.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from tenk_agent.parse import BLOCK_TAGS, _clean, _hidden, _row_cells

ITEM_LABEL = re.compile(r"^items?\s+\d{1,2}[a-c]?\b", re.I)
TEXT_BLOCK = {"ix:nonnumeric", "ix:continuation"}
DEI = [
    "TradingSymbol",
    "DocumentFiscalYearFocus",
    "DocumentPeriodEndDate",
    "EntityRegistrantName",
    "DocumentType",
]


@dataclass
class Block:
    id: str
    kind: str  # "paragraph" or "table"
    text: str
    rows: list[list[str]] = field(default_factory=list)
    emph: str = "-"  # B, I, BI, - or runin
    runin: int = 0  # length of the emphasized lead when emph == "runin"
    xbrl: str | None = None  # innermost inline-XBRL text block the block sits in
    xbrl_start: str | None = (
        None  # note / policy / table: an outermost text block of that kind starts here
    )
    pb: bool = False  # a page break follows
    link: bool = False  # the text links within the document

    def features(self) -> str:
        """The `features` field of the LLM input line (system.md # Input)."""
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
    soup = BeautifulSoup(html, "lxml")
    names = _text_block_names(soup)
    blocks: list[Block] = []
    _walk(soup.body or soup, blocks, names)
    for i, b in enumerate(blocks):
        b.id = f"b{i + 1:04d}"
    return blocks


def cover_metadata(full_filing: Path) -> dict:
    """D3: filing metadata from the dei: cover tags, plus accession and URL from metadata.json."""
    html = full_filing.read_text(encoding="utf-8")
    meta: dict = {}
    for name in DEI:
        m = re.search(rf'name="dei:{name}"[^>]*>(.*?)</ix:', html, re.S)
        meta[name] = _clean(BeautifulSoup(m.group(1), "lxml").get_text(" ")) if m else None
    assert meta["DocumentType"] == "10-K", meta["DocumentType"]
    side = json.loads((full_filing.parent / "metadata.json").read_text())
    return {
        "ticker": meta["TradingSymbol"],
        "registrant": meta["EntityRegistrantName"],
        "fiscal_year": int(meta["DocumentFiscalYearFocus"]),
        "period_end": side.get("period_end_date"),
        "accession": side.get("accession_no"),
        "source_url": side.get("source_url"),
    }


def _text_block_names(soup: BeautifulSoup) -> dict[str, str]:
    """D2: the element name behind every ix:continuation id, following continuedAt chains."""
    names: dict[str, str] = {}
    for tag in soup.find_all("ix:nonnumeric", attrs={"name": re.compile("TextBlock$")}):
        nxt = tag.get("continuedat")
        while nxt and nxt not in names:
            names[nxt] = tag["name"]
            cont = soup.find("ix:continuation", attrs={"id": nxt})
            nxt = cont.get("continuedat") if cont else None
    return names


def _block_kind(name: str) -> str:
    local = name.split(":")[-1]
    if "Policy" in local:
        return "policy"
    return "table" if "Table" in local or local.startswith("Schedule") else "note"


def _xbrl(tag: Tag, names: dict[str, str]) -> tuple[str | None, str | None]:
    """(innermost text block name, kind of an outermost text block that starts in this block).
    A text block can wrap many blocks (an ancestor) or start inside one, e.g. mid-heading."""
    spans = [(p, n) for p in [tag, *tag.parents] if (n := _text_block(p, names))]
    inner = spans[0][1] if spans else None
    if spans:
        outer_tag, outer_name = spans[-1]
        if outer_tag.name == "ix:nonnumeric" and _first_text(outer_tag) is _first_text(tag):
            return inner, _block_kind(outer_name)
        return inner, None
    for child in tag.find_all("ix:nonnumeric"):
        if name := _text_block(child, names):
            return name, _block_kind(name)
    return None, None


def _text_block(tag, names: dict[str, str]) -> str | None:
    if not isinstance(tag, Tag) or tag.name not in TEXT_BLOCK:
        return None
    name = tag.get("name") if tag.name == "ix:nonnumeric" else names.get(tag.get("id", ""))
    return name if name and name.endswith("TextBlock") else None


def _first_text(tag: Tag):
    return next((s for s in tag.find_all(string=True) if s.strip()), None)


def _walk(element: Tag, blocks: list[Block], names: dict[str, str]) -> None:
    """D5: paragraphs and tables in reading order; D1 drops hidden elements."""
    for child in element.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            text = _clean(str(child))
            if text and element.find(BLOCK_TAGS):
                blocks.append(Block("", "paragraph", text))
            continue
        if not isinstance(child, Tag) or _hidden(child):
            continue
        style = (child.get("style") or "").replace(" ", "").lower()
        if "page-break-before:always" in style and blocks:
            blocks[-1].pb = True
        if child.name == "table":
            rows = [cells for tr in child.find_all("tr") if (cells := _row_cells(tr))]
            if rows:
                block = Block("", "table", "\n".join(" | ".join(r) for r in rows), rows=rows)
                block.link = bool(child.find("a", href=re.compile("^#")))
                block.xbrl, block.xbrl_start = _xbrl(child, names)
                blocks.append(block)
        elif child.find(BLOCK_TAGS):
            _walk(child, blocks, names)
        elif text := _clean(child.get_text(" ")):
            block = Block("", "paragraph", text)
            block.emph, block.runin = _emphasis(child, text)
            link = child.find("a", href=re.compile("^#"))
            block.link = bool(link) and len(_clean(link.get_text(" "))) >= len(text) / 2
            block.xbrl, block.xbrl_start = _xbrl(child, names)
            blocks.append(block)
        if "page-break-after:always" in style and blocks:
            blocks[-1].pb = True


def _emphasis(tag: Tag, text: str) -> tuple[str, int]:
    """D7: B / I / BI when all text shares it; runin + N when only a leading run is emphasized."""
    runs: list[tuple[str, str]] = []
    for s in tag.find_all(string=True):
        if not s.strip():
            continue
        e = _run_emphasis(s, tag)
        if runs and runs[-1][0] == e:
            runs[-1] = (e, runs[-1][1] + str(s))
        else:
            runs.append((e, str(s)))
    styles = {e for e, t in runs if re.search(r"\w", t)}
    if len(styles) == 1 and "-" not in styles:
        return styles.pop(), 0
    if len(runs) > 1 and runs[0][0] != "-":
        lead = _clean(runs[0][1])
        if lead and text.startswith(lead) and any(e == "-" for e, _ in runs[1:]):
            return "runin", len(lead)
    return "-", 0


def _run_emphasis(node: NavigableString, stop: Tag) -> str:
    bold = italic = False
    for parent in node.parents:
        if parent is stop.parent:
            break
        style = (parent.get("style") or "").replace(" ", "").lower()
        bold |= parent.name in ("b", "strong") or bool(
            re.search(r"font-weight:(bold|[6-9]00)", style)
        )
        italic |= parent.name in ("i", "em") or "font-style:italic" in style
    return ("B" if bold else "") + ("I" if italic else "") or "-"
