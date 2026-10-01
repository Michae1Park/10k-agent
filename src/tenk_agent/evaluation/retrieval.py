"""Retrieval metrics: Recall@k and MRR against gold chunks.

Gold chunks are resolved at evaluation time from each source's verbatim evidence quote
(gold.resolve_evidence), so re-chunking never invalidates the gold set.
"""

from dataclasses import dataclass

from tenk_agent import gold
from tenk_agent.store import Store


@dataclass
class GoldChunks:
    """Per primary source, the chunk IDs that satisfy it (its own, or an acceptable alternative)."""

    per_source: list[set[str]]

    @property
    def all(self) -> set[str]:
        return set().union(*self.per_source) if self.per_source else set()


def gold_chunks(store: Store, q: dict) -> GoldChunks:
    """An acceptable source (the same figure in a later filing) counts for the primary source
    it restates, matched by item and section name."""
    sources = q.get("sources", [])
    alternatives = q.get("acceptable_sources", [])
    resolved = gold.resolve_evidence(store, q)
    primary, extra = resolved[: len(sources)], resolved[len(sources) :]
    per_source = []
    for source, chunk_ids in zip(sources, primary, strict=True):
        satisfying = set(chunk_ids)
        for alt, alt_ids in zip(alternatives, extra, strict=True):
            if (alt["item"], alt["section"]) == (source["item"], source["section"]):
                satisfying |= set(alt_ids)
        per_source.append(satisfying)
    return GoldChunks(per_source)


def recall_at_k(retrieved: list[str], gold_set: GoldChunks, k: int) -> float:
    """Share of primary sources with at least one satisfying chunk in the top k."""
    if not gold_set.per_source:
        return 0.0
    top = set(retrieved[:k])
    return sum(bool(top & chunks) for chunks in gold_set.per_source) / len(gold_set.per_source)


def reciprocal_rank(retrieved: list[str], gold_set: GoldChunks) -> float:
    relevant = gold_set.all
    for rank, chunk_id in enumerate(retrieved, 1):
        if chunk_id in relevant:
            return 1 / rank
    return 0.0


def is_scorable(store: Store, q: dict) -> bool:
    """Retrieval is scored only for questions with sources whose evidence resolves."""
    gold_set = gold_chunks(store, q)
    return bool(gold_set.per_source) and all(gold_set.per_source)
