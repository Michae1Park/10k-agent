"""What the three chunkers share: sizes and token counts, subsections, table chunks (C2–C4),
sentence cuts (C5), chunk metadata and ids (T1–T3). Spec: experiments/V0_PARSING_CHUNKING.md
§2.4–2.5, §3.
"""

import os
import re
from functools import lru_cache

# Sizes from the config's `chunking:` (meanings there), set by configure() before chunking. The
# chunkers read them as common.TARGET etc., so configure() reaches every module.
TOKENIZER: str
TARGET: int
PARENT: int
CHILD: int
CHILD_OVERLAP: int
CANDIDATE_SIZE: int
CAPTION_CHARS: int
TABLE_CHARS: int
OVERLAP_CHARS: int
YEAR = re.compile(r"\b(20[0-3]\d)\b")  # C8
SENTENCE_END = re.compile(r"[.;]\s+")  # C5
# C5: a period after one of these doesn't end a sentence ("the U.S. Department of Commerce").
ABBREVIATION = re.compile(r"(?:^|[\s(])(?:U\.S|U\.K|Inc|Corp|Co|Ltd|No|Nos|e\.g|i\.e|vs)\.$")
VALUE = re.compile(r"\(?\$?\s*\(?[\d,]*\.?\d+\)?\s*%?\)?")  # a table cell holding a number
BARE_YEAR = re.compile(r"(19|20)\d\d")


def configure(chunking: dict) -> None:
    """Set the sizes above from the config's `chunking:`."""
    global TOKENIZER, TARGET, PARENT, CHILD, CHILD_OVERLAP, CANDIDATE_SIZE
    global CAPTION_CHARS, TABLE_CHARS, OVERLAP_CHARS
    TOKENIZER = chunking["tokenizer"]
    TARGET = chunking["target"]
    PARENT, CHILD, CHILD_OVERLAP = chunking["parent"], chunking["child"], chunking["child_overlap"]
    CANDIDATE_SIZE = chunking["candidate_size"]
    CAPTION_CHARS, TABLE_CHARS = chunking["caption_chars"], chunking["table_chars"]
    OVERLAP_CHARS = chunking["overlap_chars"]
    tokenizer.cache_clear()


@lru_cache(maxsize=1)
def tokenizer():
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(TOKENIZER, local_files_only=True)


def tokens(text: str) -> int:
    return len(tokenizer().encode(text, add_special_tokens=False))


def subsections(blocks: list[dict]) -> list[dict]:
    """Split a section's blocks at each heading into {"headings": [...], "body": [...]}.
    Headings with nothing between them (a category, then its first risk factor) open one
    subsection together."""
    groups: list[dict] = []
    for b in blocks:
        if b["kind"] == "heading":
            if groups and not groups[-1]["body"]:
                groups[-1]["headings"].append(b)
            else:
                groups.append({"headings": [b], "body": []})
        else:
            if not groups:
                groups.append({"headings": [], "body": []})
            groups[-1]["body"].append(b)
    return groups


def chunk_path(blocks: list[dict]) -> list[str]:
    """T2: the deepest heading path shared by all the chunk's blocks. A heading that is an ancestor
    of another block in the chunk (a category above its risk factor) doesn't narrow it."""
    paths = [b["heading_path"] for b in blocks]
    keep = [
        p
        for b, p in zip(blocks, paths, strict=True)
        if not (b["kind"] == "heading" and any(len(q) > len(p) and q[: len(p)] == p for q in paths))
    ]
    return list(os.path.commonprefix(keep or paths))


def make_chunk(section: dict, kind: str, text: str, blocks: list[dict], **extra) -> dict:
    """T1: a chunk with its Item, heading path, source blocks and pages (first, last); tables add
    units and years (C8). Ids (T3) are added by assign_ids."""
    printed = [b["printed_page"] for b in blocks if b.get("printed_page")]
    chunk = {
        "item": section["item"],
        "item_title": section["title"],
        "heading_path": chunk_path(blocks),
        "kind": kind,
        "text": text,
        "block_ids": [b["id"] for b in blocks],
        "pages": [blocks[0]["page"], blocks[-1]["page"]],
        "printed_pages": [printed[0], printed[-1]] if printed else None,
        "tokens": tokens(text),
    }
    if kind == "table":
        table = next(b for b in blocks if b["kind"] == "table")
        top = "\n".join(text.splitlines()[:6])
        chunk |= {
            "units": table.get("units"),
            "years_covered": sorted({int(y) for y in YEAR.findall(top)}),
        }
    return chunk | extra


def assign_ids(document: dict, chunks: list[dict]) -> list[dict]:
    """T3: <TICKER>-FY<year>-<item>-<seq>, numbered per Item."""
    filing = document["filing"]
    seq: dict[str, int] = {}
    for c in chunks:
        n = seq.get(c["item"], 0)
        seq[c["item"]] = n + 1
        c["id"] = f"{filing['ticker']}-FY{filing['fiscal_year']}-{c['item']}-{n:03d}"
    return chunks


def caption(blocks: list[dict], i: int) -> list[dict]:
    """C3: the table at blocks[i]'s caption, up to 2 short blocks right before it."""
    out: list[dict] = []
    for b in reversed(blocks[max(0, i - 2) : i]):
        if b["kind"] == "table" or len(b["text"]) > CAPTION_CHARS:
            break
        out.insert(0, b)
    return out


