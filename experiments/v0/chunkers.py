"""V0 chunkers on the structured document: rule-based, parent-child (LlamaIndex) and agentic
(Chonkie SlumberChunker). All share subsection grouping and metadata tagging (T1–T3).
Spec: experiments/V0_PARSING_CHUNKING.md §2.4–2.5, §3.
"""

import json
import re
from functools import lru_cache

TOKENIZER = "Qwen/Qwen3-Embedding-0.6B"
TARGET = 600  # rule-based and agentic chunk size, tokens
PARENT, CHILD = 1280, 256  # parent-child, 5:1
CAPTION_CHARS = 300  # C3
TABLE_CHARS = 7200  # C4
OVERLAP_CHARS = 600  # C7
HEADER_ROWS = 2
YEAR = re.compile(r"\b(20[0-3]\d)\b")
SENTENCE = re.compile(r"(?<=[.;])\s+")


@lru_cache(maxsize=1)
def tokenizer():
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(TOKENIZER, local_files_only=True)


def tokens(text: str) -> int:
    return len(tokenizer().encode(text, add_special_tokens=False))


METHODS = ("rule", "parent_child", "agentic")


def run_all(document: dict, model: str, base_url: str, methods=METHODS) -> dict[str, list[dict]]:
    run = {
        "rule": lambda: rule_based(document),
        "parent_child": lambda: parent_child(document),
        "agentic": lambda: agentic(document, model, base_url),
    }
    return {m: run[m]() for m in methods}


# ---------- shared ----------


def subsections(blocks: list[dict]) -> list[dict]:
    """Groups that start at each heading. Headings with no text between them (a category, then its
    first risk factor) open one group together."""
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
    """T2: the deepest heading path shared by all blocks. A heading whose path is a strict prefix of
    another block's path (a category above its risk factor) is an ancestor and doesn't narrow it."""
    paths = [b["heading_path"] for b in blocks]
    keep = [
        p
        for b, p in zip(blocks, paths, strict=True)
        if not (b["kind"] == "heading" and any(len(q) > len(p) and q[: len(p)] == p for q in paths))
    ]
    keep = keep or paths
    common = keep[0]
    for p in keep[1:]:
        n = 0
        while n < min(len(common), len(p)) and common[n] == p[n]:
            n += 1
        common = common[:n]
    return list(common)


