"""The agent's four tools (plus the optional V5 structured-data tool).

Schemas are defined once here as ToolSpecs; the model layer translates them per provider.
Every tool returns JSON text. Errors come back as tool results the model can correct,
never as exceptions that end the run.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from tenk_agent.calculator import CalculatorError, calculate
from tenk_agent.corpus import COMPANIES, FISCAL_YEARS, resolve_company
from tenk_agent.models import ToolSpec
from tenk_agent.retrieval import ITEM_NAMES, Retriever
from tenk_agent.store import ChunkRecord, Store
from tenk_agent.verify import find_text, find_value

MAX_K = 20

SEARCH_FILINGS = ToolSpec(
    name="search_filings",
    description=(
        "Search passages and tables in the corpus 10-K filings. Returns ranked chunks with "
        "chunk IDs, company, fiscal year and 10-K Item. Use the filters: searching 'research "
        "and development expense' filtered to one company and fiscal year beats an unfiltered "
        "search. Each filing's financial tables show the current and prior fiscal years."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to look for, in plain words."},
            "companies": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Company names or tickers, e.g. ['Apple', 'MSFT'].",
            },
            "fiscal_years": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Filing fiscal years (the filer's own label), e.g. [2024].",
            },
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "description": "10-K Items, e.g. ['7'] for MD&A, ['8'] for financial "
                "statements, ['1A'] for risk factors.",
            },
            "k": {"type": "integer", "description": "Number of results (default 8, max 20)."},
        },
        "required": ["query"],
    },
)

GET_FILING_SECTION = ToolSpec(
    name="get_filing_section",
    description=(
        "Read one 10-K section in full, in order, as chunks with IDs. Long sections are "
        "paged: pass `start` from the previous result's `next_start` to continue."
    ),
    parameters={
        "type": "object",
        "properties": {
            "company": {"type": "string", "description": "Company name or ticker."},
            "fiscal_year": {"type": "integer", "description": "Filing fiscal year."},
            "item": {"type": "string", "description": "10-K Item, e.g. '7', '1A', '8'."},
            "start": {"type": "integer", "description": "Chunk to start from (default 0)."},
        },
        "required": ["company", "fiscal_year", "item"],
    },
)

CALCULATOR = ToolSpec(
    name="calculator",
    description=(
        "Do all arithmetic here; never calculate in your head. Either a named op with its "
        "inputs — pct_change {old, new}, cagr {start, end, years}, ratio {numerator, "
        "denominator}, percent_of {part, total}, difference {a, b}, sum {any names} — or an "
        "`expression` over named `inputs`, e.g. '(a + b) / c'. Returns the result with the "
        "formula. Input numbers must be copied from tool results, in one consistent unit."
    ),
    parameters={
        "type": "object",
        "properties": {
            "op": {
                "type": "string",
                "enum": ["pct_change", "cagr", "ratio", "percent_of", "difference", "sum"],
            },
            "expression": {"type": "string"},
            "inputs": {
                "type": "object",
                "additionalProperties": {"type": "number"},
                "description": "Named numeric inputs, e.g. {'old': 29915, 'new': 31370}.",
            },
            "input_unit": {
                "type": "string",
                "description": "Unit of the inputs, e.g. 'USD millions' (echoed back).",
            },
        },
        "required": ["inputs"],
    },
)

VERIFY_CITATION = ToolSpec(
    name="verify_citation",
    description=(
        "Check that a chunk actually contains a value or quote before citing it. Numbers are "
        "matched after unit normalization (1,234 in a table 'in millions' matches 1.234 "
        "USD billions). Returns found / not found and the matched span."
    ),
    parameters={
        "type": "object",
        "properties": {
            "chunk_id": {"type": "string"},
            "value": {"type": "number", "description": "A number to find."},
            "unit": {
                "type": "string",
                "description": "Unit of `value`: 'USD millions', 'USD billions', 'USD', "
                "'percent', 'count', ...",
            },
            "quote": {"type": "string", "description": "Or: exact text to find."},
        },
        "required": ["chunk_id"],
    },
)

MVP_TOOLS = [SEARCH_FILINGS, GET_FILING_SECTION, CALCULATOR, VERIFY_CITATION]


class ToolError(ValueError):
    pass


@dataclass
class Toolbox:
    store: Store
    retriever: Retriever
    structured_data: bool = False  # V5 experiment only
    data_dir: Path = field(default_factory=lambda: Path("data"))
    section_chars: int = 24_000  # one page of get_filing_section output

    @classmethod
    def from_settings(cls, store: Store, retriever: Retriever, settings) -> "Toolbox":
        return cls(
            store, retriever, settings.structured_data, settings.data_dir, settings.section_chars
        )

    @property
    def specs(self) -> list[ToolSpec]:
        if self.structured_data:
            from tenk_agent.xbrl import GET_FINANCIAL_FACTS

            return [*MVP_TOOLS, GET_FINANCIAL_FACTS]
        return MVP_TOOLS

    def run(self, name: str, arguments: dict) -> tuple[str, bool]:
        """(JSON result, is_error). Unknown tools and bad arguments are errors, not crashes."""
        handlers = {
            "search_filings": self.search_filings,
            "get_filing_section": self.get_filing_section,
            "calculator": self.calculator,
            "verify_citation": self.verify_citation,
        }
        if self.structured_data:
            handlers["get_financial_facts"] = self.get_financial_facts
        if name not in handlers:
            return json.dumps({"error": f"unknown tool {name!r}"}), True
        try:
            return json.dumps(handlers[name](**arguments)), False
        except (ToolError, CalculatorError) as e:
            return json.dumps({"error": str(e)}), True
        except TypeError as e:  # unexpected or missing arguments
            return json.dumps({"error": f"bad arguments: {e}"}), True

    def search_filings(self, query, companies=None, fiscal_years=None, items=None, k=8):
        tickers = [_ticker(c) for c in companies] if companies else None
        years = [_year(y) for y in fiscal_years] if fiscal_years else None
        k = max(1, min(int(k), MAX_K))
        chunks = self.retriever.search(query, tickers, years, items or None, k=k)
        return {"results": [self._chunk_json(c) for c in chunks]}

    def get_filing_section(self, company, fiscal_year, item, start=0):
        ticker, year = _ticker(company), _year(fiscal_year)
        chunks = self.store.get_section(ticker, year, str(item))
        if not chunks:
            items = sorted(self.store.section_titles(ticker, year))
            raise ToolError(f"{ticker} FY{year} has no Item {item}; available: {items}")
        page, size = [], 0
        for chunk in chunks[start:]:
            if page and size + len(chunk.text) > self.section_chars:
                break
            page.append({"chunk_id": chunk.id, "kind": chunk.kind, "text": chunk.text})
            size += len(chunk.text)
        end = start + len(page)
        filing = self.store.filing(ticker, year)
        return {
            "company": ticker,
            "fiscal_year": year,
            "period_end_date": filing.period_end_date if filing else None,
            "item": str(item).upper(),
            "title": self.store.section_titles(ticker, year).get(str(item).upper()),
            "chunks": page,
            "next_start": end if end < len(chunks) else None,
            "total_chunks": len(chunks),
        }

    def calculator(self, inputs, op=None, expression=None, input_unit=None):
        result = calculate(op, expression, inputs).to_dict()
        return result | {"op": op, "expression": expression, "input_unit": input_unit}

    def verify_citation(self, chunk_id, value=None, unit=None, quote=None):
        chunk = self.store.get_chunk(chunk_id)
        if chunk is None:
            raise ToolError(f"no chunk {chunk_id!r}")
        if value is None and not quote:
            raise ToolError("give a value or a quote to look for")
        if value is not None:
            span = find_value(float(value), unit, chunk)
            return {"chunk_id": chunk_id, "found": span is not None, "matched_span": span}
        found = find_text(quote, chunk)
        return {"chunk_id": chunk_id, "found": found, "matched_span": quote if found else None}

    def get_financial_facts(self, company, fiscal_year, metrics=None):
        from tenk_agent.xbrl import financial_facts

        ticker, year = _ticker(company), _year(fiscal_year)
        return financial_facts(self.data_dir, self.store, ticker, year, metrics)

    def _chunk_json(self, chunk: ChunkRecord) -> dict:
        filing = self.store.filing(chunk.ticker, chunk.fiscal_year)
        return {
            "chunk_id": chunk.id,
            "company": chunk.ticker,
            "fiscal_year": chunk.fiscal_year,
            "period_end_date": filing.period_end_date if filing else None,
            "item": f"{chunk.item} {ITEM_NAMES.get(chunk.item, '')}".strip(),
            "kind": chunk.kind,
            "units": chunk.units,
            "text": chunk.text,
        }


def _ticker(name: str) -> str:
    company = resolve_company(str(name))
    if company is None:
        corpus = ", ".join(f"{c.name} ({c.ticker})" for c in COMPANIES)
        raise ToolError(f"{name!r} is not in the corpus. The corpus covers: {corpus}")
    return company.ticker


def _year(year) -> int:
    try:
        year = int(year)
    except (TypeError, ValueError) as e:
        raise ToolError(f"fiscal_year must be a year, not {year!r}") from e
    if year not in FISCAL_YEARS:
        raise ToolError(
            f"FY{year} is outside the corpus, which has 10-Ks for fiscal years "
            f"{FISCAL_YEARS[0]}–{FISCAL_YEARS[-1]} only"
        )
    return year
