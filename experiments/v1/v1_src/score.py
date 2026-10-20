"""V1 metrics: Recall@k and MRR against the gold chunks. Spec: experiments/V1_RETRIEVAL.md §3.

Gold chunks come from each source's verbatim evidence quote, resolved against the database
(tenk_agent.evaluation.retrieval.gold_chunks), so they follow whatever chunking the db holds.
"""

from tenk_agent.evaluation.retrieval import GoldChunks


def recall_at(retrieved: list[str], gold: GoldChunks, k: int) -> float:
    """Share of gold sources with at least one satisfying chunk in the top k."""
    top = set(retrieved[:k])
    return sum(bool(top & ids) for ids in gold.per_source) / len(gold.per_source)


def reciprocal_rank(retrieved: list[str], gold: GoldChunks) -> float:
    """1 / rank of the first gold chunk in the list; 0 if none is retrieved."""
    relevant = gold.all
    for rank, chunk_id in enumerate(retrieved, 1):
        if chunk_id in relevant:
            return 1 / rank
    return 0.0


def first_gold_rank(retrieved: list[str], gold: GoldChunks) -> int | None:
    return next((r for r, cid in enumerate(retrieved, 1) if cid in gold.all), None)
