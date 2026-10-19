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
HEADER_ROWS: int
OVERLAP_CHARS: int
YEAR = re.compile(r"\b(20[0-3]\d)\b")  # C8
SENTENCE_END = re.compile(r"[.;]\s+")  # C5
# C5: a period after one of these doesn't end a sentence ("the U.S. Department of Commerce").
ABBREVIATION = re.compile(r"(?:^|[\s(])(?:U\.S|U\.K|Inc|Corp|Co|Ltd|No|Nos|e\.g|i\.e|vs)\.$")
METHODS = ("rule", "parent_child", "agentic")


def configure(chunking: dict) -> None:
    """Set the sizes above from config.yaml's `chunking:`."""
    global TOKENIZER, TARGET, PARENT, CHILD, CHILD_OVERLAP, CANDIDATE_SIZE
    global CAPTION_CHARS, TABLE_CHARS, HEADER_ROWS, OVERLAP_CHARS
    TOKENIZER = chunking["tokenizer"]
    TARGET = chunking["target"]
    PARENT, CHILD, CHILD_OVERLAP = chunking["parent"], chunking["child"], chunking["child_overlap"]
    CANDIDATE_SIZE = chunking["candidate_size"]
    CAPTION_CHARS, TABLE_CHARS = chunking["caption_chars"], chunking["table_chars"]
    HEADER_ROWS, OVERLAP_CHARS = chunking["header_rows"], chunking["overlap_chars"]
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


def table_chunks(section: dict, i: int, **extra) -> list[dict]:
    """C2–C4: the chunk(s) for the table at section["blocks"][i]: the caption (up to 2 short
    blocks right before it) and its rows, split into row groups only if over TABLE_CHARS."""
    blocks = section["blocks"]
    caption: list[dict] = []
    for b in reversed(blocks[max(0, i - 2) : i]):
        if b["kind"] == "table" or len(b["text"]) > CAPTION_CHARS:
            break
        caption.insert(0, b)
    rows = ["| " + " | ".join(r) + " |" for r in blocks[i]["rows"]]
    head = [c["text"] for c in caption] + rows[:HEADER_ROWS]  # repeated in every row group
    groups: list[list[str]] = [[]]
    for row in rows[HEADER_ROWS:]:
        if groups[-1] and len("\n".join(head + groups[-1] + [row])) > TABLE_CHARS:
            groups.append([])
        groups[-1].append(row)
    sources = caption + [blocks[i]]
    return [make_chunk(section, "table", "\n".join(head + g), sources, **extra) for g in groups]


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
            for b in group["body"]:
                if b["kind"] == "table":
                    chunks += _pack(section, heads, prose)
                    prose = []
                    chunks += table_chunks(section, index[b["id"]])
                else:
                    prose += [(p, b) for p in split_at_sentences(b["text"], limit)]
            chunks += _pack(section, heads, prose)
    return assign_ids(document, chunks)


def _pack(section: dict, heads: list[dict], prose: list[tuple[str, dict]]) -> list[dict]:
    """C6: pack a subsection's paragraphs greedily up to TARGET tokens. C10: the first chunk starts
    with all its heading lines, later ones with its own (last) heading. C7: a later chunk also
    repeats the previous chunk's last paragraph if it's short and still fits."""
    if not prose:  # a heading over only tables is already in their captions
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
    (what search matches). Each data table is its own parent with no children."""
    parser = _hierarchical_parser()
    chunks: list[dict] = []
    for section in document["sections"]:
        index = {b["id"]: i for i, b in enumerate(section["blocks"])}
        for group in subsections(section["blocks"]):
            paragraphs = [b for b in group["body"] if b["kind"] != "table"]
            if paragraphs:  # a heading over only tables is already in their captions
                chunks += _parents(section, group["headings"] + paragraphs, parser)
            for b in group["body"]:
                if b["kind"] == "table":
                    chunks += table_chunks(section, index[b["id"]], children=[])
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
    """One subsection's text as parent chunks, each listing its children. Every child carries the
    subsection's heading path, since most children's text doesn't include the heading (T2)."""
    from llama_index.core import Document

    path = chunk_path(blocks)
    meta = {"item": section["item"], "heading_path": " › ".join(path)}
    doc = Document(
        text="\n".join(b["text"] for b in blocks),
        metadata=meta,
        # Metadata rides along but doesn't count toward chunk sizes.
        excluded_embed_metadata_keys=list(meta),
        excluded_llm_metadata_keys=list(meta),
    )
    nodes = {n.node_id: n for n in parser.get_nodes_from_documents([doc])}
    out = []
    for parent in (n for n in nodes.values() if n.parent_node is None):
        kids = [nodes[c.node_id] for c in (parent.child_nodes or []) if c.node_id in nodes]
        children = [{"text": k.text, "tokens": tokens(k.text), "heading_path": path} for k in kids]
        out.append(make_chunk(section, "prose", parent.text, blocks, children=children))
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
