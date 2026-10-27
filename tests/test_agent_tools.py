"""The V3 agent's tools, offline: corpus reads, the calculator, and cached SEC data."""

import json

import pytest

from tenk_agent.agent.sec import SecData
from tenk_agent.agent.tools import Toolbox
from tenk_agent.generation.verify import verify_claim
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
    return Toolbox(make_store(tmp_path), None, make_sec(tmp_path))


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
    as_string = tools.call("calculator", {"op": "sum", "inputs": '{"a": 1, "b": 2}'})
    assert as_string["result"] == 3
    assert "bad arguments" in tools.call("calculator", {"op": "sum", "inputs": "a=1"})["error"]


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
    assert [s.name for s in tools.specs] == ["search_filings", "calculator"]
    assert "unknown tool" in tools.call("xbrl_facts", {})["error"]


# --- Claim check with XBRL citations (verify.py, D-036) ------------------------------------

FACT = f"xbrl:AAPL:us-gaap:ResearchAndDevelopmentExpense:{ACCESSION}:2025-09-27"
PRIOR = f"xbrl:AAPL:us-gaap:ResearchAndDevelopmentExpense:{ACCESSION}:2024-09-28"


def claim(**fields) -> dict:
    return {"company": "AAPL", "fiscal_year": 2025, "calculation": None} | fields


def test_a_value_matching_a_cited_fact_is_verified_in_any_unit(tools):
    for value, unit in ((34550, "USD millions"), (34.55, "USD billions"), (34.6, "USD billions")):
        checked = verify_claim(
            claim(value=value, unit=unit, chunk_ids=[FACT]), tools.store, tools.sec
        )
        assert checked["status"] == "verified", (value, unit)
    wrong = verify_claim(
        claim(value=34000, unit="USD millions", chunk_ids=[FACT]), tools.store, tools.sec
    )
    assert wrong["status"] == "unverified"


def test_a_fact_of_another_company_or_an_unknown_fact_does_not_verify(tools):
    other = verify_claim(
        claim(company="MSFT", value=34550, unit="USD millions", chunk_ids=[FACT]),
        tools.store,
        tools.sec,
    )
    assert other["status"] == "unverified"
    unknown = verify_claim(
        claim(value=1, unit="USD", chunk_ids=[FACT[:-1] + "8"]), tools.store, tools.sec
    )
    assert "unknown chunk IDs" in unknown["status_reason"]


def test_calculation_inputs_may_come_from_cited_facts(tools):
    calculation = {
        "op": "pct_change",
        "inputs": {"old": 31370, "new": 34550},
        "input_unit": "USD millions",
    }
    checked = verify_claim(
        claim(value=10.14, unit="percent", calculation=calculation, chunk_ids=[FACT, PRIOR]),
        tools.store,
        tools.sec,
    )
    assert checked["status"] == "calculated"
    one_fact = verify_claim(
        claim(value=10.14, unit="percent", calculation=calculation, chunk_ids=[FACT]),
        tools.store,
        tools.sec,
    )
    assert one_fact["status"] == "unverified" and "old" in one_fact["status_reason"]


def test_a_percent_claim_may_come_from_a_ratio(tools):
    calculation = {
        "op": "ratio",
        "inputs": {"numerator": 31370, "denominator": 34550},
        "input_unit": "USD millions",
    }
    checked = verify_claim(
        claim(value=90.8, unit="percent", calculation=calculation, chunk_ids=[FACT, PRIOR]),
        tools.store,
        tools.sec,
    )
    assert checked["status"] == "calculated"


def test_bare_dollar_amounts_match_gold_values_in_millions():
    from tenk_agent.evaluation.answer_metrics import matches

    expected = {"value": 97690, "unit": "USD millions"}
    assert matches(expected, "97,690,000,000", None, 0.005)
    assert matches(expected, "97690", None, 0.005)
    assert not matches(expected, "97,690,000", None, 0.005)


