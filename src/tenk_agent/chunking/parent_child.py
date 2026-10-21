"""Parent-child chunking with LlamaIndex's HierarchicalNodeParser: PARENT-token parents, each with
CHILD-token children (what search matches). Spec: experiments/V0_PARSING_CHUNKING.md §3.2.
"""

from tenk_agent.chunking import common
from tenk_agent.chunking.common import (
    assign_ids,
    captions,
    chunk_path,
    make_child,
    make_chunk,
    sentences,
    subsections,
    table_chunks,
    tokenizer,
)


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
        node_parser_map={
            "parent": splitter(common.PARENT, 0),
            "child": splitter(common.CHILD, common.CHILD_OVERLAP),
        },
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
        children = [make_child("prose", k.text, path) for k in kids]
        out.append(make_chunk(section, "prose", parent.text, own, children=children))
    return out
