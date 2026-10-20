"""V1 retrieval pipeline: sub-question retrieval over a fixed hybrid search. Spec:
experiments/V1_RETRIEVAL.md §3.

  question ─► QueryDecomposer ─► sub-questions (one per company and filing)
           ─► HybridSearch per sub-question: keyword (BM25) + dense ─► RRF ─► rerank
           ─► round-robin merge ─► top k
  (SubQuestionRetriever runs the whole thing.)

Decomposition is rule-based, so V1 needs no LLM: companies and years come from the question's
words. A question naming several companies gets one sub-question per company, rewritten to
name only that company. A question naming none, but asking which company, can fan out to every
company in the corpus.

The store (SQLite, FTS5, sqlite-vec), company aliases and model loaders come from tenk_agent;
every pipeline decision is here. On a parent-child database (V0 method P) the search runs over
the children, and each reranked child is swapped for its parent, best child first, each parent
once (as V7 does).
"""

import re
from dataclasses import dataclass, field

from tenk_agent.corpus import FISCAL_YEARS, resolve_company
from tenk_agent.embeddings import Embedder, Reranker
from tenk_agent.retrieval import child_embedding_text, embedding_text  # what was embedded
from tenk_agent.store import ChunkRecord, Store

# --- Names: companies and years in the question ------------------------------------------

YEAR = re.compile(r"\b(?:(FY|fiscal(?:\s+year)?)\s*)?(20\d\d)\b", re.IGNORECASE)
YEAR_RANGE = re.compile(r"\b(20\d\d)\s*(?:-|–|—|to|through)\s*(20\d\d)\b")
LAST_N_YEARS = re.compile(r"\b(?:last|past) (?:three|3) (?:fiscal )?years\b", re.IGNORECASE)
WORD = re.compile(r"[A-Za-z][\w.&'-]*")


def companies_in(question: str) -> list[str]:
    """Corpus tickers named in a question, by ticker, name or alias ("Google" -> GOOGL)."""
    words = WORD.findall(question)
    found = []
    for size in (3, 2, 1):
        for i in range(len(words) - size + 1):
            company = resolve_company(" ".join(words[i : i + size]))
            if company and company.ticker not in found:
                found.append(company.ticker)
    return found


@dataclass
class Mention:
    start: int
    end: int
    ticker: str


def mentions(question: str) -> list[Mention]:
    """Where each company is named, longest name first ("Amazon Web Services" over "Amazon")."""
    words = list(WORD.finditer(question))
    found: list[Mention] = []
    for size in (3, 2, 1):
        for i in range(len(words) - size + 1):
            start, end = words[i].start(), words[i + size - 1].end()
            if any(m.start < end and start < m.end for m in found):
                continue
            company = resolve_company(question[start:end].rstrip(".,"))
            if company:
                found.append(Mention(start, end, company.ticker))
    return sorted(found, key=lambda m: m.start)


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


def slots(
    store: Store, question: str, tickers: list[str] | None = None
) -> list[tuple[str | None, list[int] | None]]:
    """(ticker, fiscal years) pairs to search separately; [(None, None)] for one search.
    `tickers` overrides the companies the question names (fan-out)."""
    tickers = companies_in(question) if tickers is None else tickers
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


# --- Query decomposition: question → sub-questions ---------------------------------------

ASKS_WHICH_COMPANY = re.compile(
    r"\b(which|what)\s+(of\s+the\s+)?(compan(y|ies)|firms?)\b|\bwhose\b", re.IGNORECASE
)
LIST_GLUE = re.compile(r"^\s*(,|,?\s*and|,?\s*or|&)\s*$")  # between names in a list
DANGLING = re.compile(r"\b(of|for|between|from|by)\s+(?=[\s:?.,]|$)", re.IGNORECASE)


@dataclass
class SubQuestion:
    """One search: the text to search with, and the company and filings to filter to."""

    text: str
    ticker: str | None
    fiscal_years: list[int] | None


