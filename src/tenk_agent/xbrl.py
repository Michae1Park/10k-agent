"""V5 experiment: a get_financial_facts tool over the SEC's XBRL company facts.

Off by default (TENK_STRUCTURED_DATA=1 enables it). Through V4, XBRL data only verifies gold
answers behind the scenes; the agent never sees it. V5 measures what changes when it does.

Tag names differ between companies, so each standard metric maps to candidate us-gaap
concepts, first match wins, with per-company overrides. Company facts omits dimensional
data (segments, product lines) and custom tags, so those metrics are simply unavailable.
"""

import json
from datetime import date
from functools import cache
from pathlib import Path

from tenk_agent.models import ToolSpec
from tenk_agent.store import Store

METRICS: dict[str, tuple[str, ...]] = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"),
    "cost_of_revenue": ("CostOfGoodsAndServicesSold", "CostOfRevenue"),
    "gross_profit": ("GrossProfit",),
    "research_and_development": ("ResearchAndDevelopmentExpense",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss",),
    "capital_expenditures": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "total_assets": ("Assets",),
    "cash_and_equivalents": ("CashAndCashEquivalentsAtCarryingValue",),
}

# Per-company overrides; an empty tuple means the company reports no standard tag for it.
OVERRIDES: dict[str, dict[str, tuple[str, ...]]] = {
    # Amazon reports "Technology and infrastructure" under a custom (amzn:) tag.
    "AMZN": {"research_and_development": ()},
    # NVIDIA and Netflix tag total revenue as Revenues.
    "NVDA": {"revenue": ("Revenues",)},
    "NFLX": {"revenue": ("Revenues",)},
}

GET_FINANCIAL_FACTS = ToolSpec(
    name="get_financial_facts",
    description=(
        "Structured financial-statement values from the filing's XBRL data (company totals "
        "only; no segments or product lines). Metrics: " + ", ".join(METRICS) + ". Values "
        "are in USD millions, for the filing's fiscal year, with the XBRL concept used. Cite "
        "the filing's statement chunk for any number you report."
    ),
    parameters={
        "type": "object",
        "properties": {
            "company": {"type": "string"},
            "fiscal_year": {"type": "integer"},
            "metrics": {"type": "array", "items": {"type": "string", "enum": list(METRICS)}},
        },
        "required": ["company", "fiscal_year"],
    },
)


def concepts_for(ticker: str, metric: str) -> tuple[str, ...]:
    return OVERRIDES.get(ticker, {}).get(metric, METRICS[metric])


@cache
def _facts(data_dir: Path, ticker: str) -> dict:
    path = data_dir / "raw" / "edgar" / ticker / "companyfacts.json"
    return json.loads(path.read_text())["facts"].get("us-gaap", {})


def financial_facts(
    data_dir: Path, store: Store, ticker: str, fiscal_year: int, metrics=None
) -> dict:
    filing = store.filing(ticker, fiscal_year)
    if filing is None:
        return {"error": f"no {ticker} FY{fiscal_year} filing"}
    facts = _facts(data_dir, ticker)
    values = {}
    for metric in metrics or METRICS:
        if metric not in METRICS:
            values[metric] = {"error": f"unknown metric; use one of {list(METRICS)}"}
            continue
        values[metric] = _lookup(facts, concepts_for(ticker, metric), filing)
    return {
        "company": ticker,
        "fiscal_year": fiscal_year,
        "period_end_date": filing.period_end_date,
        "unit": "USD millions",
        "values": values,
    }


def _lookup(facts: dict, concepts: tuple[str, ...], filing) -> dict | None:
    for concept in concepts:
        for fact in facts.get(concept, {}).get("units", {}).get("USD", []):
            if fact.get("accn") != filing.accession_no or fact["end"] != filing.period_end_date:
                continue
            if "start" in fact:
                days = (date.fromisoformat(fact["end"]) - date.fromisoformat(fact["start"])).days
                if not 350 <= days <= 380:
                    continue
            return {"value": round(fact["val"] / 1e6, 3), "concept": f"us-gaap:{concept}"}
    return None
