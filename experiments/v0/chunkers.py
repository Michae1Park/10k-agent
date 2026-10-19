"""Three chunkers on the structured document, all sharing table handling and chunk metadata:

  rule          rule-based: a new chunk at every heading, prose packed to TARGET tokens (C1–C10)
  parent_child  LlamaIndex HierarchicalNodeParser: PARENT-token parents, CHILD-token children
  agentic       Chonkie SlumberChunker: the LLM picks each boundary

Every chunk stays inside one Item (C1); each data table is its own chunk (C2–C4); metadata and
ids are the same for all (T1–T3). Spec: experiments/V0_PARSING_CHUNKING.md §2.4–2.5, §3.
"""

import json
import os
import re
from functools import lru_cache

# Sizes from config.yaml's `chunking:` (meanings there), set by configure() before chunking.
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
METHODS = ("rule", "parent_child", "agentic")


def configure(chunking: dict) -> None:
    """Set the sizes above from config.yaml's `chunking:`."""
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


def run_all(
    document: dict, chunking: dict, model: str, base_url: str, methods=METHODS
) -> dict[str, list[dict]]:
    """method → its chunks. `chunking` is config.yaml's `chunking:`; `model` and `base_url` are
    the LLM the agentic chunker calls."""
    configure(chunking)
    run = {
        "rule": lambda: rule_based(document),
        "parent_child": lambda: parent_child(document),
        "agentic": lambda: agentic(document, model, base_url),
    }
    return {m: run[m]() for m in methods}


# ---------- shared ----------


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
            extra["children"] = [_child(kind, t, path) for kind, t in kids]
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


def _child(kind: str, text: str, path: list[str]) -> dict:
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


# ---------- rule-based (C1–C10) ----------


