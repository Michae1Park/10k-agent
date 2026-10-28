"""V4 Ask (tool set "ask"): data ids resolve and verify, and the data tools read as of the store."""

from tenk_agent.agent.loop import build_system
from tenk_agent.agent.tools import TOOL_SETS, Toolbox
from tenk_agent.asof import AsOf
from tenk_agent.facts.xbrl import company_facts
from tenk_agent.generation.verify import verify_claim
from tenk_agent.research.refs import Resolver
from tenk_agent.store import Store, target_key
from test_agent_tools import make_store

KPI = {"metric": "units", "segment": "total", "value": 61.2, "unit": "count millions",
       "chunk_id": "AAPL-2025Q3-ER-EX99-002", "quote": "iPhone units 61.2"}
GUIDE = {"metric": "revenue_growth", "target_period": "Q4 2025", "low": 5, "high": 7,
         "unit": "percent", "basis": "growth_yoy", "chunk_id": "AAPL-2025Q3-ER-EX99-001",
         "quote": "revenue growth of 5% to 7%"}
REVENUE = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
    {"start": "2025-07-01", "end": "2025-09-30", "val": 94.04e9, "accn": "a", "fy": 2025,
     "fp": "Q3", "form": "10-Q", "filed": "2025-08-01"},
]}}}}}


def ask(tmp_path, asof: str) -> Toolbox:
    store = make_store(tmp_path)  # AAPL FY2025 10-K, filed 2025-10-31
    store.enable_facts()
    store.add_extractions("AAPL", "2025Q3", "2025-07-31", [KPI], [GUIDE])
    store.add_facts(company_facts("AAPL", REVENUE, "2023-01-01"))
    store.add_market([("px:AAPL:close", "2025-11-03", 270.4, "2025-11-03"),
                      ("px:AAPL:close", "2025-11-04", 268.0, "2025-11-04")])
    return Toolbox(Store(tmp_path / "test.db", AsOf.of(asof)), None, None, "ask")


def test_guidance_targets_become_period_keys():
    assert [target_key(p) for p in ("Q3 2026", "Full-year 2026", "FY 2025", "Fiscal Year 2025",
                                    "2023", "next year")] == [
        "2026Q3", "FY2026", "FY2025", "FY2025", "FY2023", "next year"]


def test_data_ids_resolve_as_of_the_store(tmp_path):
    box = ask(tmp_path, "2025-11-04")
    resolver = Resolver(box.store)
    assert resolver.resolve("kpi:AAPL:units:2025Q3").values[0]["value"] == 61.2
    guide = resolver.resolve("guide:AAPL:revenue_growth:2025Q4")
    assert [v["value"] for v in guide.values] == [5, 7]
    assert resolver.resolve("q:AAPL:revenue:2025Q3").values[0]["value"] == 94.04e9
    assert resolver.resolve("px:AAPL:2025-11-03").values[0]["value"] == 270.4
    assert resolver.resolve("px:AAPL:2025-11-04") is None  # published on the as-of date
    assert Resolver(ask(tmp_path, "2025-07-31").store).resolve("kpi:AAPL:units:2025Q3") is None


def test_claims_verify_against_data_rows(tmp_path):
    store = ask(tmp_path, "2025-11-04").store

    def status(value, unit, ref):
        claim = {"company": "AAPL", "value": value, "unit": unit, "chunk_ids": [ref]}
        return verify_claim(claim, store)["status"]

    assert status(61.2, "count millions", "kpi:AAPL:units:2025Q3") == "verified"
    assert status(94.0, "USD billions", "q:AAPL:revenue:2025Q3") == "verified"
    assert status(7, "percent", "guide:AAPL:revenue_growth:2025Q4") == "verified"
    assert status(8, "percent", "guide:AAPL:revenue_growth:2025Q4") == "unverified"
    reason = verify_claim({"company": "AAPL", "value": 1, "unit": "count millions",
                           "chunk_ids": ["kpi:AAPL:units:2024Q1"]}, store)["status_reason"]
    assert "unknown chunk IDs ['kpi:AAPL:units:2024Q1']" in reason


def test_ask_tools(tmp_path):
    box = ask(tmp_path, "2025-11-04")
    assert set(TOOL_SETS["full"]) == {"search_filings", "calculator", "xbrl_facts", "edgar_filings"}
    kpis = box.call("company_kpis", {"company": "Apple"})
    assert [r["id"] for r in kpis["rows"]] == ["kpi:AAPL:units:2025Q3"]
    assert "metrics" in box.call("financials", {"company": "AAPL", "metric": "bogus"})
    revenue = box.call("financials", {"company": "AAPL", "metric": "revenue"})["rows"]
    assert [(r["period"], r["derivation"]) for r in revenue] == [("2025Q3", "direct")]
    guide = box.call("guidance", {"company": "AAPL", "periods": ["2025Q4"]})["rows"]
    assert guide[0]["id"] == "guide:AAPL:revenue_growth:2025Q4"
    assert guide[0]["issued_in"] == "2025Q3"
    price = box.call("market_data", {"company": "AAPL", "dates": ["2025-11-05"]})["rows"]
    assert price == [{"id": "px:AAPL:2025-11-03", "date": "2025-11-03", "value": 270.4}]
    assert "error" in box.call("company_kpis", {"company": "Nowhere Inc"})
    assert [f["form"] for f in box.call("filings", {"company": "AAPL"})["filings"]] == ["10-K"]


def test_ask_prompt_names_the_as_of_and_coverage(tmp_path):
    system = build_system(ask(tmp_path, "2025-11-04"), 12)
    assert "Today is 2025-11-04" in system and "- AAPL" in system
    assert "{" + "coverage}" not in system


def test_decomposer_takes_years_without_a_fiscal_year_list(tmp_path, monkeypatch):
    from tenk_agent.retrieval import decompose

    monkeypatch.setattr(decompose, "FISCAL_YEARS", None)  # V4 corpora list none
    store = ask(tmp_path, "2025-11-04").store
    assert decompose.slots(store, "Apple guidance for Q3 2026 and FY2025", ["AAPL"]) == [
        ("AAPL", [2025])
    ]
