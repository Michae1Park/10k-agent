"""V1 metrics: Recall@k and MRR against the gold chunks. Spec: experiments/V1_RETRIEVAL.md §4.

Gold chunks come from each source's verbatim evidence quote, resolved against the database
(gold.resolve_evidence), so they follow whatever chunking the db holds. An acceptable
source names the primary source it restates (`restates`: its index), e.g. an MD&A table stating
the same figure as the Item 8 statement, and its chunks count for that source.
"""

from dataclasses import dataclass

from tenk_agent.evaluation import gold as gold_set
from tenk_agent.store import Store


@dataclass
class GoldChunks:
    """Per primary source, the chunk IDs that satisfy it (its own, or a restatement)."""

    per_source: list[set[str]]

    @property
    def all(self) -> set[str]:
        return set().union(*self.per_source) if self.per_source else set()


def gold_chunks(store: Store, q: dict) -> GoldChunks:
    """Per primary source, the chunks that contain it or a restatement of it."""
    sources = q.get("sources", [])
    resolved = gold_set.resolve_evidence(store, q)
    per_source = [set(ids) for ids in resolved[: len(sources)]]
    for alt, ids in zip(q.get("acceptable_sources", []), resolved[len(sources) :], strict=True):
        per_source[alt["restates"]] |= set(ids)
    return GoldChunks(per_source)


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
