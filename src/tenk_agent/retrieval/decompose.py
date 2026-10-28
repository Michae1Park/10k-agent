"""V1 query decomposition: a question → sub-questions, one per company and filing it names.

Rule-based, so V1 needs no LLM: companies and years come from the question's words. A question
naming several companies gets one sub-question per company, rewritten to name only that
company. A question naming none, but asking which company, can fan out to every company in the
corpus. Spec: experiments/V1_RETRIEVAL.md §3.
"""

import re
from dataclasses import dataclass

from tenk_agent.corpus import FISCAL_YEARS, resolve_company
from tenk_agent.store import Store

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
    periods: list[str] | None = None  # V4: 2026Q2, FY2025 (the agent's search_filings)
    forms: list[str] | None = None  # V4: 10-K, 10-Q, ER


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

    def decompose(
        self,
        question: str,
        tickers: list[str] | None = None,
        fiscal_years: list[int] | None = None,
    ) -> list[SubQuestion]:
        """tickers, fiscal_years: given by the caller (the agent's search_filings), they replace
        the companies and years found in the question; years are then taken as fiscal years,
        exactly. Neither given: V1 as is."""
        tickers = tickers or None
        if tickers is None and not companies_in(question):
            if self.fan_out and ASKS_WHICH_COMPANY.search(question):
                tickers = sorted({f.ticker for f in self.store.filings()})
        pairs = slots(self.store, question, tickers)
        if fiscal_years:
            pairs = [(t, list(fiscal_years)) for t in dict.fromkeys(t for t, _ in pairs)]
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
            filings = self.store.filings()
            name = next((f.company_name for f in filings if f.ticker == ticker), ticker)
            text = f"{name} ({ticker}): {text}"
        return text