def test_a_calculation_with_op_expression_is_rerun_as_an_expression(tools):
    calculation = {
        "op": "expression",
        "expression": "(new - old) / old * 100",
        "inputs": {"old": 31370, "new": 34550},
        "input_unit": "USD millions",
    }
    checked = verify_claim(
        claim(value=10.14, unit="percent", calculation=calculation, chunk_ids=[FACT, PRIOR]),
        tools.store,
        tools.sec,
    )
    assert checked["status"] == "calculated"


def test_a_value_the_facts_show_verifies_despite_a_unit_conversion_calculation(tools):
    calculation = {
        "op": "ratio",
        "inputs": {"numerator": 34550000000, "denominator": 1000000000},
        "input_unit": "USD",
    }
    checked = verify_claim(
        claim(value=34550000000, unit="USD", calculation=calculation, chunk_ids=[FACT]),
        tools.store,
        tools.sec,
    )
    assert checked["status"] == "verified"


def test_a_sum_in_millions_may_be_claimed_in_dollars(tools):
    calculation = {"op": "sum", "inputs": {"a": 31370, "b": 34550}, "input_unit": "USD millions"}
    checked = verify_claim(
        claim(value=65920000000, unit="USD", calculation=calculation, chunk_ids=[FACT, PRIOR]),
        tools.store,
        tools.sec,
    )
    assert checked["status"] == "calculated"


def test_calculation_inputs_may_be_facts_of_another_company(tools):
    calculation = {
        "op": "difference",
        "inputs": {"a": 34550, "b": 31370},
        "input_unit": "USD millions",
    }
    checked = verify_claim(
        claim(
            company="MSFT",
            value=3180,
            unit="USD millions",
            calculation=calculation,
            chunk_ids=[FACT, PRIOR],
        ),
        tools.store,
        tools.sec,
    )
    assert checked["status"] == "calculated"


def test_a_filing_claim_may_cite_its_accession_number(tools):
    cited = verify_claim(
        claim(value=None, unit=None, chunk_ids=[ACCESSION]), tools.store, tools.sec
    )
    assert cited["status"] == "cited"
    unknown = verify_claim(
        claim(value=None, unit=None, chunk_ids=["0000000000-25-000000"]), tools.store, tools.sec
    )
    assert unknown["status"] == "unverified" and "unknown chunk IDs" in unknown["status_reason"]


def test_per_share_units_are_dollars():
    from tenk_agent.generation.verify import unit_scale

    assert unit_scale("USD/shares") == unit_scale("USD per share") == 1.0


def test_search_filings_runs_v1_retrieval_and_asks_for_companies(tmp_path):
    from tenk_agent.retrieval.decompose import QueryDecomposer
    from tenk_agent.retrieval.index import embed_corpus
    from tenk_agent.retrieval.search import HybridSearch, SubQuestionRetriever
    from test_retrieval import FakeEmbedder, FakeReranker
    from test_retrieval import make_store as two_company_store

    store = two_company_store(tmp_path)
    embed_corpus(store, FakeEmbedder())
    retriever = SubQuestionRetriever(
        QueryDecomposer(store), HybridSearch(store, FakeEmbedder(), FakeReranker())
    )
    tools = Toolbox(store, retriever, None)
    result = tools.call("search_filings", {"query": "revenue", "companies": ["all"], "k": 2})
    assert [s["company"] for s in result["searches"]] == ["AAPL", "NVDA"]
    assert sorted(c["company"] for c in result["chunks"]) == ["AAPL", "NVDA"]
    result = tools.call("search_filings", {"query": "revenue", "fiscal_years": [2025]})
    assert result["error"].startswith("name the companies")
    named = tools.call("search_filings", {"query": "Apple revenue in fiscal 2025"})
    assert [c["chunk_id"] for c in named["chunks"]] == ["AAPL-FY2025-7-000"]


def test_tool_sets_are_built_from_groups_in_v3_order():
    from tenk_agent.agent.tools import GROUPS, TOOL_SETS

    assert TOOL_SETS == {
        "text": ("search_filings", "calculator"),
        "full": ("search_filings", "calculator", "xbrl_facts", "edgar_filings"),
    }
    assert {t for tools in GROUPS.values() for t in tools} == set(TOOL_SETS["full"])


def test_toolbox_sees_its_stores_asof(tools):
    assert tools.asof == tools.store.asof