def captions(section: dict, body: list[dict]) -> set[str]:
    """C3: ids of the blocks that caption a table in `body`. They are in the table's chunk, so
    prose leaves them out rather than repeat them in a chunk of their own."""
    index = {b["id"]: i for i, b in enumerate(section["blocks"])}
    return {
        c["id"]
        for b in body
        if b["kind"] == "table"
        for c in caption(section["blocks"], index[b["id"]])
    }


def table_chunks(section: dict, i: int, children: bool = False) -> list[dict]:
    """C2–C4: the chunk(s) for the table at section["blocks"][i]: the caption and its rows, split
    into row groups only if over TABLE_CHARS, each repeating the caption and header rows. With
    `children`, each chunk lists a title child (caption + header rows) and one child per row
    (row_children)."""
    blocks = section["blocks"]
    cap = caption(blocks, i)
    header = header_rows(blocks[i]["rows"])
    rows = ["| " + " | ".join(r) + " |" for r in blocks[i]["rows"]]
    head = [c["text"] for c in cap] + rows[:header]  # repeated in every row group
    groups: list[list[int]] = [[]]  # row numbers
    for n in range(header, len(rows)):
        if (
            groups[-1]
            and len("\n".join(head + [rows[k] for k in groups[-1]] + [rows[n]])) > TABLE_CHARS
        ):
            groups.append([])
        groups[-1].append(n)
    sources = cap + [blocks[i]]
    labelled = row_children(blocks[i]["rows"]) if children else {}
    path = chunk_path(sources)
    out = []
    for g in groups:
        text = "\n".join(head + [rows[n] for n in g])
        extra = {}
        if children:
            kids = [("table_title", "\n".join(head))] + [
                ("row", labelled[n]) for n in g if n in labelled
            ]
            extra["children"] = [make_child(kind, t, path) for kind, t in kids]
        out.append(make_chunk(section, "table", text, sources, **extra))
    return out


def _is_value(cell: str) -> bool:
    return bool(VALUE.fullmatch(cell.strip())) and not BARE_YEAR.fullmatch(cell.strip())


def header_rows(rows: list[list[str]]) -> int:
    """C4: how many leading rows are the header: those before the first row with a number (years
    and dates aren't numbers) or a group label ("Federal:"). A table with no numbers at all has
    its first row as the header."""
    for n, r in enumerate(rows):
        if any(_is_value(c) for c in r[1:]) or (len(r) == 1 and r[0].endswith(":")):
            return n
    return min(1, len(rows))


def row_children(rows: list[list[str]]) -> dict[int, str]:
    """Row number → that row as a sentence naming its columns, e.g. "Americas: 2025 = $178,353;
    Change = 7%; …", for every row under the header (header_rows). A value-less row such as
    "Federal:" is a group label, prefixed to the rows under it until a "Total" row. Values align
    to the columns from the right, or to the year columns when there are exactly that many (a
    "Percentage of total net sales" row)."""
    header = rows[: header_rows(rows)]
    widest = max(header, key=len) if header else []
    data_width = max((len(r) for r in rows[len(header) :]), default=0)
    # The widest header row names the value columns; it names the label column too if it is as
    # wide as the data rows.
    columns = widest[1:] if widest and len(widest) >= data_width else widest
    years = [c for c in columns if YEAR.search(c)]

    out, group = {}, ""
    for n, r in enumerate(rows[len(header) :], start=len(header)):
        label, values = r[0].strip(), [c.strip() for c in r[1:]]
        if not any(values):  # a group label
            group = label.rstrip(":")
            continue
        name = f"{group} › {label}" if group else label
        names = years if len(values) == len(years) < len(columns) else columns[-len(values) :]
        if len(names) == len(values):
            cells = "; ".join(f"{c} = {v}" for c, v in zip(names, values, strict=True))
        else:  # no header to name them
            cells = " ".join(values)
        out[n] = f"{name}: {cells}"
        if label.lower().startswith("total"):
            group = ""
    return out


def make_child(kind: str, text: str, path: list[str]) -> dict:
    return {"kind": kind, "text": text, "tokens": tokens(text), "heading_path": path}


def sentences(text: str) -> list[str]:
    """C5: text cut after each sentence end (`.` or `;` then whitespace, not after an
    abbreviation). Pieces keep their trailing whitespace, so they join back to `text`; the
    parent-child splitters use this too."""
    pieces, start = [], 0
    for m in SENTENCE_END.finditer(text):
        if not ABBREVIATION.search(text[max(start, m.start() - 8) : m.start() + 1]):
            pieces.append(text[start : m.end()])
            start = m.end()
    return pieces + ([text[start:]] if start < len(text) else [])


def split_at_sentences(text: str, limit: int) -> list[str]:
    """C5: a paragraph over `limit` tokens splits at sentence ends."""
    if tokens(text) <= limit:
        return [text]
    parts, current = [], ""
    for sentence in (s.strip() for s in sentences(text)):
        if current and tokens(f"{current} {sentence}") > limit:
            parts.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
    return parts + ([current] if current else [])
