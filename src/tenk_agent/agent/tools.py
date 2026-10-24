"""The V3 agent's tools: what each does, its schema, and the code that runs it.

  search_filings   V1's hybrid search (keyword + dense → RRF → rerank), one search per company
  get_section      one 10-K Item, in order, a page of chunks at a time
  calculator       generation/calculator.py: named operations or an expression over inputs
  xbrl_facts       SEC XBRL company facts for one concept and 10-K            (tool set "full")
  edgar_filings    live EDGAR filing metadata: which filings exist, when      (tool set "full")

Tools never raise: a bad call returns {"error": ...} for the model to read and recover from.
The tool set ("text" or "full") is the V3 ablation (experiments/V3_AGENT.md).
"""

import json
from dataclasses import dataclass

from tenk_agent.agent.sec import SecData, SecError
from tenk_agent.corpus import resolve_company
from tenk_agent.generation.calculator import CalculatorError, calculate
from tenk_agent.models import ToolSpec
from tenk_agent.retrieval.decompose import SubQuestion
from tenk_agent.retrieval.search import HybridSearch
from tenk_agent.store import ChunkRecord, Store

COMPANY = {"type": "string", "description": "Ticker or name, e.g. AAPL or Apple"}
YEARS = {"type": "array", "items": {"type": "integer"}, "description": "Fiscal years (10-Ks)"}

SPECS = {
    "search_filings": ToolSpec(
        "search_filings",
        "Search the 10-K excerpts in the corpus. Runs one search per company given, so a "
        "comparison gets results for every company. Returns chunks with their IDs to cite.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to find, e.g. 'total net sales'"},
                "companies": {"type": "array", "items": COMPANY},
                "fiscal_years": YEARS,
                "k": {"type": "integer", "description": "Chunks per company (default 4, max 8)"},
            },
            "required": ["query"],
        },
    ),
    "get_section": ToolSpec(
        "get_section",
        "Read one Item of a 10-K in order (e.g. 7 = MD&A, 8 = financial statements, "
        "1A = risk factors), a page of chunks at a time. Use it when a search hit is "
        "incomplete or the table you need is next to it.",
        {
            "type": "object",
            "properties": {
                "company": COMPANY,
                "fiscal_year": {"type": "integer"},
                "item": {"type": "string", "description": "Item number, e.g. '7', '8', '1A'"},
                "page": {"type": "integer", "description": "Page of the Item, from 1"},
            },
            "required": ["company", "fiscal_year", "item"],
        },
    ),
    "calculator": ToolSpec(
        "calculator",
        "Do arithmetic exactly. Give a named op (pct_change: old, new; cagr: start, end, "
        "years; ratio: numerator, denominator; percent_of: part, total; difference: a, b; "
        "sum: any inputs) or an expression over named inputs, e.g. '(a - b) / b * 100'. "
        "For reported figures only: not for projections or forecasts.",
        {
            "type": "object",
            "properties": {
                "op": {"type": "string"},
                "expression": {"type": "string"},
                "inputs": {"type": "object", "additionalProperties": {"type": "number"}},
            },
            "required": ["inputs"],
        },
    ),
    "xbrl_facts": ToolSpec(
        "xbrl_facts",
        "SEC XBRL financial data a company tagged in one 10-K: the values of one concept "
        "(e.g. us-gaap:Revenues, us-gaap:NetIncomeLoss, us-gaap:ResearchAndDevelopmentExpense) "
        "for the filing's year and the prior years it shows. Works for any SEC registrant. "
        "An unknown concept returns candidate concept names. Cite a value by its xbrl_id.",
        {
            "type": "object",
            "properties": {
                "company": COMPANY,
                "concept": {"type": "string", "description": "e.g. us-gaap:NetIncomeLoss"},
                "fiscal_year": {"type": "integer", "description": "The 10-K's fiscal year"},
            },
            "required": ["company", "concept", "fiscal_year"],
        },
    ),
    "edgar_filings": ToolSpec(
        "edgar_filings",
        "SEC EDGAR filing metadata for any registrant: which filings of a form exist, with "
        "filing dates, period ends, accession numbers and links. No financial data.",
        {
            "type": "object",
            "properties": {
                "company": COMPANY,
                "form": {"type": "string", "description": "e.g. 10-K (default), 10-Q, 8-K"},
                "fiscal_year": {"type": "integer"},
            },
            "required": ["company"],
        },
    ),
}

TOOL_SETS = {
    "text": ("search_filings", "get_section", "calculator"),
    "full": ("search_filings", "get_section", "calculator", "xbrl_facts", "edgar_filings"),
}


