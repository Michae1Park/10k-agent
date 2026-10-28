"""V1 retrieval pipeline: sub-question retrieval over a fixed hybrid search. Spec:
experiments/V1_RETRIEVAL.md §3. Spec: docs/stages/V1_RETRIEVAL.md.

  question ─► QueryDecomposer ─► sub-questions (one per company and filing)    decompose.py
           ─► HybridSearch per sub-question: keyword (BM25) + dense ─► RRF ─► rerank
           ─► round-robin merge ─► top k
  (SubQuestionRetriever runs the whole thing; from_settings builds it from the config.)

On a parent-child database (V0 method P) the search runs over the children, and each reranked
child is swapped for its parent, best child first, each parent once.
"""

from dataclasses import dataclass, field

from tenk_agent.retrieval.decompose import QueryDecomposer, SubQuestion
from tenk_agent.retrieval.embeddings import Embedder, Reranker, get_embedder, get_reranker
from tenk_agent.retrieval.index import embedding_text
from tenk_agent.store import ChildRecord, ChunkRecord, Store


@dataclass
class Search:
    """One sub-question's search, kept for the report."""

    query: str
    ticker: str | None
    fiscal_years: list[int] | None
    keyword: list[str]  # chunk ids, BM25 order
    dense: list[str]  # chunk ids, cosine order
    reranked: list[tuple[str, float]]  # (chunk id, rerank score), best first


def fuse(rankings: list[list[ChunkRecord]], rrf_k: int) -> list[ChunkRecord]:
    """Reciprocal rank fusion: each list contributes 1 / (rrf_k + rank) per chunk."""
    scores: dict[str, float] = {}
    chunks: dict[str, ChunkRecord] = {}
    for ranking in rankings:
        for rank, chunk in enumerate(ranking, 1):
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1 / (rrf_k + rank)
            chunks[chunk.id] = chunk
    return [chunks[cid] for cid in sorted(scores, key=scores.get, reverse=True)]


@dataclass
class HybridSearch:
    """keyword + dense → RRF → rerank, filtered to one sub-question's company and filings."""

    store: Store
    embedder: Embedder
    reranker: Reranker
    candidates: int = 40
    rrf_k: int = 60

    def search(self, sub: SubQuestion, k: int) -> tuple[list[ChunkRecord], Search]:
        filters = {
            "tickers": [sub.ticker] if sub.ticker else None,
            "fiscal_years": sub.fiscal_years,
        } | ({"periods": sub.periods, "forms": sub.forms} if sub.periods or sub.forms else {})
        size = max(self.candidates, k)
        children = self.store.has_children()
        keyword_search = self.store.child_search if children else self.store.search
        vector_search = self.store.child_vector_search if children else self.store.vector_search
        keyword = keyword_search(sub.text, k=size, **filters)
        dense = vector_search(self.embedder.embed_query(sub.text), k=size, **filters)
        candidates = fuse([keyword, dense], self.rrf_k)[:size]
        scores = self.reranker.scores(sub.text, [embedding_text(c) for c in candidates])
        order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)
        reranked = [(candidates[i], float(scores[i])) for i in order]
        if children:
            best = {}  # parent id → its best child's score (children are best first)
            for child, score in reranked:
                best.setdefault(child.parent_id, score)
            reranked = [(self.store.get_chunk(pid), score) for pid, score in best.items()]
            keyword, dense = _parents(self.store, keyword), _parents(self.store, dense)
        record = Search(
            sub.text,
            sub.ticker,
            sub.fiscal_years,
            [c.id for c in keyword],
            [c.id for c in dense],
            [(c.id, s) for c, s in reranked],
        )
        return [c for c, _ in reranked[:k]], record


def _parents(store: Store, children: list[ChildRecord]) -> list[ChunkRecord]:
    """Each child's parent, once, in the order of its first child."""
    return [store.get_chunk(pid) for pid in dict.fromkeys(c.parent_id for c in children)]


def _top_score(search: Search) -> float:
    return search.reranked[0][1] if search.reranked else float("-inf")


# --- Sub-question retrieval: the whole pipeline -------------------------------------------


@dataclass
class Result:
    chunks: list[ChunkRecord]
    searches: list[Search] = field(default_factory=list)


@dataclass
class SubQuestionRetriever:
    """Decompose, search each sub-question, and merge round-robin: every sub-question's best
    chunk first, then every sub-question's second, … Each gets ceil(k / sub-questions) places
    (at least 2), so every company the question needs is represented. Within a round, the
    sub-question whose best chunk scored highest goes first, so "which company…" questions
    put the matching company at the top.

    slot_search off: one unfiltered search with the question as asked.
    """

    decomposer: QueryDecomposer
    hybrid: HybridSearch
    slot_search: bool = True

    @classmethod
    def from_settings(cls, store: Store, settings: dict) -> "SubQuestionRetriever":
        """The pipeline as the config's `retrieval:` section sets it. The database's vectors
        must come from the configured embedder."""
        embedder = get_embedder(settings["embedder"], settings["device"])
        if store.embedding_model() != embedder.name:
            raise RuntimeError(
                f"the database's vectors are {store.embedding_model()!r}, not {embedder.name!r}"
            )
        decompose = settings["decompose"]
        return cls(
            QueryDecomposer(store, rewrite=decompose["rewrite"], fan_out=decompose["fan_out"]),
            HybridSearch(
                store,
                embedder,
                get_reranker(settings["reranker"], settings["device"]),
                candidates=settings["candidates"],
                rrf_k=settings["rrf_k"],
            ),
            slot_search=settings["slot_search"],
        )

    def search(
        self,
        question: str,
        k: int,
        tickers: list[str] | None = None,
        fiscal_years: list[int] | None = None,
        periods: list[str] | None = None,
        forms: list[str] | None = None,
    ) -> Result:
        """tickers, fiscal_years: replace what the question names (QueryDecomposer.decompose).
        periods, forms (V4): filter every sub-question; periods replace the years."""
        subs = (
            self.decomposer.decompose(question, tickers, fiscal_years)
            if self.slot_search
            else [SubQuestion(question, None, None)]
        )
        if periods or forms:
            subs = [
                SubQuestion(s.text, s.ticker, None if periods else s.fiscal_years, periods, forms)
                for s in subs
            ]
        per_sub = k if len(subs) == 1 else max(2, -(-k // len(subs)))
        searched = [self.hybrid.search(sub, per_sub) for sub in subs]
        # Round-robin, the best-matching sub-question first in every round.
        rankings = [
            chunks for chunks, _ in sorted(searched, key=lambda p: _top_score(p[1]), reverse=True)
        ]
        merged: dict[str, ChunkRecord] = {}
        for rank in range(per_sub):
            for ranking in rankings:
                if rank < len(ranking):
                    merged.setdefault(ranking[rank].id, ranking[rank])
        return Result(list(merged.values())[:k], [s for _, s in searched])
