"""The V3 agent's tools, offline: corpus reads, the calculator, and cached SEC data."""

import json

import pytest

from tenk_agent.agent.sec import SecData
from tenk_agent.agent.tools import Toolbox
from tenk_agent.retrieval.index import load
from tenk_agent.store import Store

ACCESSION = "0000320193-25-000079"


def make_store(tmp_path) -> Store:
    meta = {
        "ticker": "AAPL",
        "fiscal_year": 2025,
        "accession_no": ACCESSION,
        "period_end_date": "2025-09-27",
        "filing_date": "2025-10-31",
        "source_url": "https://example.com/aapl.htm",
    }
    chunks = [
        {"item": "8", "kind": "prose", "text": f"Part {i} of the financial statements."}
        for i in range(5)
    ] + [{"item": "1A", "kind": "prose", "text": "Tariffs could affect our supply chain."}]
    document = {
        "sections": [
            {"item": "8", "title": "Financial Statements"},
            {"item": "1A", "title": "Risk Factors"},
        ]
    }
    load(tmp_path / "test.db", [(meta, document, chunks)])
    return Store(tmp_path / "test.db")


def make_sec(tmp_path) -> SecData:
    def fact(fy, start, end, val):
        row = {"start": start, "end": end, "val": val, "accn": ACCESSION, "fy": fy}
        return row | {"fp": "FY", "form": "10-K"}

    facts = {
        "facts": {
            "dei": {
                "EntityPublicFloat": {
                    "units": {
                        "USD": [
                            {
                                "end": "2025-03-28",
                                "val": 1,
                                "accn": ACCESSION,
                                "fy": 2025,
                                "form": "10-K",
                            }
                        ]
                    }
                }
            },
            "us-gaap": {
                "ResearchAndDevelopmentExpense": {
                    "label": "Research and Development Expense",
                    "units": {
                        "USD": [
                            fact(2025, "2023-10-01", "2024-09-28", 31370000000),
                            fact(2025, "2024-09-29", "2025-09-27", 34550000000),
                        ]
                    },
                },
                "NetIncomeLoss": {"label": "Net Income (Loss)", "units": {"USD": []}},
            },
        }
    }
    (tmp_path / "edgar" / "AAPL").mkdir(parents=True)
    (tmp_path / "edgar" / "AAPL" / "companyfacts.json").write_text(json.dumps(facts))
    recent = {
        "form": ["10-K", "10-Q", "10-K/A"],
        "accessionNumber": [ACCESSION, "0000320193-25-000057", "0000320193-24-000001"],
        "filingDate": ["2025-10-31", "2025-08-01", "2024-12-01"],
        "reportDate": ["2025-09-27", "2025-06-28", "2024-09-28"],
        "primaryDocument": ["aapl-20250927.htm", "aapl-20250628.htm", "a.htm"],
    }
    submissions = {"fiscalYearEnd": "0927", "filings": {"recent": recent}}
    (tmp_path / "edgar" / "AAPL" / "submissions.json").write_text(json.dumps(submissions))
    return SecData(tmp_path / "edgar")


@pytest.fixture
def tools(tmp_path) -> Toolbox:
    return Toolbox(make_store(tmp_path), None, make_sec(tmp_path), section_chunks=2)


def test_get_section_pages_through_an_item_in_order(tools):
    first = tools.call("get_section", {"company": "Apple", "fiscal_year": 2025, "item": "8"})
    assert (first["title"], first["page"], first["pages"]) == ("Financial Statements", 1, 3)
    assert [c["chunk_id"] for c in first["chunks"]] == ["AAPL-FY2025-8-000", "AAPL-FY2025-8-001"]
    last = tools.call(
        "get_section", {"company": "AAPL", "fiscal_year": 2025, "item": "8", "page": 9}
    )
    assert last["page"] == 3 and [c["chunk_id"] for c in last["chunks"]] == ["AAPL-FY2025-8-004"]


def test_get_section_errors_name_what_exists(tools):
    missing = tools.call("get_section", {"company": "AAPL", "fiscal_year": 2025, "item": "7"})
    assert "no Item 7" in missing["error"] and ["1A", "Risk Factors"] in map(list, missing["items"])
    other = tools.call("get_section", {"company": "Oracle", "fiscal_year": 2025, "item": "8"})
    assert other["corpus"] == ["AAPL FY2025"]


def test_calculator_tool_returns_formula_and_errors_as_results(tools):
    result = tools.call("calculator", {"op": "pct_change", "inputs": {"old": 100, "new": 125}})
    assert result == {
        "result": 25.0,
        "formula": "(new - old) / old * 100",
        "inputs": {"old": 100.0, "new": 125.0},
    }
    assert (
        "division by zero"
        in tools.call("calculator", {"expression": "a / 0", "inputs": {"a": 1}})["error"]
    )
    assert "bad arguments" in tools.call("calculator", {"expression": "1 + 1"})["error"]


def test_xbrl_facts_returns_the_filings_values_newest_first_with_citable_ids(tools):
    result = tools.call(
        "xbrl_facts",
        {"company": "Apple", "fiscal_year": 2025, "concept": "ResearchAndDevelopmentExpense"},
    )
    assert result["accession_no"] == ACCESSION
    assert [v["value"] for v in result["values"]] == [34550000000, 31370000000]
    xbrl_id = result["values"][0]["xbrl_id"]
    assert xbrl_id == f"xbrl:AAPL:us-gaap:ResearchAndDevelopmentExpense:{ACCESSION}:2025-09-27"
    assert tools.sec.xbrl_fact(xbrl_id)["value"] == 34550000000


def test_xbrl_facts_suggests_concepts_and_rejects_missing_years(tools):
    fuzzy = tools.call(
        "xbrl_facts",
        {"company": "AAPL", "fiscal_year": 2025, "concept": "research and development"},
    )
    assert fuzzy["candidates"][0]["concept"] == "us-gaap:ResearchAndDevelopmentExpense"
    assert (
        "no AAPL 10-K"
        in tools.call(
            "xbrl_facts", {"company": "AAPL", "fiscal_year": 2019, "concept": "NetIncomeLoss"}
        )["error"]
    )


def test_edgar_filings_lists_original_10ks_and_other_forms(tools):
    annual = tools.call("edgar_filings", {"company": "Apple"})
    assert [f["accession_no"] for f in annual["filings"]] == [ACCESSION]
    assert annual["filings"][0]["url"].endswith("/000032019325000079/aapl-20250927.htm")
    quarterly = tools.call("edgar_filings", {"company": "AAPL", "form": "10-Q"})
    assert quarterly["filings"][0]["period_end_date"] == "2025-06-28"


def test_offline_sec_data_never_fetches(tools):
    assert "offline" in tools.call("edgar_filings", {"company": "Oracle"})["error"]


def test_text_tool_set_hides_sec_tools(tools):
    tools.tool_set = "text"
    assert [s.name for s in tools.specs] == ["search_filings", "get_section", "calculator"]
    assert "unknown tool" in tools.call("xbrl_facts", {})["error"]