@dataclass
class Toolbox:
    store: Store
    search: HybridSearch | None  # None: search_filings unavailable (tests)
    sec: SecData | None
    tool_set: str = "full"
    section_chunks: int = 4  # chunks per get_section page

    @property
    def specs(self) -> list[ToolSpec]:
        return [SPECS[name] for name in TOOL_SETS[self.tool_set]]

    def call(self, name: str, arguments: dict) -> dict:
        if name not in TOOL_SETS[self.tool_set]:
            return {"error": f"unknown tool {name!r}; tools: {list(TOOL_SETS[self.tool_set])}"}
        arguments = _coerce(arguments, SPECS[name].parameters["properties"])
        try:
            return getattr(self, name)(**arguments)
        except (TypeError, AttributeError) as e:
            return {"error": f"bad arguments for {name}: {e}"}
        except (CalculatorError, SecError, ValueError) as e:
            return {"error": str(e)}

    # --- Corpus --------------------------------------------------------------------------

    def search_filings(
        self,
        query: str,
        companies: list[str] | None = None,
        fiscal_years: list[int] | None = None,
        k: int = 4,
    ) -> dict:
        if self.search is None:
            return {"error": "search is not available"}
        k = max(1, min(int(k), 8))
        tickers, unknown = self._tickers(companies or [])
        if unknown:
            return {"error": f"not in the corpus: {unknown}", "corpus": self._corpus()}
        results = []
        for ticker in tickers or [None]:
            chunks, _ = self.search.search(SubQuestion(query, ticker, fiscal_years or None), k)
            results.extend(chunk_view(c) for c in chunks)
        return {"query": query, "chunks": results}

    def get_section(self, company: str, fiscal_year: int, item: str, page: int = 1) -> dict:
        tickers, unknown = self._tickers([company])
        if unknown:
            return {"error": f"not in the corpus: {unknown}", "corpus": self._corpus()}
        ticker, item = tickers[0], str(item).upper().removeprefix("ITEM").strip()
        chunks = [c for c in self.store.chunks(ticker, int(fiscal_year)) if c.item == item]
        if not chunks:
            items = sorted(self.store.section_titles(ticker, int(fiscal_year)).items())
            return {"error": f"no Item {item} in {ticker} FY{fiscal_year}", "items": items}
        pages = -(-len(chunks) // self.section_chunks)
        page = max(1, min(int(page), pages))
        start = (page - 1) * self.section_chunks
        title = self.store.section_titles(ticker, int(fiscal_year)).get(item)
        return {
            "company": ticker,
            "fiscal_year": int(fiscal_year),
            "item": item,
            "title": title,
            "page": page,
            "pages": pages,
            "chunks": [chunk_view(c) for c in chunks[start : start + self.section_chunks]],
        }

    def _tickers(self, names: list[str]) -> tuple[list[str], list[str]]:
        corpus = {f.ticker for f in self.store.filings()}
        tickers, unknown = [], []
        for name in names:
            company = resolve_company(name)
            if company and company.ticker in corpus:
                tickers.append(company.ticker)
            else:
                unknown.append(name)
        return list(dict.fromkeys(tickers)), unknown

    def _corpus(self) -> list[str]:
        return [f"{f.ticker} FY{f.fiscal_year}" for f in self.store.filings()]

    # --- Calculator ----------------------------------------------------------------------

    def calculator(
        self, inputs: dict, op: str | None = None, expression: str | None = None
    ) -> dict:
        if op == "expression":  # models write the mode's name as the op
            op = None
        result = calculate(op, expression, inputs)
        return {"result": result.result, "formula": result.formula, "inputs": result.inputs}

    # --- SEC -----------------------------------------------------------------------------

    def xbrl_facts(self, company: str, concept: str, fiscal_year: int) -> dict:
        if self.sec is None:
            return {"error": "SEC data is not available"}
        return self.sec.xbrl_facts(company, concept, int(fiscal_year))

    def edgar_filings(self, company: str, form: str = "10-K", fiscal_year: int | None = None):
        if self.sec is None:
            return {"error": "SEC data is not available"}
        return self.sec.filings(company, form, int(fiscal_year) if fiscal_year else None)


def _coerce(arguments: dict, properties: dict) -> dict:
    """Object and array arguments the model wrote as JSON strings, parsed."""
    out = dict(arguments)
    for key, value in arguments.items():
        if properties.get(key, {}).get("type") in ("object", "array") and isinstance(value, str):
            try:
                out[key] = json.loads(value)
            except json.JSONDecodeError:
                pass
    return out


def chunk_view(chunk: ChunkRecord) -> dict:
    """A chunk as the model sees it in a tool result."""
    return {
        "chunk_id": chunk.id,
        "company": chunk.ticker,
        "fiscal_year": chunk.fiscal_year,
        "item": chunk.item,
        "kind": chunk.kind,
        "units": chunk.units,
        "printed_pages": chunk.printed_pages,
        "text": chunk.text,
    }


def toolbox_from_settings(
    settings: dict, tool_set: str = "full", live: bool = False, search: bool = True
) -> Toolbox:
    """The tools as the config's `agent:` section sets them. live: SEC requests not in the
    cache go to EDGAR (needs SEC_USER_AGENT); otherwise offline. search: load V1's models."""
    import os
    from pathlib import Path

    from tenk_agent.acquisition.edgar import EdgarClient
    from tenk_agent.retrieval.search import SubQuestionRetriever

    store = Store(Path(settings["db"]))
    hybrid = (
        SubQuestionRetriever.from_settings(store, settings["retrieval"]).hybrid if search else None
    )
    client = None
    if live:
        user_agent = os.environ.get("SEC_USER_AGENT")
        if not user_agent:
            raise SecError("live SEC requests need SEC_USER_AGENT, e.g. 'Name you@example.com'")
        client = EdgarClient(user_agent)
    sec = SecData(Path(settings["data_dir"]) / "raw" / "edgar", client)
    return Toolbox(store, hybrid, sec, tool_set, settings["agent"]["section_chunks"])
