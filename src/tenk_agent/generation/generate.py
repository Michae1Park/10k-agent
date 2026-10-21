"""V2 generation: one model call over a question's excerpts → a JSON answer with cited claims.
Spec: experiments/V2_GENERATION.md §3. Guide: docs/V2_GENERATION.md.

  chunks ─► prompt (prompts/answer.md) ─► one call
  ─► parse JSON (one "only the JSON" retry) ─► each claim checked against its cited chunks

The chunks are V1's (or, in the V2 experiment, an oracle's or none); every generation decision
is here and in the prompt. Claims are normalized and checked in answers.py and verify.py.
"""

import time
from dataclasses import dataclass
from pathlib import Path

from tenk_agent.generation.answers import corpus_table, finalize, unparsed
from tenk_agent.models import Model, parse_json
from tenk_agent.prompts import PROMPTS
from tenk_agent.retrieval.index import ITEM_NAMES
from tenk_agent.store import ChunkRecord, Store

PROMPT = PROMPTS / "answer.md"

# --- Prompt and call ---------------------------------------------------------------------


def format_chunks(chunks: list[ChunkRecord], store: Store) -> str:
    """The excerpts as the model sees them: each chunk tagged with its id, under a line naming
    company, fiscal year, period end, Item, kind and units."""
    parts = []
    for c in chunks:
        filing = store.filing(c.ticker, c.fiscal_year)
        period = f", period ended {filing.period_end_date}" if filing else ""
        item = f"Item {c.item} {ITEM_NAMES.get(c.item, '')}".strip()
        units = f", {c.units}" if c.units else ""
        parts.append(
            f'<chunk id="{c.id}">\n{c.ticker} FY{c.fiscal_year} 10-K{period}, {item}'
            f" ({c.kind}{units})\n{c.text}\n</chunk>"
        )
    return "\n\n".join(parts)


def build_prompt(question: str, chunks: list[ChunkRecord], store: Store, prompt: Path = PROMPT):
    """The system prompt (rules, corpus table, answer format) and the one user message:
    excerpts, then the question. No chunks: the excerpts read "(none)"."""
    system = prompt.read_text().replace("{corpus}", corpus_table(store))
    excerpts = format_chunks(chunks, store) if chunks else "(none)"
    return system, f"Excerpts:\n\n{excerpts}\n\nQuestion: {question}"


@dataclass
class Generation:
    answer: dict  # finalize(): answer, claims (with status), table, scope_notice, abstained, …
    replies: list[str]  # raw model text, one per call
    seconds: float
    input_tokens: int
    output_tokens: int


def generate(
    model: Model, system: str, user: str, store: Store, max_tokens: int = 4000
) -> Generation:
    """One call; if the reply isn't JSON, one "reply with only the JSON" retry; then each
    claim is normalized and checked against the chunks it cites."""
    messages = [{"role": "user", "content": user}]
    replies, seconds, tokens_in, tokens_out = [], 0.0, 0, 0
    raw = None
    for _ in range(2):
        start = time.perf_counter()
        response = model.complete(messages, system=system, max_tokens=max_tokens)
        seconds += time.perf_counter() - start
        tokens_in += response.input_tokens
        tokens_out += response.output_tokens
        replies.append(response.text)
        try:
            raw = parse_json(response.text)
            break
        except ValueError:
            messages += [
                response.message(),
                {"role": "user", "content": "Reply again with only the JSON object."},
            ]
    answer = finalize(raw if raw is not None else unparsed(replies[-1]), store)
    answer.pop("citations")  # chunk texts, kept in the store
    return Generation(answer, replies, round(seconds, 3), tokens_in, tokens_out)
