"""KPI extraction (facts/extract.py) with a scripted model, and layer-1 scoring."""

import json

import pytest

from tenk_agent.asof import AsOf
from tenk_agent.corpus import Company
from tenk_agent.evaluation.extraction import score
from tenk_agent.facts import extract
from tenk_agent.models import Response
from tenk_agent.retrieval.index import load
from tenk_agent.store import Store

TABLE = "| Gross Bookings | $46,756 | $58,022 | 24% | 22% |\n| Trips | 3,268 | 3,867 | 18% |"
OUTLOOK = "For Q3 2026, we anticipate: Gross Bookings of $58.25 billion to $60.25 billion."


@pytest.fixture(autouse=True)
def uber_in_corpus(monkeypatch):  # the default corpus is V3's; these tests load a V4 release
    monkeypatch.setattr(
        "tenk_agent.retrieval.index.company_by_ticker",
        lambda t: Company("UBER", 1543151, "Uber Technologies, Inc."),
    )


def release_store(tmp_path):
    meta = {
        "ticker": "UBER",
        "fiscal_year": 2026,
        "fiscal_period": "Q2",
        "form": "ER",
        "accession_no": "a",
        "period_end_date": "2026-06-30",
        "filing_date": "2026-08-05",
        "documents": [{"url": "u"}],
    }
    chunks = [
        {"item": "EX99", "kind": "table", "text": TABLE},
        {"item": "EX99", "kind": "prose", "text": OUTLOOK},
    ]
    load(tmp_path / "r.db", [(meta, {"sections": [{"item": "EX99", "title": "t"}]}, chunks)])
    return Store(tmp_path / "r.db", AsOf.of("2026-08-06"))


class Scripted:
    name = "scripted"

    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def complete(self, messages, tools=None, system=None, max_tokens=8000, tool_choice=None):
        self.seen.append(messages)
        return Response(
            text=json.dumps(self.replies.pop(0)),
            tool_calls=[],
            input_tokens=10,
            output_tokens=5,
            latency_s=0,
            model=self.name,
            stop_reason="stop",
        )


GOOD_GB = {
    "metric": "gross_bookings",
    "segment": "total",
    "value": 58022,
    "unit": "USD millions",
    "chunk_id": "UBER-2026Q2-ER-EX99-000",
    "quote": "| Gross Bookings | $46,756 | $58,022 | 24% | 22% |",
}
GUIDE = {
    "metric": "gross_bookings",
    "segment": "total",
    "target_period": "Q3 2026",
    "low": 58250,
    "high": 60250,
    "unit": "USD millions",
    "basis": "level",
    "chunk_id": "UBER-2026Q2-ER-EX99-001",
    "quote": "Gross Bookings of $58.25 billion to $60.25 billion",
}


def test_valid_rows_are_kept_and_bad_ones_repaired_once(tmp_path):
    store = release_store(tmp_path)
    wrong_value = {
        **GOOD_GB,
        "metric": "trips",
        "segment": "total",
        "value": 3268,  # prior year
        "quote": "| Trips | 3,268 | 3,867 | 18% |",
    }
    invented = {**GOOD_GB, "metric": "mapcs", "value": 208, "quote": "MAPCs of 208 million"}
    fixed = {**wrong_value, "value": 3867}
    model = Scripted(
        {"kpis": [GOOD_GB, wrong_value, invented], "guidance": [GUIDE]},
        {"kpis": [fixed, invented], "guidance": []},
    )
    out = extract.extract(model, store, "UBER", "2026Q2")
    assert out.calls == 2 and "failed the checks" in model.seen[1][-1]["content"]
    assert {k["definition_version"] for k in out.kpis} == {1}
    assert {(k["metric"], k["value"]) for k in out.kpis} == {
        ("gross_bookings", 58022),
        ("trips", 3867),
    }
    assert out.guidance == [{k: GUIDE[k] for k in extract._clean(GUIDE, "guidance")}]
    assert [e["row"]["metric"] for e in out.exceptions] == ["mapcs"]  # quote not in the chunk