def make_chunk(
    document: dict, section: dict, kind: str, text: str, blocks: list[dict], **extra
) -> dict:
    """T1 fields; ids (T3) are numbered per Item at the end."""
    chunk = {
        "item": section["item"],
        "item_title": section["title"],
        "heading_path": chunk_path(blocks),
        "kind": kind,
        "text": text,
        "block_ids": [b["id"] for b in blocks],
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


def number(document: dict, chunks: list[dict]) -> list[dict]:
    """T3: <TICKER>-FY<year>-<item>-<seq>."""
    f = document["filing"]
    seq: dict[str, int] = {}
    for c in chunks:
        n = seq.get(c["item"], 0)
        seq[c["item"]] = n + 1
        c["id"] = f"{f['ticker']}-FY{f['fiscal_year']}-{c['item']}-{n:03d}"
    return chunks


def table_texts(section: dict, i: int) -> tuple[list[str], list[dict]]:
    """C2–C4: one table chunk (row groups over TABLE_CHARS), with up to 2 short caption lines."""
    caption: list[dict] = []
    for b in reversed(section["blocks"][max(0, i - 2) : i]):
        if b["kind"] == "table" or len(b["text"]) > CAPTION_CHARS:
            break
        caption.insert(0, b)
    rows = ["| " + " | ".join(r) + " |" for r in section["blocks"][i]["rows"]]
    head = [c["text"] for c in caption] + rows[:HEADER_ROWS]
    body = rows[HEADER_ROWS:]
    if len("\n".join(head + body)) <= TABLE_CHARS:
        return ["\n".join(head + body)], caption
    texts, group = [], []
    for row in body:
        if group and len("\n".join(head + group + [row])) > TABLE_CHARS:
            texts.append("\n".join(head + group))
            group = []
        group.append(row)
    return texts + (["\n".join(head + group)] if group else []), caption


def split_long(text: str, limit: int) -> list[str]:
    """C5: a paragraph over the limit splits at sentence ends."""
    if tokens(text) <= limit:
        return [text]
    parts, current = [], ""
    for sentence in SENTENCE.split(text):
        if current and tokens(f"{current} {sentence}") > limit:
            parts.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
    return parts + ([current] if current else [])


# ---------- rule-based (C1–C10) ----------


def rule_based(document: dict) -> list[dict]:
    chunks: list[dict] = []
    for section in document["sections"]:
        index = {b["id"]: i for i, b in enumerate(section["blocks"])}
        for group in subsections(section["blocks"]):
            heads = group["headings"]
            # Room for the heading lines that start every chunk of the subsection (C10).
            limit = max(TARGET // 2, TARGET - tokens("\n".join(h["text"] for h in heads)) - 2)
            prose: list[tuple[str, dict]] = []
            for b in group["body"]:
                if b["kind"] == "table":
                    chunks += _pack(document, section, heads, prose)
                    prose = []
                    texts, caption = table_texts(section, index[b["id"]])
                    chunks += [
                        make_chunk(document, section, "table", t, caption + [b]) for t in texts
                    ]
                else:
                    prose += [(p, b) for p in split_long(b["text"], limit)]
            chunks += _pack(document, section, heads, prose)
    return number(document, chunks)


def _pack(
    document: dict, section: dict, heads: list[dict], prose: list[tuple[str, dict]]
) -> list[dict]:
    """C6 pack to TARGET tokens; C7 overlap; C10 never across a heading, and every chunk after the
    first starts with the subsection's heading line."""
    if not prose:  # a heading over only tables is already in their captions
        return []
    first = [(h["text"], h) for h in heads]
    lead = [(heads[-1]["text"], heads[-1])] if heads else []
    join = lambda items: "\n".join(t for t, _ in items)  # noqa: E731
    out, current, has_prose = [], list(first), False
    for item in prose:
        if has_prose and tokens(join(current + [item])) > TARGET:
            out.append(current)
            last, current = current[-1], list(lead)
            # C7: carry the last paragraph over only when it is short and still fits.
            if len(last[0]) <= OVERLAP_CHARS and tokens(join(lead + [last, item])) <= TARGET:
                current.append(last)
        current.append(item)
        has_prose = True
    if has_prose:
        out.append(current)
    return [
        make_chunk(document, section, "prose", "\n".join(t for t, _ in c), [b for _, b in c])
        for c in out
    ]


# ---------- parent-child (LlamaIndex) ----------


def parent_child(document: dict) -> list[dict]:
    """Parents ≤ 1,280 tokens never crossing a leaf subsection; children ≤ 256 (5:1). Each data
    table is its own parent with no children."""
    from llama_index.core import Document
    from llama_index.core.node_parser import HierarchicalNodeParser, SentenceSplitter

    encode = lambda s: tokenizer().encode(s, add_special_tokens=False)  # noqa: E731
    parser = HierarchicalNodeParser.from_defaults(
        node_parser_ids=["parent", "child"],
        node_parser_map={
            "parent": SentenceSplitter(
                chunk_size=PARENT, chunk_overlap=0, tokenizer=encode, paragraph_separator="\n"
            ),
            "child": SentenceSplitter(
                chunk_size=CHILD, chunk_overlap=20, tokenizer=encode, paragraph_separator="\n"
            ),
        },
    )
    chunks: list[dict] = []
    for section in document["sections"]:
        index = {b["id"]: i for i, b in enumerate(section["blocks"])}
        for group in subsections(section["blocks"]):
            paragraphs = [b for b in group["body"] if b["kind"] != "table"]
            if paragraphs:  # a heading over only tables is already in their captions
                chunks += _parents(
                    document, section, group["headings"] + paragraphs, parser, Document
                )
            for b in group["body"]:
                if b["kind"] == "table":
                    texts, caption = table_texts(section, index[b["id"]])
                    chunks += [
                        make_chunk(document, section, "table", t, caption + [b], children=[])
                        for t in texts
                    ]
    return number(document, chunks)


def _parents(document, section, prose: list[dict], parser, Document) -> list[dict]:
    if not prose:
        return []
    meta = {"item": section["item"], "heading_path": " › ".join(chunk_path(prose))}
    doc = Document(
        text="\n".join(b["text"] for b in prose),
        metadata=meta,
        excluded_embed_metadata_keys=list(meta),
        excluded_llm_metadata_keys=list(meta),
    )
    nodes = parser.get_nodes_from_documents([doc])
    by_id = {n.node_id: n for n in nodes}
    roots = [n for n in nodes if n.parent_node is None]
    out = []
    for root in roots:
        kids = [by_id[c.node_id] for c in (root.child_nodes or []) if c.node_id in by_id]
        children = [{"text": k.text, "tokens": tokens(k.text)} for k in kids]
        out.append(make_chunk(document, section, "prose", root.text, prose, children=children))
    return out


# ---------- agentic (Chonkie SlumberChunker) ----------


def agentic(document: dict, model: str, base_url: str) -> list[dict]:
    """The LLM picks each boundary among paragraph (then sentence) candidates in a ~600-token
    window. Input per Item is Markdown; tables are placeholders, put back whole as own chunks."""
    from chonkie import RecursiveLevel, RecursiveRules, SlumberChunker

    rules = RecursiveRules(
        levels=[
            RecursiveLevel(delimiters=["\n\n"]),
            RecursiveLevel(delimiters=[". ", "; ", "? ", "! "]),
            RecursiveLevel(whitespace=True),
        ]
    )
    chunker = SlumberChunker(
        genie=_genie(model, base_url),
        tokenizer=tokenizer(),
        chunk_size=TARGET,
        rules=rules,
        candidate_size=128,
        min_characters_per_chunk=1,
        extract_mode="json",
        verbose=False,
    )
    chunks: list[dict] = []
    for section in document["sections"]:
        text, spans = render_markdown(section)
        index = {b["id"]: i for i, b in enumerate(section["blocks"])}
        for piece in chunker.chunk(text):
            touched = [
                (b, s, e) for b, s, e in spans if s < piece.end_index and e > piece.start_index
            ]
            prose = [b for b, _, _ in touched if b["kind"] != "table"]
            body = _strip_markdown(text[piece.start_index : piece.end_index])
            if prose and body:
                chunks.append(make_chunk(document, section, "prose", body, prose))
            for b, s, _ in touched:
                if b["kind"] == "table" and piece.start_index <= s < piece.end_index:
                    texts, caption = table_texts(section, index[b["id"]])
                    chunks += [
                        make_chunk(document, section, "table", t, caption + [b]) for t in texts
                    ]
    return number(document, chunks)


def render_markdown(section: dict) -> tuple[str, list[tuple[dict, int, int]]]:
    """# Item, heading level n as #×(n+1), tables as [TABLE n]; plus each block's character span."""
    parts = [f"# Item {section['item']}. {section['title']}"]
    spans: list[tuple[dict, int, int]] = []
    pos = len(parts[0]) + 2
    n = 0
    for b in section["blocks"]:
        if b["kind"] == "heading":
            line = "#" * (b["level"] + 1) + " " + b["text"]
        elif b["kind"] == "table":
            n += 1
            line = f"[TABLE {n}]"
        else:
            line = b["text"]
        spans.append((b, pos, pos + len(line)))
        parts.append(line)
        pos += len(line) + 2
    return "\n\n".join(parts), spans


def _strip_markdown(text: str) -> str:
    lines = [re.sub(r"^#{1,7} ", "", ln) for ln in text.strip().split("\n\n")]
    lines = [
        ln
        for ln in lines
        if ln and not re.fullmatch(r"\[TABLE \d+\]", ln) and not ln.startswith("Item ")
    ]
    return "\n".join(lines)


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
            LocalGenie.calls += 1
            return json.loads(r.choices[0].message.content)

    LocalGenie.calls = 0
    return LocalGenie(model=model, base_url=base_url, api_key="not-needed")


def full_parent_ratio(chunk: dict) -> float | None:
    """3b: parent tokens ÷ mean child tokens."""
    kids = chunk.get("children") or []
    return chunk["tokens"] / (sum(k["tokens"] for k in kids) / len(kids)) if kids else None
