"""The V3 agent's tools: what each does, its schema, and the code that runs it.

  search_filings   V1's retrieval: sub-questions per company and filing, hybrid search, round-robin
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
from tenk_agent.retrieval.search import SubQuestionRetriever
from tenk_agent.store import ChunkRecord, Store

COMPANY = {"type": "string", "description": "Ticker or name, e.g. AAPL or Apple"}
YEARS = {"type": "array", "items": {"type": "integer"}, "description": "Fiscal years (10-Ks)"}

SPECS = {
    "search_filings": ToolSpec(
        "search_filings",
        "Search the 10-K excerpts in the corpus. Name the companies (companies, or in the "
        'query; ["all"] for every company) and, if you know them, the fiscal years. Runs one '
        "search per company and filing and interleaves the results, so a comparison gets "
        "chunks from every company. Returns chunks with their IDs to cite.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to find, e.g. 'total net sales'"},
                "companies": {
                    "type": "array",
                    "items": COMPANY,
                    "description": 'Companies to search; ["all"] for every company',
                },
                "fiscal_years": YEARS,
                "k": {"type": "integer", "description": "Chunks in all (default 8, max 16)"},
            },
            "required": ["query"],
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
    "text": ("search_filings", "calculator"),
    "full": ("search_filings", "calculator", "xbrl_facts", "edgar_filings"),
}


@dataclass
class Toolbox:
    store: Store
    search: SubQuestionRetriever | None  # None: search_filings unavailable (tests)
    sec: SecData | None
    tool_set: str = "full"

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
        k: int = 8,
    ) -> dict:
        """V1's retrieval, with the companies and years the model gives in place of those it
        would find in the query. No company given or named: an error, not an unfiltered search."""
        if self.search is None:
            return {"error": "search is not available"}
        k = max(1, min(int(k), 16))
        if [c.lower() for c in companies or []] == ["all"]:
            tickers, unknown = sorted({f.ticker for f in self.store.filings()}), []
        else:
            tickers, unknown = self._tickers(companies or [])
        if unknown:
            return {"error": f"not in the corpus: {unknown}", "corpus": self._corpus()}
        years = [int(y) for y in fiscal_years] if fiscal_years else None
        subs = self.search.decomposer.decompose(query, tickers, years)
        if any(sub.ticker is None for sub in subs):
            return {
                "error": 'name the companies to search: pass companies (or ["all"]), '
                "or name them in the query",
                "corpus": self._corpus(),
            }
        result = self.search.search(query, k, tickers, years)
        return {
            "query": query,
            "searches": [
                {"query": s.query, "company": s.ticker, "fiscal_years": s.fiscal_years}
                for s in result.searches
            ],
            "chunks": [chunk_view(c) for c in result.chunks],
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

    store = Store(Path(settings["db"]))
    retriever = SubQuestionRetriever.from_settings(store, settings["retrieval"]) if search else None
    client = None
    if live:
        user_agent = os.environ.get("SEC_USER_AGENT")
        if not user_agent:
            raise SecError("live SEC requests need SEC_USER_AGENT, e.g. 'Name you@example.com'")
        client = EdgarClient(user_agent)
    sec = SecData(Path(settings["data_dir"]) / "raw" / "edgar", client)
    return Toolbox(store, retriever, sec, tool_set)
