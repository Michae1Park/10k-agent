"""Ask mode (V2): retrieve top-k chunks, rerank, then one LLM call that answers only from them."""

import time

from tenk_agent.answers import ANSWER_FORMAT, RULES, corpus_table, finalize, unparsed
from tenk_agent.models import Model, parse_json
from tenk_agent.retrieval import ITEM_NAMES, Retriever
from tenk_agent.store import Store
from tenk_agent.tracing import Trace

ASK_K = 8

SYSTEM = f"""You answer questions about SEC 10-K filings using only the excerpts provided.
Answer in 1–3 sentences, with citations.

{RULES}
- If the excerpts don't contain the answer, say what is missing instead of guessing, and set "abstained": true.

{{corpus}}

{ANSWER_FORMAT}"""


def format_chunks(chunks, store: Store) -> str:
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


def build_prompt(question: str, chunks, store: Store) -> tuple[str, list[dict]]:
    """The system prompt and the single user message: excerpts, then the question."""
    system = SYSTEM.replace("{corpus}", corpus_table(store))
    content = f"Excerpts:\n\n{format_chunks(chunks, store)}\n\nQuestion: {question}"
    return system, [{"role": "user", "content": content}]


def ask(
    question: str,
    store: Store,
    retriever: Retriever,
    model: Model,
    verify: bool = True,
    k: int = ASK_K,
) -> dict:
    trace = Trace("ask", question, model.name)
    start = time.monotonic()
    chunks = retriever.search(question, k=k)
    trace.retrieval(question, [c.id for c in chunks], time.monotonic() - start)

    system, messages = build_prompt(question, chunks, store)
    response = model.complete(messages, system=system, max_tokens=4000)
    trace.llm("answer", response, len(messages))
    try:
        raw = parse_json(response.text)
    except ValueError:
        # One repair attempt: the Ask pipeline is a single call unless the format breaks.
        messages += [
            response.message(),
            {"role": "user", "content": "Reply again with only the JSON object."},
        ]
        response = model.complete(messages, system=system, max_tokens=4000)
        trace.llm("repair", response, len(messages))
        try:
            raw = parse_json(response.text)
        except ValueError:
            raw = unparsed(response.text)

    result = finalize(raw, store, verify) | {
        "mode": "ask",
        "question": question,
        "model": model.name,
        "retrieved": [c.id for c in chunks],
        "trace_id": trace.id,
    }
    trace.output = {k: v for k, v in result.items() if k != "citations"}
    result["usage"] = trace.save(store)["totals"]
    return result