def test_validate_rejects_unknown_metric_segment_and_value_not_in_quote(tmp_path):
    store = release_store(tmp_path)
    chunks = {c.id: c for c in extract.release_chunks(store, "UBER", "2026Q2")}
    entry = extract.registry()["UBER"]
    assert extract.validate(GOOD_GB, "kpi", chunks, entry) is None
    assert "registry" in extract.validate({**GOOD_GB, "metric": "x"}, "kpi", chunks, entry)
    assert "segment" in extract.validate({**GOOD_GB, "segment": "x"}, "kpi", chunks, entry)
    assert "not in the quote" in extract.validate({**GOOD_GB, "value": 5}, "kpi", chunks, entry)
    assert extract.validate(GUIDE, "guidance", chunks, entry) is None
    assert extract.validate({**GUIDE, "high": None}, "guidance", chunks, entry) is None  # a floor
    assert "low" in extract.validate({**GUIDE, "low": None}, "guidance", chunks, entry)


def test_release_chunks_only_from_that_release_and_visible(tmp_path):
    store = release_store(tmp_path)
    assert [c.id for c in extract.release_chunks(store, "UBER", "2026Q2")] == [
        "UBER-2026Q2-ER-EX99-000",
        "UBER-2026Q2-ER-EX99-001",
    ]
    early = Store(tmp_path / "r.db", AsOf.of("2026-08-05"))
    assert extract.release_chunks(early, "UBER", "2026Q2") == []


def test_score_kpis_guidance_and_critical():
    gold = [
        {
            "id": "a",
            "ticker": "UBER",
            "period": "2026Q2",
            "metric": "gross_bookings",
            "segment": "total",
            "value": 58022.0,
            "unit": "USD millions",
        },
        {
            "id": "b",
            "ticker": "UBER",
            "period": "2026Q2",
            "metric": "guide.gross_bookings.high",
            "segment": None,
            "value": 60250.0,
            "unit": "USD millions",
            "target_period": "2026Q3",
        },
        {
            "id": "c",
            "ticker": "UBER",
            "period": "2026Q2",
            "metric": "trips",
            "segment": None,
            "value": 3867.0,
            "unit": "count millions",
        },
    ]
    got = {("UBER", "2026Q2"): {"kpis": [GOOD_GB], "guidance": [GUIDE]}}
    result = score(gold, got)
    assert result["exact"] == 2 / 3 and result["critical"] == 1.0 and not result["pass"]
    assert [m["id"] for m in result["misses"]] == ["c"]


def test_growth_pair_finds_the_later_column():
    assert extract.growth_pair("| Trips | 3,268 | 3,867 | 18% |") == (3268, 3867)
    assert extract.growth_pair("| Freight | (19) | (20) | (5)% |") == (19, 20)
    assert extract.growth_pair("| Brand Tripadvisor (1) | $235 | $255 | (8)% |") == (255, 235)
    assert extract.growth_pair("| Viator | $294 | $270 | 9% |") == (270, 294)
    assert extract.growth_pair("| Freight | 1,273 | 1,267 | (1)% |") == (1273, 1267)
    assert extract.growth_pair("Gross Bookings of $58.25 billion to $60.25 billion") is None
    assert (
        extract.growth_pair("| Diluted | $1.20 | $3.11 | $1.36 | $4.57 |") is None
    )  # no growth column


def test_definition_version_follows_the_recast():
    spec = {"definitions": {"2026Q2": 2}}
    assert extract.definition_version(spec, "2026Q1") == 1
    assert extract.definition_version(spec, "2026Q2") == 2
    assert extract.definition_version({}, "2026Q2") == 1


def test_registry_covers_all_eight_companies():
    reg = extract.registry()
    assert set(reg) == {"BKNG", "EXPE", "ABNB", "TRIP", "UBER", "LYFT", "DASH", "CART"}
    for ticker, entry in reg.items():
        for spec in [*entry["kpis"].values(), *(entry.get("guidance") or {}).values()]:
            assert spec["unit"], ticker
