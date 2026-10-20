"""V1 retrieval pipeline, frozen for the experiment: a copy of tenk_agent.retrieval with one
path and no switches. Spec: experiments/V1_RETRIEVAL.md §2.

  question ─► names ─► slots ─► per slot: keyword (BM25) + dense ─► RRF ─► rerank ─► round-robin ─► top k

The store (SQLite, FTS5, sqlite-vec), company aliases and model loaders come from tenk_agent;
every pipeline decision is here.

On a parent-child database (V0 method P) the same steps run over the children, and each
reranked child is swapped for its parent, best child first, each parent once (as V7 does).
"""

import re
from dataclasses import dataclass, field

from tenk_agent.corpus import FISCAL_YEARS, resolve_company
from tenk_agent.embeddings import Embedder, Reranker
# the header + text the stored vectors were built from
from tenk_agent.retrieval import child_embedding_text, embedding_text
from tenk_agent.store import ChunkRecord, Store

# --- 1. Names: companies and years in the question ---------------------------------------

YEAR = re.compile(r"\b(?:(FY|fiscal(?:\s+year)?)\s*)?(20\d\d)\b", re.IGNORECASE)
YEAR_RANGE = re.compile(r"\b(20\d\d)\s*(?:-|–|—|to|through)\s*(20\d\d)\b")
LAST_N_YEARS = re.compile(r"\b(?:last|past) (?:three|3) (?:fiscal )?years\b", re.IGNORECASE)


def companies_in(question: str) -> list[str]:
    """Corpus tickers named in a question, by ticker, name or alias ("Google" -> GOOGL)."""
    words = re.findall(r"[A-Za-z][\w.&'-]*", question)
    found = []
    for size in (3, 2, 1):
        for i in range(len(words) - size + 1):
            company = resolve_company(" ".join(words[i : i + size]))
            if company and company.ticker not in found:
                found.append(company.ticker)
    return found


def years_in(question: str) -> list[tuple[int, bool]]:
    """(year, stated as a fiscal year) for each year named in a question, ranges expanded."""
    years: dict[int, bool] = {}
    for start, end in YEAR_RANGE.findall(question):
        for year in range(int(start), int(end) + 1):
            years.setdefault(year, False)
    for prefix, year in YEAR.findall(question):
        years[int(year)] = years.get(int(year), False) or bool(prefix)
    if LAST_N_YEARS.search(question):
        for year in FISCAL_YEARS:
            years.setdefault(year, True)
    return sorted(years.items())


# --- 2. Slots: one (company, filings) pair per search -------------------------------------


def filing_years(store: Store, ticker: str, year: int, fiscal: bool) -> list[int]:
    """Filings a year can refer to. A bare calendar year also matches the fiscal year that
    mostly covers it: NVIDIA's FY2025 ended January 2025, so "2024" includes FY2025."""
    matches = [year] if store.filing(ticker, year) else []
    following = store.filing(ticker, year + 1)
    if not fiscal and following and int(following.period_end_date[5:7]) <= 3:
        matches.append(year + 1)
    return matches


def slots(store: Store, question: str) -> list[tuple[str | None, list[int] | None]]:
    """(ticker, fiscal years) pairs to search separately; [(None, None)] for one search."""
    tickers = companies_in(question)
    years = [(y, fiscal) for y, fiscal in years_in(question) if y in FISCAL_YEARS or not fiscal]
    if not tickers:
        return [(None, None)]
    if not years:
        return [(t, None) for t in tickers]
    found = []
    for ticker in tickers:
        for year, fiscal in years:
            matches = filing_years(store, ticker, year, fiscal)
            if matches and (ticker, matches) not in found:
                found.append((ticker, matches))
    return found or [(t, None) for t in tickers]


# --- 3–5. Retrieve, fuse, rerank (per slot); merge ----------------------------------------


@dataclass
class Search:
    """One slot's search, kept for the report."""

    ticker: str | None
    fiscal_years: list[int] | None
    keyword: list[str]  # chunk ids, BM25 order
    dense: list[str]  # chunk ids, cosine order
    reranked: list[tuple[str, float]]  # (chunk id, rerank score), best first


@dataclass
class Result:
    chunks: list[ChunkRecord]
    searches: list[Search] = field(default_factory=list)


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
class Pipeline:
    store: Store
    embedder: Embedder
    reranker: Reranker
    candidates: int = 40
    rrf_k: int = 60
    slot_search: bool = True

    def search(self, question: str, k: int) -> Result:
        found = slots(self.store, question) if self.slot_search else [(None, None)]
        result = Result([])
        if len(found) == 1:
            ticker, years = found[0]
            result.chunks = self._search(question, ticker, years, k, result.searches)
            return result
        per_slot = max(2, -(-k // len(found)))  # ceil(k / slots), at least 2
        rankings = [
            self._search(question, ticker, years, per_slot, result.searches)
            for ticker, years in found
        ]
        seen: set[str] = set()
        for rank in range(per_slot):  # round-robin: every slot's best chunk first
            for ranking in rankings:
                if rank < len(ranking) and ranking[rank].id not in seen:
                    seen.add(ranking[rank].id)
                    result.chunks.append(ranking[rank])
        result.chunks = result.chunks[:k]
        return result

    def _search(self, question, ticker, years, k, searches: list[Search]) -> list[ChunkRecord]:
        filters = {"tickers": [ticker] if ticker else None, "fiscal_years": years}
        size = max(self.candidates, k)
        children = self.store.has_children()
        search = self.store.child_search if children else self.store.search
        vector_search = self.store.child_vector_search if children else self.store.vector_search
        text = child_embedding_text if children else embedding_text
        keyword = search(question, k=size, **filters)
        dense = vector_search(self.embedder.embed_query(question), k=size, **filters)
        candidates = fuse([keyword, dense], self.rrf_k)[:size]
        scores = self.reranker.scores(question, [text(c) for c in candidates])
        order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)
        reranked = [(candidates[i], float(scores[i])) for i in order]
        if children:
            reranked = [(self.store.get_chunk(c.parent_id), s) for c, s in _first(reranked)]
            keyword, dense = [_parents(self.store, ranking) for ranking in (keyword, dense)]
        searches.append(
            Search(
                ticker,
                years,
                [c.id for c in keyword],
                [c.id for c in dense],
                [(c.id, s) for c, s in reranked],
            )
        )
        return [c for c, _ in reranked[:k]]


def _first(ranked: list[tuple]) -> list[tuple]:
    """Each parent once, at its best child's place (children already best first)."""
    seen: set[str] = set()
    return [(c, s) for c, s in ranked if not (c.parent_id in seen or seen.add(c.parent_id))]


def _parents(store: Store, children: list) -> list[ChunkRecord]:
    return [store.get_chunk(c.parent_id) for c, _ in _first([(c, 0.0) for c in children])]
