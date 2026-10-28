"""The V3 agent's tools: what each does, its schema, and the code that runs it.

  search_filings   V1's retrieval: sub-questions per company and filing, hybrid search, round-robin
  calculator       generation/calculator.py: named operations or an expression over inputs
  xbrl_facts       SEC XBRL company facts for one concept and 10-K            (tool set "full")
  edgar_filings    live EDGAR filing metadata: which filings exist, when      (tool set "full")

V4 tool set "ask" (design doc §7.1), every result as of the store's AsOf:
  financials       derived quarters and full years of a core line (facts/quarters.py)
  company_kpis     release KPIs (facts/extract.py), with their source chunks
  guidance         release guidance, with the actual where the target period has reported
  filings          the corpus's 10-Ks, 10-Qs and releases, with filing dates
  market_data      closing prices and macro series

V4 tool set "research" (§7.1) adds the model group, over the session's model book
(research/book.py), every number cited by a model: id:
  model_view       history and forecast lines, the assumption register and guidance anchors
  run_scenario     overrides to the drivers → changed lines, price target and rating vs base
  compare_peers    one metric (a model line or a valuation output) across companies
  variance_review  our prior estimate for a quarter vs the actual, by driver, with the bridge

Tools never raise: a bad call returns {"error": ...} for the model to read and recover from.
The tool set ("text" or "full") is the V3 ablation (experiments/V3_AGENT.md).

V4 (design doc §7.1): tools come in groups, and a tool set is a list of groups, so no single
context gets every tool. A Toolbox sees what its store's AsOf sees; no tool takes an as-of.
"""

import json
from dataclasses import dataclass

from tenk_agent.agent.sec import SecData, SecError
from tenk_agent.asof import AsOf
from tenk_agent.corpus import resolve_company
from tenk_agent.generation.calculator import CalculatorError, calculate
from tenk_agent.models import ToolSpec
from tenk_agent.retrieval.search import SubQuestionRetriever
from tenk_agent.store import ChunkRecord, Store, period_parts

COMPANY = {"type": "string", "description": "Ticker or name, e.g. AAPL or Apple"}
YEARS = {"type": "array", "items": {"type": "integer"}, "description": "Fiscal years (10-Ks)"}
PERIODS = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Period keys, e.g. 2026Q2 or FY2025 (default: the latest eight)",
}
ROWS = 40  # rows a data tool returns at most; the rest are counted

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

SPECS |= {
    "financials": ToolSpec(
        "financials",
        "Reported financial lines of a company by quarter and full year, from its XBRL "
        "filings: revenue, net_income, operating_income, diluted_eps, operating_cash_flow and "
        "the others listed on an unknown metric. Q4 and other quarters missing from the filings "
        "are derived (Q4 = FY - 9M) and say so. Values in USD (per share: USD/shares). Cite a "
        "value by its id.",
        {
            "type": "object",
            "properties": {
                "company": COMPANY,
                "metric": {"type": "string", "description": "e.g. revenue"},
                "periods": PERIODS,
            },
            "required": ["company", "metric"],
        },
    ),
    "company_kpis": ToolSpec(
        "company_kpis",
        "Operating KPIs from a company's earnings releases (gross bookings, trips, room "
        "nights, orders, active riders, ...) by quarter and segment, each with the release "
        "chunk and quote it came from. No metric: the KPIs the company reports. Cite a value "
        "by its id.",
        {
            "type": "object",
            "properties": {
                "company": COMPANY,
                "metric": {"type": "string", "description": "e.g. gross_bookings"},
                "segment": {"type": "string", "description": "e.g. mobility (default: all)"},
                "periods": PERIODS,
            },
            "required": ["company"],
        },
    ),
    "guidance": ToolSpec(
        "guidance",
        "A company's outlook from its earnings releases: low and high, basis (level, "
        "growth_yoy, margin), the period it targets and the release it was issued in, and the "
        "actual once that period has reported. Cite a range by its id.",
        {
            "type": "object",
            "properties": {
                "company": COMPANY,
                "metric": {"type": "string", "description": "e.g. adj_ebitda (default: all)"},
                "periods": {**PERIODS, "description": "Target periods, e.g. 2026Q3, FY2026"},
            },
            "required": ["company"],
        },
    ),
    "filings": ToolSpec(
        "filings",
        "Which 10-Ks, 10-Qs and earnings releases (ER) of a company are in the corpus, with "
        "periods, filing dates and accession numbers. No financial data.",
        {
            "type": "object",
            "properties": {
                "company": COMPANY,
                "form": {"type": "string", "description": "10-K, 10-Q or ER (default: all)"},
            },
            "required": ["company"],
        },
    ),
    "market_data": ToolSpec(
        "market_data",
        "Daily closing prices of a company's stock (or SPY), or a macro series: DGS10 (10-year "
        "Treasury yield, percent), CPIAUCSL, DTWEXBGS, RSFSDP, UMCSENT. Gives the last close "
        "on or before each date asked for, or the series over a range. Cite a value by its id.",
        {
            "type": "object",
            "properties": {
                "company": COMPANY,
                "series": {"type": "string", "description": "A macro series, e.g. DGS10"},
                "dates": {"type": "array", "items": {"type": "string"},
                          "description": "YYYY-MM-DD (default: the latest)"},
                "start": {"type": "string", "description": "YYYY-MM-DD: a range from here"},
            },
        },
    ),
}