@dataclass
class QueryDecomposer:
    """Question → sub-questions, one per (company, filing) the question names.

    rewrite: a sub-question names only its own company. The other companies in the question
        are taken out, so the search and the reranker match that company's text, not a
        question about several.
    fan_out: a question that names no company but asks which company ("which company…",
        "whose…") gets one sub-question per company in the corpus. Fine for a few companies;
        a large corpus needs structured data for this instead.
    """

    store: Store
    rewrite: bool = True
    fan_out: bool = True

    def decompose(self, question: str) -> list[SubQuestion]:
        named = companies_in(question)
        tickers = None
        if not named and self.fan_out and ASKS_WHICH_COMPANY.search(question):
            tickers = sorted({f.ticker for f in self.store.filings()})
        pairs = slots(self.store, question, tickers)
        several = len({t for t, _ in pairs}) > 1
        return [
            SubQuestion(
                self.for_company(question, ticker) if self.rewrite and several else question,
                ticker,
                years,
            )
            for ticker, years in pairs
        ]

    def for_company(self, question: str, ticker: str) -> str:
        """The question as asked of one company: other companies' names taken out of lists
        ("Apple, Amazon and NVIDIA" → "Amazon"); the company's name put in front when the
        question doesn't name it (fan-out)."""
        found = mentions(question)
        runs: list[list[Mention]] = []  # names joined by commas / and / or
        for m in found:
            if runs and LIST_GLUE.match(question[runs[-1][-1].end : m.start]):
                runs[-1].append(m)
            else:
                runs.append([m])
        out, pos = [], 0
        for run in runs:
            out.append(question[pos : run[0].start])
            own = [m for m in run if m.ticker == ticker]
            out.append(" and ".join(question[m.start : m.end] for m in own))
            pos = run[-1].end
        out.append(question[pos:])
        text = DANGLING.sub("", "".join(out))
        text = re.sub(r"\s+([,:?.])", r"\1", re.sub(r"\s{2,}", " ", text)).strip()
        text = re.sub(r":\s*\?$", "?", text)
        if not any(m.ticker == ticker for m in found):
            filing = self.store.filings()
            name = next((f.company_name for f in filing if f.ticker == ticker), ticker)
            text = f"{name} ({ticker}): {text}"
        return text


# --- Hybrid search: one sub-question ------------------------------------------------------


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
        }
        size = max(self.candidates, k)
        children = self.store.has_children()
        keyword_search = self.store.child_search if children else self.store.search
        vector_search = self.store.child_vector_search if children else self.store.vector_search
        text = child_embedding_text if children else embedding_text
        keyword = keyword_search(sub.text, k=size, **filters)
        dense = vector_search(self.embedder.embed_query(sub.text), k=size, **filters)
        candidates = fuse([keyword, dense], self.rrf_k)[:size]
        scores = self.reranker.scores(sub.text, [text(c) for c in candidates])
        order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)
        reranked = [(candidates[i], float(scores[i])) for i in order]
        if children:
            reranked = [(self.store.get_chunk(c.parent_id), s) for c, s in _first(reranked)]
            keyword, dense = [_parents(self.store, ranking) for ranking in (keyword, dense)]
        log = Search(
            sub.text,
            sub.ticker,
            sub.fiscal_years,
            [c.id for c in keyword],
            [c.id for c in dense],
            [(c.id, s) for c, s in reranked],
        )
        return [c for c, _ in reranked[:k]], log


def _first(ranked: list[tuple]) -> list[tuple]:
    """Each parent once, at its best child's place (children already best first)."""
    seen: set[str] = set()
    return [(c, s) for c, s in ranked if not (c.parent_id in seen or seen.add(c.parent_id))]


def _top_score(search: Search) -> float:
    return search.reranked[0][1] if search.reranked else float("-inf")


def _parents(store: Store, children: list) -> list[ChunkRecord]:
    return [store.get_chunk(c.parent_id) for c, _ in _first([(c, 0.0) for c in children])]


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

    def search(self, question: str, k: int) -> Result:
        subs = (
            self.decomposer.decompose(question)
            if self.slot_search
            else [SubQuestion(question, None, None)]
        )
        result = Result([])
        per_sub = k if len(subs) == 1 else max(2, -(-k // len(subs)))
        searched = []
        for sub in subs:
            chunks, log = self.hybrid.search(sub, per_sub)
            searched.append((chunks, log))
            result.searches.append(log)
        # Round-robin, the best-matching sub-question first in every round.
        searched.sort(key=lambda pair: _top_score(pair[1]), reverse=True)
        rankings = [chunks for chunks, _ in searched]
        seen: set[str] = set()
        for rank in range(per_sub):
            for ranking in rankings:
                if rank < len(ranking) and ranking[rank].id not in seen:
                    seen.add(ranking[rank].id)
                    result.chunks.append(ranking[rank])
        result.chunks = result.chunks[:k]
        return result
