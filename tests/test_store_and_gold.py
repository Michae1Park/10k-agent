from tenk_agent import gold
from tenk_agent.chunking import chunk_sections
from tenk_agent.edgar import Filing
from tenk_agent.parse import Block, Section
from tenk_agent.store import Store

FILING = Filing(
    "AAPL",
    320193,
    "0000320193-25-000079",
    "2025-10-31",
    "2025-09-27",
    2025,
    "aapl.htm",
    "https://example.com/aapl.htm",
)


def make_store(tmp_path) -> Store:
    sections = [
        Section(
            "1A", "Risk Factors", [Block("paragraph", "Tariffs could affect our supply chain.")]
        ),
        Section(
            "8",
            "Financial Statements",
            [
                Block("paragraph", "(In millions)"),
                Block(
                    "table",
                    rows=[["2025", "2024"], ["Research and development", "$34,550", "$31,370"]],
                ),
            ],
        ),
    ]
    store = Store(tmp_path / "test.db")
    store.add_filing(FILING, "Apple Inc.", sections, chunk_sections("AAPL-FY2025", sections))
    store.rebuild_search_index()
    return store


def test_search_filters_by_company_year_and_item(tmp_path):
    store = make_store(tmp_path)
    assert [c.item for c in store.search("tariffs supply chain")] == ["1A"]
    assert store.search("tariffs", items=["8"]) == []
    assert store.search("tariffs", fiscal_years=[2024]) == []


def test_reingesting_a_filing_replaces_its_chunks(tmp_path):
    store = make_store(tmp_path)
    before = len(store.chunks("AAPL", 2025))
    make_store(tmp_path)
    assert len(Store(tmp_path / "test.db").chunks("AAPL", 2025)) == before


def test_evidence_resolves_to_the_table_chunk(tmp_path):
    store = make_store(tmp_path)
    question = {
        "sources": [
            {
                "company": "AAPL",
                "fiscal_year": 2025,
                "evidence": "Research and development | 34,550 | 31,370",
            }
        ]
    }
    [[chunk_id]] = gold.resolve_evidence(store, question)
    assert store.get_chunk(chunk_id).kind == "table"


def valid_question(**overrides) -> dict:
    q = {
        "id": "q1",
        "split": "dev",
        "category": "retrieval",
        "failure_mode": None,
        "question": "What was Apple's R&D in fiscal 2025?",
        "answer_type": "number",
        "expected_answer": {"value": 34550, "unit": "USD millions"},
        "origin": "manual",
        "sources": [{"company": "AAPL", "fiscal_year": 2025, "evidence": "x"}],
    }
    q.update(overrides)
    return q


def test_validate_accepts_a_well_formed_question():
    assert gold.validate([valid_question()]) == []


def test_validate_reports_structural_problems():
    problems = gold.validate(
        [
            valid_question(category="failure_case"),
            valid_question(id="q2", expected_answer={"value": 1, "unit": "USD bn"}),
            valid_question(
                id="q2", sources=[{"company": "ORCL", "fiscal_year": 2025, "evidence": "x"}]
            ),
        ]
    )
    assert any("failure_mode is required" in p for p in problems)
    assert any("unknown unit" in p for p in problems)
    assert any("outside the corpus" in p for p in problems)
    assert any("duplicate id" in p for p in problems)


def test_mark_verified_records_reviewer_and_reports_unknown_ids():
    from datetime import date

    questions = [valid_question()]
    unknown = gold.mark_verified(questions, ["q1", "nope"], "MP", date(2026, 10, 1))
    assert unknown == ["nope"]
    assert gold.is_verified(questions[0])
    assert questions[0]["verified_on"] == "2026-10-01"
