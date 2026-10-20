"""V2 generation: one model call over a question's excerpts → a JSON answer with cited claims.
Spec: experiments/V2_GENERATION.md §3.

  context (V1's top k, the oracle, or none) ─► prompt (v2_prompt/system.md) ─► one call
  ─► parse JSON (one "only the JSON" retry) ─► each claim checked against its cited chunks

The retrieval is V1's, unchanged; every generation decision is here and in the prompt. The
chunk rendering, corpus table, claim normalization and claim check (value found in a cited
chunk) come from tenk_agent.
"""

import time
from dataclasses import dataclass
from pathlib import Path

from tenk_agent.answers import corpus_table, finalize, unparsed
from tenk_agent.ask import format_chunks
from tenk_agent.models import Model, parse_json
from tenk_agent.store import ChunkRecord, Store

# --- Context: which chunks the model sees ------------------------------------------------


def oracle_context(
    retrieved: list[ChunkRecord],
    per_source: list[set[str]],
    primary: list[list[str]],
    store: Store,
    k: int,
) -> list[ChunkRecord]:
    """V1's top k with every gold source it missed swapped in: the source's own chunk takes
    the place of the lowest-ranked chunk that is no source's gold. Questions V1 already
    answers in full get the same context as `retrieved`, so the two differ only where
    retrieval failed. `per_source`: each source's gold chunks (restatements included);
    `primary`: the chunks holding the source's own evidence quote."""
    chunks = list(retrieved[:k])
    gold = set().union(*per_source) if per_source else set()
    for ids, own in zip(per_source, primary, strict=True):
        if not own or any(c.id in ids for c in chunks):
            continue
        add = store.get_chunk(own[0])
        drop = next((i for i in reversed(range(len(chunks))) if chunks[i].id not in gold), None)
        if len(chunks) < k or drop is None:  # room left, or every place is gold: go over k
            chunks.append(add)
        else:
            chunks[drop] = add
    return chunks


# --- Prompt and call ---------------------------------------------------------------------


def build_prompt(prompt: Path, question: str, chunks: list[ChunkRecord], store: Store):
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
