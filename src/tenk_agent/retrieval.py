"""Chunk retrieval: keyword (FTS5), dense (sqlite-vec), or hybrid, then an optional reranker.

Every search takes the same metadata filters (company, fiscal year, item). Comparative
questions depend on them: "R&D expense" filtered to Apple FY2024 beats hoping the top 5
happens to contain the right company-year.
"""

import logging
import re
from dataclasses import dataclass

from tenk_agent.config import Settings
from tenk_agent.corpus import COMPANIES, FISCAL_YEARS, resolve_company
from tenk_agent.embeddings import Embedder, Reranker, get_embedder, get_reranker
from tenk_agent.store import ChunkRecord, Store

log = logging.getLogger(__name__)

MODES = ("keyword", "dense", "hybrid")
RRF_K = 60  # standard reciprocal-rank-fusion constant
ITEM_NAMES = {
    "1": "Business",
    "1A": "Risk Factors",
    "7": "Management's Discussion and Analysis",
    "7A": "Market Risk",
    "8": "Financial Statements",
}


def chunk_header(chunk: ChunkRecord, company_name: str) -> str:
    """Context line prepended to chunk text for embedding and reranking (not for display)."""
    item = ITEM_NAMES.get(chunk.item, "")
    return (
        f"{company_name} ({chunk.ticker}) fiscal {chunk.fiscal_year} 10-K, Item {chunk.item} {item}"
    )


COMPANY_NAMES = {c.ticker: c.name for c in COMPANIES}


def embedding_text(chunk: ChunkRecord) -> str:
    return chunk_header(chunk, COMPANY_NAMES.get(chunk.ticker, chunk.ticker)) + "\n" + chunk.text


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


YEAR = re.compile(r"\b(?:(FY|fiscal(?:\s+year)?)\s*)?(20\d\d)\b", re.IGNORECASE)
YEAR_RANGE = re.compile(r"\b(20\d\d)\s*(?:-|–|—|to|through)\s*(20\d\d)\b")
LAST_N_YEARS = re.compile(r"\b(?:last|past) (?:three|3) (?:fiscal )?years\b", re.IGNORECASE)


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


def filing_years(store: Store, ticker: str, year: int, fiscal: bool) -> list[int]:
    """Filings a year can refer to. A bare calendar year also matches the fiscal year that
    mostly covers it: NVIDIA's FY2025 ended January 2025, so "2024" includes FY2025."""
    matches = [year] if store.filing(ticker, year) else []
    following = store.filing(ticker, year + 1)
    if not fiscal and following and int(following.period_end_date[5:7]) <= 3:
        matches.append(year + 1)
    return matches


