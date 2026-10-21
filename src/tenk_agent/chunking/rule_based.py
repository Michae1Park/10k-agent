"""Rule-based chunking (C1–C10): a new chunk at every heading, each table its own chunk, prose
packed to TARGET tokens. Spec: experiments/V0_PARSING_CHUNKING.md §3.1.
"""

from tenk_agent.chunking import common
from tenk_agent.chunking.common import (
    assign_ids,
    captions,
    make_chunk,
    split_at_sentences,
    subsections,
    table_chunks,
    tokens,
)


def rule_based(document: dict) -> list[dict]:
    """Per subsection: each table is its own chunk; the prose around it is packed to TARGET."""
    chunks: list[dict] = []
    for section in document["sections"]:
        index = {b["id"]: i for i, b in enumerate(section["blocks"])}
        for group in subsections(section["blocks"]):
            heads = group["headings"]
            # C5 leaves room for the heading lines every chunk starts with (C10).
            limit = max(
                common.TARGET // 2, common.TARGET - tokens("\n".join(h["text"] for h in heads)) - 2
            )
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
        if i > 0 and tokens(text(current + [paragraph])) > common.TARGET:
            last = current[-1]
            overlap = (
                len(last[0]) <= common.OVERLAP_CHARS
                and tokens(text(lead + [last, paragraph])) <= common.TARGET
            )
            packed.append(lead + ([last] if overlap else []))
        packed[-1].append(paragraph)
    return [make_chunk(section, "prose", text(c), [b for _, b in c]) for c in packed]
