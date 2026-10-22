"""Evaluation: gold questions (validation, sign-off, evidence → chunks) and retrieval metrics."""

from datetime import date

from tenk_agent.evaluation import gold
from tenk_agent.evaluation.retrieval import GoldChunks, recall_at, reciprocal_rank
from test_store import make_store


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
    questions = [valid_question()]
    unknown = gold.mark_verified(questions, ["q1", "nope"], "MP", date(2026, 10, 1))
    assert unknown == ["nope"]
    assert gold.is_verified(questions[0])
    assert questions[0]["verified_on"] == "2026-10-01"


def test_recall_counts_sources_and_mrr_uses_first_relevant_rank():
    gold = GoldChunks([{"x1", "x2"}, {"y1"}])
    assert recall_at(["x2", "z", "q"], gold, 5) == 0.5
    assert recall_at(["z", "y1", "x1"], gold, 5) == 1.0
    assert reciprocal_rank(["z", "q", "y1"], gold) == 1 / 3
    assert reciprocal_rank(["z"], gold) == 0.0