@dataclass
class Retriever:
    store: Store
    mode: str = "hybrid"
    embedder: Embedder | None = None
    reranker: Reranker | None = None
    # Search each (company, filing) the question names separately, so a comparison gets
    # evidence from every filing it needs.
    slot_search: bool = True
    candidates: int = 40  # chunks fetched per method before fusion / reranking

    @classmethod
    def from_settings(cls, store: Store, settings: Settings, **overrides) -> "Retriever":
        mode = overrides.pop("mode", settings.retrieval)
        embedder = get_embedder(settings.embedder, settings.device) if mode != "keyword" else None
        if embedder and store.embedding_model() != embedder.name:
            raise RuntimeError(
                f"Database embeddings are {store.embedding_model()!r}, not {embedder.name!r}. "
                "Run `tenk embed`."
            )
        reranker = get_reranker(overrides.pop("reranker", settings.reranker), settings.device)
        overrides.setdefault("slot_search", settings.slot_search)
        overrides.setdefault("candidates", settings.candidates)
        return cls(store, mode, embedder, reranker, **overrides)

    @property
    def name(self) -> str:
        parts = [self.mode]
        if self.embedder and self.mode != "keyword":
            parts.append(self.embedder.name)
        if self.reranker:
            parts.append(f"rerank={self.reranker.name}")
        if not self.slot_search:
            parts.append("no-slot-search")
        return " ".join(parts)

    def slots(self, query: str) -> list[tuple[str | None, list[int] | None]]:
        """(ticker, fiscal years) pairs to search separately; [(None, None)] for one search."""
        tickers = companies_in(query)
        years = [(y, fiscal) for y, fiscal in years_in(query) if y in FISCAL_YEARS or not fiscal]
        if not tickers:
            return [(None, None)]
        if not years:
            return [(t, None) for t in tickers]
        slots = []
        for ticker in tickers:
            for year, fiscal in years:
                matches = filing_years(self.store, ticker, year, fiscal)
                if matches and (ticker, matches) not in slots:
                    slots.append((ticker, matches))
        return slots or [(t, None) for t in tickers]

    def search(
        self,
        query: str,
        tickers: list[str] | None = None,
        fiscal_years: list[int] | None = None,
        items: list[str] | None = None,
        k: int = 10,
        explain: list[dict] | None = None,
    ) -> list[ChunkRecord]:
        """Top-k chunks. Pass a list as `explain` to get each search's intermediate rankings."""
        if tickers or fiscal_years or not self.slot_search:
            return self._search(query, tickers, fiscal_years, items, k, explain)
        slots = self.slots(query)
        if len(slots) == 1:
            ticker, years = slots[0]
            return self._search(query, [ticker] if ticker else None, years, items, k, explain)
        per_slot = max(2, -(-k // len(slots)))  # ceil(k / slots), at least 2
        rankings = [
            self._search(query, [ticker] if ticker else None, years, items, per_slot, explain)
            for ticker, years in slots
        ]
        merged: list[ChunkRecord] = []
        for rank in range(per_slot):  # round-robin: every slot's best chunk first
            for ranking in rankings:
                if rank < len(ranking) and ranking[rank].id not in {c.id for c in merged}:
                    merged.append(ranking[rank])
        return merged[:k]

    def _search(self, query, tickers, fiscal_years, items, k, explain=None) -> list[ChunkRecord]:
        filters = {"tickers": tickers, "fiscal_years": fiscal_years, "items": items}
        size = max(self.candidates, k)
        rankings = {}
        if self.mode in ("keyword", "hybrid"):
            rankings["keyword"] = self.store.search(query, k=size, **filters)
        if self.mode in ("dense", "hybrid"):
            assert self.embedder is not None, "dense retrieval needs an embedder"
            vector = self.embedder.embed_query(query)
            rankings["dense"] = self.store.vector_search(vector, k=size, **filters)
        lists = list(rankings.values())
        candidates = _fuse(lists) if len(lists) > 1 else lists[0]

        scores = None
        if self.reranker and candidates:
            candidates = candidates[:size]
            scores = self.reranker.scores(query, [embedding_text(c) for c in candidates])
            order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)
            candidates, scores = [candidates[i] for i in order], [scores[i] for i in order]
        if explain is not None:
            explain.append(
                {
                    "filters": filters,
                    "ranks": {
                        name: {c.id: rank for rank, c in enumerate(ranking, 1)}
                        for name, ranking in rankings.items()
                    },
                    "results": [
                        (c, scores[i] if scores else None) for i, c in enumerate(candidates[:k])
                    ],
                }
            )
        return candidates[:k]


def _fuse(rankings: list[list[ChunkRecord]]) -> list[ChunkRecord]:
    """Reciprocal rank fusion: each list contributes 1 / (RRF_K + rank) per chunk."""
    scores: dict[str, float] = {}
    chunks: dict[str, ChunkRecord] = {}
    for ranking in rankings:
        for rank, chunk in enumerate(ranking):
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1 / (RRF_K + rank + 1)
            chunks[chunk.id] = chunk
    return [chunks[cid] for cid in sorted(scores, key=scores.get, reverse=True)]


def embed_corpus(store: Store, embedder: Embedder, batch: int = 256) -> int:
    """Embed every chunk that has no vector yet; re-embeds everything if the model changed."""
    if store.embedding_model() != embedder.name:
        dimensions = len(embedder.embed_query("dimension probe"))
        store.reset_embeddings(embedder.name, dimensions)
    pending = store.chunks_without_embeddings()
    for start in range(0, len(pending), batch):
        group = pending[start : start + batch]
        vectors = embedder.embed_documents([embedding_text(c) for c in group])
        store.add_embeddings([c.id for c in group], vectors)
        log.info("Embedded %d/%d chunks", min(start + batch, len(pending)), len(pending))
    return len(pending)