ASK_SPECS = {  # tool set "ask": the same tool over every form
    "search_filings": ToolSpec(
        "search_filings",
        "Search the text of the filings: 10-Ks, 10-Qs and earnings releases (ER). Name the "
        'companies (companies, or in the query; ["all"] for every company) and, if you know '
        "them, the periods and forms. Runs one search per company and interleaves the results, "
        "so a comparison gets chunks from every company. Returns chunks with their IDs to cite.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to find, e.g. 'take rate'"},
                "companies": {
                    "type": "array",
                    "items": COMPANY,
                    "description": 'Companies to search; ["all"] for every company',
                },
                "periods": {**PERIODS, "description": "Period keys, e.g. 2026Q2 or FY2025"},
                "forms": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["10-K", "10-Q", "ER"]},
                    "description": "Forms to search (default: all)",
                },
                "k": {"type": "integer", "description": "Chunks in all (default 8, max 16)"},
            },
            "required": ["query"],
        },
    ),
}

SPECS |= {
    "model_view": ToolSpec(
        "model_view",
        "Our model of a company as of today: history and forecast (quarters and fiscal years) "
        "of its main lines, the assumptions behind the forecast (each driver's value, default "
        "and whether guidance anchors it) and its exceptions. Cite a value by the id pattern "
        "the result gives.",
        {
            "type": "object",
            "properties": {
                "company": COMPANY,
                "lines": {"type": "array", "items": {"type": "string"},
                          "description": "Model lines (default: the main ones)"},
                "periods": {**PERIODS, "description": "e.g. 2026Q3, FY2027 (default: recent "
                            "quarters, the forecast and its fiscal years)"},
            },
            "required": ["company"],
        },
    ),
    "run_scenario": ToolSpec(
        "run_scenario",
        "Rerun a company's model with changed drivers: give each driver's new value, or a "
        "change from the base (deltas). Returns the changed lines (2026E, 2027E), the price "
        "target and rating against the base, with ids. An unknown driver returns the valid ones."
        " Growth drivers (.g0) and take-rate or margin changes (.shift0) are fractions: a take "
        "rate 50 bps lower is a delta of -0.005.",
        {
            "type": "object",
            "properties": {
                "company": COMPANY,
                "overrides": {"type": "object", "additionalProperties": {"type": "number"},
                              "description": "driver → new value"},
                "deltas": {"type": "object", "additionalProperties": {"type": "number"},
                           "description": "driver → change from the base value"},
            },
            "required": ["company"],
        },
    ),
    "compare_peers": ToolSpec(
        "compare_peers",
        "One metric across companies at today's as-of, from our models: a model line in a "
        "period (revenue, adj_ebitda, adj_ebitda_margin, eps_diluted, free_cash_flow, take_rate…"
        " in FY2027 by default) or a valuation output (ev_to_ebitda, ev_to_ebitda_less_sbc, pe, "
        "fcf_yield, price_target, return, rating).",
        {
            "type": "object",
            "properties": {
                "metric": {"type": "string"},
                "companies": {"type": "array", "items": COMPANY,
                              "description": 'Companies (default: all eight); ["all"] too'},
                "period": {"type": "string", "description": "For a model line (default FY2027)"},
            },
            "required": ["metric"],
        },
    ),
    "variance_review": ToolSpec(
        "variance_review",
        "Our estimate for a reported quarter (made the day after the previous quarter's "
        "release) against the actual, line by line, with the revenue bridge: how much of the "
        "miss came from volume, from value per unit, and from the take rate.",
        {
            "type": "object",
            "properties": {"company": COMPANY,
                           "period": {"type": "string", "description": "e.g. 2026Q2"}},
            "required": ["company", "period"],
        },
    ),
}