def rule_based(document: dict) -> list[dict]:
    """Per subsection: each table is its own chunk; the prose around it is packed to TARGET."""
    chunks: list[dict] = []
    for section in document["sections"]:
        index = {b["id"]: i for i, b in enumerate(section["blocks"])}
        for group in subsections(section["blocks"]):
            heads = group["headings"]
            # C5 leaves room for the heading lines every chunk starts with (C10).
            limit = max(TARGET // 2, TARGET - tokens("\n".join(h["text"] for h in heads)) - 2)
            prose: list[tuple[str, dict]] = []  # (paragraph text, source block), to be packed
            captioned = captions(section, group["body"])
            for b in group["body"]:
                if b["kind"] == "table":
                    chunks += _pack(section, heads, prose)
                    prose = []
                    chunks += table_chunks(section, index[b["id"]])
                elif b["id"] not in captioned:
                    prose += [(p, b) for p in split_at_sentences(b["text"], limit)]
            chunks += _pack(section, heads, prose)
    return assign_ids(document, chunks)


def _pack(section: dict, heads: list[dict], prose: list[tuple[str, dict]]) -> list[dict]:
    """C6: pack a subsection's paragraphs greedily up to TARGET tokens. C10: the first chunk starts
    with all its heading lines, later ones with its own (last) heading. C7: a later chunk also
    repeats the previous chunk's last paragraph if it's short and still fits."""
    if not prose:  # a heading over only tables (and their captions) is already in their chunks
        return []

    def text(lines: list[tuple[str, dict]]) -> str:
        return "\n".join(t for t, _ in lines)

    lead = [(heads[-1]["text"], heads[-1])] if heads else []
    packed: list[list[tuple[str, dict]]] = [[(h["text"], h) for h in heads]]
    for i, paragraph in enumerate(prose):
        current = packed[-1]
        if i > 0 and tokens(text(current + [paragraph])) > TARGET:
            last = current[-1]
            overlap = (
                len(last[0]) <= OVERLAP_CHARS and tokens(text(lead + [last, paragraph])) <= TARGET
            )
            packed.append(lead + ([last] if overlap else []))
        packed[-1].append(paragraph)
    return [make_chunk(section, "prose", text(c), [b for _, b in c]) for c in packed]


# ---------- parent-child (LlamaIndex) ----------


def parent_child(document: dict) -> list[dict]:
    """Parents ≤ PARENT tokens that never cross a subsection, each with children ≤ CHILD tokens
    (what search matches). Each data table is its own parent; its children are a title (caption
    + header rows) and one per row."""
    parser = _hierarchical_parser()
    chunks: list[dict] = []
    for section in document["sections"]:
        index = {b["id"]: i for i, b in enumerate(section["blocks"])}
        for group in subsections(section["blocks"]):
            captioned = captions(section, group["body"])
            paragraphs = [
                b for b in group["body"] if b["kind"] != "table" and b["id"] not in captioned
            ]
            if paragraphs:  # a heading over only tables (and their captions) is in their chunks
                chunks += _parents(section, group["headings"] + paragraphs, parser)
            for b in group["body"]:
                if b["kind"] == "table":
                    chunks += table_chunks(section, index[b["id"]], children=True)
    return assign_ids(document, chunks)


def _hierarchical_parser():
    """Two sentence splitters, parent then child, counting Qwen3 tokens and cutting sentences
    as C5 does (LlamaIndex's default punkt splitter cuts at "U.S. Tariffs")."""
    from llama_index.core.node_parser import HierarchicalNodeParser, SentenceSplitter

    def splitter(size: int, overlap: int) -> SentenceSplitter:
        return SentenceSplitter(
            chunk_size=size,
            chunk_overlap=overlap,
            tokenizer=lambda s: tokenizer().encode(s, add_special_tokens=False),
            paragraph_separator="\n",
            chunking_tokenizer_fn=sentences,
        )

    return HierarchicalNodeParser.from_defaults(
        node_parser_ids=["parent", "child"],
        node_parser_map={"parent": splitter(PARENT, 0), "child": splitter(CHILD, CHILD_OVERLAP)},
    )


def _parents(section: dict, blocks: list[dict], parser) -> list[dict]:
    """One subsection's text as parent chunks, each listing its children. A parent's blocks are
    the ones its text spans. Every child carries the subsection's heading path, since most
    children's text doesn't include the heading (T2)."""
    from llama_index.core import Document

    path = chunk_path(blocks)
    meta = {"item": section["item"], "heading_path": " › ".join(path)}
    text = "\n".join(b["text"] for b in blocks)
    spans, pos = [], 0
    for b in blocks:
        spans.append((b, pos, pos + len(b["text"])))
        pos += len(b["text"]) + 1
    doc = Document(
        text=text,
        metadata=meta,
        # Metadata rides along but doesn't count toward chunk sizes.
        excluded_embed_metadata_keys=list(meta),
        excluded_llm_metadata_keys=list(meta),
    )
    nodes = {n.node_id: n for n in parser.get_nodes_from_documents([doc])}
    out = []
    for parent in (n for n in nodes.values() if n.parent_node is None):
        kids = [nodes[c.node_id] for c in (parent.child_nodes or []) if c.node_id in nodes]
        start, end = parent.start_char_idx, parent.end_char_idx
        own = [b for b, s, e in spans if s < end and e > start]
        children = [_child("prose", k.text, path) for k in kids]
        out.append(make_chunk(section, "prose", parent.text, own, children=children))
    return out


# ---------- agentic (Chonkie SlumberChunker) ----------


def agentic(document: dict, model: str, base_url: str) -> list[dict]:
    """Each Item is rendered as Markdown, tables as [TABLE n] placeholders. The LLM picks each
    chunk's end among paragraph (then sentence) candidates. Character spans map chunks back to
    blocks; a table placeholder is replaced by the table's own chunk(s)."""
    chunker = _slumber_chunker(model, base_url)
    chunks: list[dict] = []
    for section in document["sections"]:
        text, spans = render_markdown(section)
        index = {b["id"]: i for i, b in enumerate(section["blocks"])}
        for piece in chunker.chunk(text):
            start, end = piece.start_index, piece.end_index
            touched = [(b, s) for b, s, e in spans if s < end and e > start]
            prose = [b for b, _ in touched if b["kind"] != "table"]
            body = _strip_markdown(text[start:end], section)
            if prose and body:
                chunks.append(make_chunk(section, "prose", body, prose))
            for b, s in touched:
                if b["kind"] == "table" and start <= s < end:  # the placeholder starts here
                    chunks += table_chunks(section, index[b["id"]])
    return assign_ids(document, chunks)


def _slumber_chunker(model: str, base_url: str):
    from chonkie import RecursiveLevel, RecursiveRules, SlumberChunker

    candidates = RecursiveRules(
        levels=[
            RecursiveLevel(delimiters=["\n\n"]),  # paragraphs
            RecursiveLevel(delimiters=[". ", "; ", "? ", "! "]),  # sentences
            RecursiveLevel(whitespace=True),  # words
        ]
    )
    return SlumberChunker(
        genie=_genie(model, base_url),
        tokenizer=tokenizer(),
        chunk_size=TARGET,
        rules=candidates,
        candidate_size=CANDIDATE_SIZE,
        min_characters_per_chunk=1,  # the default 24 merges short headings into the previous one
        extract_mode="json",
        verbose=False,
    )


def _item_title(section: dict) -> str:
    return f"Item {section['item']}. {section['title']}"


def render_markdown(section: dict) -> tuple[str, list[tuple[dict, int, int]]]:
    """(Markdown, [(block, start, end)]): `# Item`, a heading at level n as #×(n+1), tables as
    [TABLE n]; paragraphs separated by blank lines."""
    parts = [f"# {_item_title(section)}"]
    spans: list[tuple[dict, int, int]] = []
    pos = len(parts[0]) + 2
    tables = 0
    for b in section["blocks"]:
        if b["kind"] == "heading":
            line = "#" * (b["level"] + 1) + " " + b["text"]
        elif b["kind"] == "table":
            tables += 1
            line = f"[TABLE {tables}]"
        else:
            line = b["text"]
        spans.append((b, pos, pos + len(line)))
        parts.append(line)
        pos += len(line) + 2
    return "\n\n".join(parts), spans


def _strip_markdown(text: str, section: dict) -> str:
    """Back to plain lines: no # marks, table placeholders or Item title."""
    lines = [re.sub(r"^#{1,7} ", "", ln) for ln in text.strip().split("\n\n")]
    drop = {_item_title(section), ""}
    return "\n".join(
        ln for ln in lines if ln not in drop and not re.fullmatch(r"\[TABLE \d+\]", ln)
    )


def _genie(model: str, base_url: str):
    from chonkie.genie import OpenAIGenie

    class LocalGenie(OpenAIGenie):
        """OpenAIGenie with temperature 0, thinking off and schema-constrained JSON (vLLM)."""

        def _create(self, prompt: str, **kw):
            return self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                **kw,
            )

        def generate(self, prompt: str) -> str:
            return self._create(prompt, max_tokens=64).choices[0].message.content

        def generate_json(self, prompt: str, schema) -> dict:
            fmt = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
            }
            r = self._create(prompt, max_tokens=64, response_format=fmt)
            return json.loads(r.choices[0].message.content)

    return LocalGenie(model=model, base_url=base_url, api_key="not-needed")