GROUPS = {  # V4 §7.1; reports and model gain their tools in V4.5
    "text": ("search_filings",),
    "calc": ("calculator",),
    "sec": ("xbrl_facts", "edgar_filings"),  # V3's facts group
    "facts": ("financials", "company_kpis", "guidance", "filings"),
    "market": ("market_data",),
    "model": ("model_view", "run_scenario", "compare_peers", "variance_review"),
}
GROUP_SETS = {
    "text": ("text", "calc"),
    "full": ("text", "calc", "sec"),
    "ask": ("text", "calc", "facts", "market"),
    "research": ("text", "calc", "facts", "market", "model"),
}
TOOL_SETS = {name: sum((GROUPS[g] for g in groups), ()) for name, groups in GROUP_SETS.items()}


@dataclass
class Toolbox:
    store: Store
    search: SubQuestionRetriever | None  # None: search_filings unavailable (tests)
    sec: SecData | None
    tool_set: str = "full"
    book: object | None = None  # research/book.py Book: tool set "research" (model tools)

    @property
    def asof(self) -> AsOf:
        return self.store.asof

    @property
    def specs(self) -> list[ToolSpec]:
        return [self._spec(name) for name in TOOL_SETS[self.tool_set]]

    def _spec(self, name: str) -> ToolSpec:
        v4 = self.tool_set in ("ask", "research")
        return (ASK_SPECS if v4 else {}).get(name) or SPECS[name]

    def call(self, name: str, arguments: dict) -> dict:
        if name not in TOOL_SETS[self.tool_set]:
            return {"error": f"unknown tool {name!r}; tools: {list(TOOL_SETS[self.tool_set])}"}
        arguments = _coerce(arguments, self._spec(name).parameters["properties"])
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
        periods: list[str] | None = None,
        forms: list[str] | None = None,
    ) -> dict:
        """V1's retrieval, with the companies and years the model gives in place of those it
        would find in the query. No company given or named: an error, not an unfiltered search.
        V4 (tool set "ask"): periods and forms in place of fiscal years."""
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
        if self.tool_set in ("ask", "research"):
            for p in periods or []:
                period_parts(p)  # a bad key: ValueError, returned as an error
            result = self.search.search(query, k, tickers, None, periods or None, forms or None)
            return {
                "query": query,
                "searches": [
                    {"query": s.query, "company": s.ticker}
                    | ({"periods": periods} if periods else {})
                    for s in result.searches
                ],
                "chunks": [ask_chunk_view(c) for c in result.chunks],
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


    # --- V4 data (tool set "ask") ------------------------------------------------------

    def _ticker(self, company: str) -> str:
        tickers, unknown = self._tickers([company])
        if unknown:
            raise ValueError(f"not in the corpus: {company!r}; companies: {self._companies()}")
        return tickers[0]

    def _companies(self) -> list[str]:
        return sorted({f.ticker for f in self.store.filings()})

    def financials(self, company: str, metric: str, periods: list[str] | None = None) -> dict:
        from tenk_agent.facts.quarters import derive, metrics

        ticker, specs = self._ticker(company), metrics()
        if metric not in specs:
            return {"error": f"unknown metric {metric!r}", "metrics": sorted(specs)}
        rows = [
            {"id": q.id, "period": _key(q), "value": q.value, "unit": q.unit,
             "derivation": q.derivation} | ({"restated": True} if q.restated else {})
            for q in derive(self.store, ticker, metric, specs[metric])
        ]
        return {"company": ticker, "metric": metric} | _pick(rows, periods)

    def company_kpis(
        self,
        company: str,
        metric: str | None = None,
        segment: str | None = None,
        periods: list[str] | None = None,
    ) -> dict:
        from tenk_agent.research.refs import kpi_id

        ticker = self._ticker(company)
        rows = self.store.kpis(ticker, metric)
        if not rows:
            reported = sorted({r["metric"] for r in self.store.kpis(ticker)})
            return {"error": f"no KPI {metric!r} for {ticker}", "kpis": reported}
        latest = {(r["metric"], r["segment"], r["period"]): r for r in rows}  # later filed wins
        out = [
            {"id": kpi_id(ticker, r["metric"], r["segment"], r["period"]), "period": r["period"],
             "metric": r["metric"], "segment": r["segment"], "value": r["value"],
             "unit": r["unit"], "chunk_id": r["chunk_id"], "quote": r["quote"]}
            for r in latest.values()
            if segment is None or r["segment"] == segment
        ]
        return {"company": ticker} | _pick(out, periods)

    def guidance(
        self, company: str, metric: str | None = None, periods: list[str] | None = None
    ) -> dict:
        from tenk_agent.research.refs import Resolver, guide_id, kpi_id

        ticker = self._ticker(company)
        rows = self.store.guidance(ticker, metric)
        if not rows:
            given = sorted({r["metric"] for r in self.store.guidance(ticker)})
            return {"error": f"no guidance {metric!r} for {ticker}", "guided": given}
        resolver, out = Resolver(self.store), []
        for r in rows:  # oldest issue first: an update follows the guide it replaces
            actual = r["basis"] == "level" and resolver.resolve(
                kpi_id(ticker, r["metric"], r["segment"], r["target_period"])
            )
            out.append(
                {"id": guide_id(ticker, r["metric"], r["segment"], r["target_period"]),
                 "period": r["target_period"], "metric": r["metric"], "segment": r["segment"],
                 "low": r["low"], "high": r["high"], "unit": r["unit"], "basis": r["basis"],
                 "issued_in": r["issued_in"], "chunk_id": r["chunk_id"], "quote": r["quote"]}
                | ({"actual": actual.values[0]["value"], "actual_id": actual.ref.id}
                   if actual else {})
            )
        return {"company": ticker} | _pick(out, periods)

    def filings(self, company: str, form: str | None = None) -> dict:
        ticker = self._ticker(company)
        rows = [
            {"form": f.form, "period": f"FY{f.fiscal_year}" if f.fiscal_period == "FY"
             else f"{f.fiscal_year}{f.fiscal_period}", "period_end_date": f.period_end_date,
             "filing_date": f.filing_date, "accession_no": f.accession_no, "id": f.accession_no}
            for f in self.store.filings()
            if f.ticker == ticker and (form is None or f.form == form.upper())
        ]
        rows.sort(key=lambda r: r["filing_date"])
        return {"company": ticker, "filings": rows[-ROWS:], "total": len(rows)}

    def market_data(
        self,
        company: str | None = None,
        series: str | None = None,
        dates: list[str] | None = None,
        start: str | None = None,
    ) -> dict:
        if bool(company) == bool(series):
            return {"error": "give a company or a macro series, not both"}
        if company:
            ticker = "SPY" if company.upper() == "SPY" else self._ticker(company)
            name, prefix, unit = f"px:{ticker}:close", f"px:{ticker}", "USD"
        else:
            name, prefix, unit = f"macro:{series.upper()}", f"macro:{series.upper()}", ""
        points = self.store.series(name)
        if not points:
            return {"error": f"no data for {name}"}
        if start:
            chosen = [p for p in points if p[0] >= start]
        else:
            chosen = [
                next((p for p in reversed(points) if p[0] <= d), None) for d in dates
            ] if dates else [points[-1]]
        rows = [{"id": f"{prefix}:{d}", "date": d, "value": v} for d, v in filter(None, chosen)]
        return {"series": name, "unit": unit} | _cap(rows)


    # --- V4 model (tool set "research") ------------------------------------------------

    def model_view(self, company: str, lines: list[str] | None = None,
                   periods: list[str] | None = None) -> dict:
        ticker = self._ticker(company)
        f = self.book.base(ticker)
        m = f.model
        names = lines or _main_lines(m)
        unknown = [n for n in names if not any(x.name == n for x in m.lines)]
        if unknown:
            return {"error": f"unknown lines {unknown}", "lines": [x.name for x in m.lines]}
        columns = periods or (m.history[-4:] + m.forecast[:6] + sorted(
            {c for c in m.columns if c.startswith("FY")})[-3:])
        anchored = {a.solve: a for a in f.anchors}
        return {
            "company": ticker, "as_of": self.book.as_of, "forecast_from": m.forecast[0],
            "id_format": f"model:{ticker}:{self.book.as_of}:base:<line>.<period>",
            "rows": [{"line": n, "unit": m.line(n).unit,
                      "values": {c: _round(m.value(n, c)) for c in columns}} for n in names],
            "assumptions": [
                {"driver": k, "value": _round(v), "default": _round(f.defaults.get(k)),
                 "source": f"guidance {anchored[k].guide_id}" if k in anchored else "default"}
                for k, v in sorted(m.assumptions.items())
                if not k.endswith((".lo", ".hi"))],
            "exceptions": [e["item"] for e in f.exceptions][:6],
        }

    def run_scenario(self, company: str, overrides: dict | None = None,
                     deltas: dict | None = None) -> dict:
        from tenk_agent.research.model.graph import build
        from tenk_agent.research.propose import proposable

        ticker = self._ticker(company)
        f = self.book.base(ticker)
        base = f.model
        bounds = proposable(base, set()) | {
            "fade": tuple(self.book.settings["forecast"]["fade_bounds"])}
        changes = dict(overrides or {})
        for k, d in (deltas or {}).items():
            if k in base.assumptions:
                changes[k] = base.assumptions[k] + float(d)
            else:
                changes[k] = float("nan")
        bad = [k for k in changes if k not in bounds]
        if bad or not changes:
            return {"error": f"unknown drivers {bad}" if bad else "give overrides or deltas",
                    "drivers": sorted(bounds)}
        clipped = {}
        for k, v in changes.items():
            lo, hi = bounds[k]
            changes[k] = min(max(float(v), lo), hi)
            if changes[k] != float(v):
                clipped[k] = [lo, hi]
        model = build(ticker, base.template, base.as_of, base.history, f.pack, base.forecast,
                      base.assumptions | changes)
        version = self.book.add_scratch(model)
        val_base, val = self.book.valuation(ticker), self.book.valuation(ticker, version)
        years = sorted({c for c in model.columns if c.startswith("FY")})[-2:]
        rows = []
        for line in ("revenue", "adj_ebitda", "eps_diluted", "free_cash_flow"):
            for y in years:
                rows.append({"line": line, "period": y, "base": _round(base.value(line, y)),
                             "scenario": _round(model.value(line, y)),
                             "id": self.book.ref(ticker, version, line, y),
                             "base_id": self.book.ref(ticker, "base", line, y)})
        return {
            "company": ticker, "version": version,
            "changes": {k: _round(v) for k, v in changes.items()}, "clipped_to_bounds": clipped,
            "rows": rows,
            "price_target": {"base": _round(val_base["price_target"]),
                             "scenario": _round(val["price_target"]),
                             "id": f"model:{ticker}:{self.book.as_of}:val-{version}:price_target",
                             "base_id": f"model:{ticker}:{self.book.as_of}:val-base:price_target"},
            "rating": {"base": val_base["rating"], "scenario": val["rating"]},
        }

    def compare_peers(self, metric: str, companies: list[str] | None = None,
                      period: str = "FY2027") -> dict:
        if not companies or [c.lower() for c in companies] == ["all"]:
            tickers = self._companies()
        else:
            tickers, unknown = self._tickers(companies)
            if unknown:
                return {"error": f"not in the corpus: {unknown}"}
        valuation_fields = {"ev_to_ebitda", "ev_to_ebitda_less_sbc", "pe", "fcf_yield",
                            "price_target", "return", "rating", "ev", "market_cap"}
        rows = []
        for t in tickers:
            if metric in valuation_fields:
                v = self.book.valuation(t)
                value = v["multiples_2027e"].get(metric, v.get(metric))
                ref = f"model:{t}:{self.book.as_of}:val-base:{metric}"
            else:
                m = self.book.base(t).model
                if not any(x.name == metric for x in m.lines):
                    return {"error": f"unknown metric {metric!r}",
                            "model_lines": [x.name for x in m.lines if x.kind != "assumption"],
                            "valuation": sorted(valuation_fields)}
                value = m.value(metric, period)
                ref = self.book.ref(t, "base", metric, period)
            rows.append({"company": t, "value": _round(value) if not isinstance(value, str)
                         else value, "id": ref})
        numeric = [r for r in rows if isinstance(r["value"], int | float)]
        numeric.sort(key=lambda r: r["value"])
        return {"metric": metric, "period": None if metric in valuation_fields else period,
                "rows": numeric + [r for r in rows if r not in numeric]}

    def variance_review(self, company: str, period: str) -> dict:
        from tenk_agent.evaluation.backtest import releases, run_day
        from tenk_agent.research.forecast import next_quarter

        ticker = self._ticker(company)
        actual = self.book.base(ticker).model
        if period not in actual.history:
            return {"error": f"{period} is not reported as of {self.book.as_of}"}
        prior_period = next(p for p in sorted(releases(self.store, ticker))
                            if next_quarter(p) == period)
        prior_as_of = run_day(releases(self.store, ticker)[prior_period])
        prior = self.book.at(prior_as_of).base(ticker).model
        names = [n for n in _main_lines(actual) if any(x.name == n for x in prior.lines)]
        rows = []
        for n in names:
            est, act = prior.value(n, period), actual.value(n, period)
            rows.append({"line": n, "estimate": _round(est), "actual": _round(act),
                         "variance_pct": _round((act / est - 1) * 100) if est and act else None,
                         "estimate_id": f"model:{ticker}:{prior_as_of}:base:{n}.{period}",
                         "actual_id": actual.cells[(n, period)].ref
                         or self.book.ref(ticker, "base", n, period)})
        return {"company": ticker, "period": period, "estimate_as_of": prior_as_of,
                "rows": rows, "bridge": _bridge(prior, actual, period)}


def _main_lines(model) -> list[str]:
    """The lines a view shows by default: the operating build's drivers, then the summary."""
    names = [x.name for x in model.lines]
    build = [n for n in ("volume", "trips", "gross_bookings", "value_per_unit",
                         "gross_bookings_per_unit", "take_rate", "adj_ebitda_margin_gb")
             if n in names]
    return build + [n for n in ("revenue", "adj_ebitda", "adj_ebitda_margin", "eps_diluted",
                                "free_cash_flow") if n in names]


def _bridge(prior, actual, period: str) -> dict | None:
    """Revenue variance = gross value effect + take-rate effect (gross value split into volume
    and value per unit where the template has them)."""
    def v(model, line):
        return model.value(line, period) if any(x.name == line for x in model.lines) else None

    gb0, gb1 = v(prior, "gross_bookings"), v(actual, "gross_bookings")
    tr0, tr1 = v(prior, "take_rate"), v(actual, "take_rate")
    if None in (gb0, gb1, tr0, tr1):
        return None
    out = {"revenue_variance": _round(gb1 * tr1 - gb0 * tr0),
           "from_gross_value": _round((gb1 - gb0) * tr0),
           "from_take_rate": _round(gb1 * (tr1 - tr0))}
    unit = next((n for n in ("value_per_unit", "gross_bookings_per_unit")
                 if v(prior, n) is not None), None)
    vol0, vol1 = v(prior, "volume"), v(actual, "volume")
    if unit and None not in (vol0, vol1, v(actual, unit)):
        out["of_which_volume"] = _round((vol1 - vol0) * v(prior, unit) * tr0)
        out["of_which_value_per_unit"] = _round(vol1 * (v(actual, unit) - v(prior, unit)) * tr0)
    return out


def _round(value):
    return None if value is None else round(float(value), 4)


def _key(q) -> str:
    return f"FY{q.fiscal_year}" if q.period == "FY" else f"{q.fiscal_year}{q.period}"


def _pick(rows: list[dict], periods: list[str] | None) -> dict:
    """The rows of the periods asked for, or of the latest eight periods."""
    if periods:
        wanted = {p.upper().replace(" ", "") for p in periods}
        rows = [r for r in rows if r["period"].upper() in wanted]
    else:
        latest = sorted({r["period"] for r in rows}, key=_order)[-8:]
        rows = [r for r in rows if r["period"] in latest]
    return _cap(sorted(rows, key=lambda r: _order(r["period"])))


def _order(period: str) -> tuple:
    """FY2025 sorts after 2025Q4."""
    return (period[2:], "Q5") if period.startswith("FY") else (period[:4], period[4:])


def _cap(rows: list[dict]) -> dict:
    out = {"rows": rows[-ROWS:]}
    return out | ({"omitted": len(rows) - ROWS} if len(rows) > ROWS else {})


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


def ask_chunk_view(chunk: ChunkRecord) -> dict:
    """A chunk as the Ask model sees it: its form, period and filing date in place of the
    10-K's fiscal year."""
    fy, fp = chunk.fiscal_year, chunk.fiscal_period
    return {
        "chunk_id": chunk.id,
        "company": chunk.ticker,
        "form": chunk.form,
        "period": f"FY{fy}" if fp == "FY" else f"{fy}{fp}",
        "filed": chunk.filed,
        "item": chunk.item,
        "kind": chunk.kind,
        "units": chunk.units,
        "text": chunk.text,
    }


def toolbox_from_settings(
    settings: dict,
    tool_set: str = "full",
    live: bool = False,
    search: bool = True,
    asof: AsOf | None = None,
) -> Toolbox:
    """The tools as the config's `agent:` section sets them. live: SEC requests not in the
    cache go to EDGAR (needs SEC_USER_AGENT); otherwise offline. search: load V1's models.
    asof: what the tools see (None: today, as in V3)."""
    import os
    from pathlib import Path

    from tenk_agent.acquisition.edgar import EdgarClient

    store = Store(Path(settings["db"]), asof)
    retriever = SubQuestionRetriever.from_settings(store, settings["retrieval"]) if search else None
    client = None
    if live:
        user_agent = os.environ.get("SEC_USER_AGENT")
        if not user_agent:
            raise SecError("live SEC requests need SEC_USER_AGENT, e.g. 'Name you@example.com'")
        client = EdgarClient(user_agent)
    sec = SecData(Path(settings["data_dir"]) / "raw" / "edgar", client)
    return Toolbox(store, retriever, sec, tool_set)
