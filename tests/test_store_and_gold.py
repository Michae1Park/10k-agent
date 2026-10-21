from tenk_agent.evaluation import gold
from tenk_agent.retrieval.index import load
from tenk_agent.store import Store


def filing(ticker: str, year: int, period_end: str, chunks: list[dict]) -> tuple:
    """(metadata.json, document, chunks) for one filing, as tenk_agent.retrieval.index.load
    takes them."""
    meta = {
        "ticker": ticker,
        "fiscal_year": year,
        "accession_no": f"{ticker}-{year}",
        "period_end_date": period_end,
        "filing_date": "2025-11-01",
        "source_url": f"https://example.com/{ticker}-{year}.htm",
    }
    items = dict.fromkeys(c["item"] for c in chunks)
    document = {"sections": [{"item": i, "title": f"Item {i}"} for i in items]}
    return meta, document, chunks


def prose(item: str, text: str, **extra) -> dict:
    return {"item": item, "kind": "prose", "text": text} | extra


APPLE = [
    prose("1A", "Tariffs could affect our supply chain.", pages=[12, 12]),
    {
        "item": "8",
        "kind": "table",
        "text": "(In millions)\n| 2025 | 2024 |\n| Research and development | $34,550 | $31,370 |",
        "units": "USD millions",
        "years_covered": [2024, 2025],
        "children": [{"kind": "row", "text": "Research and development: 2025 = $34,550"}],
    },
]


def make_store(tmp_path) -> Store:
    load(tmp_path / "test.db", [filing("AAPL", 2025, "2025-09-27", APPLE)])
    return Store(tmp_path / "test.db")


def test_load_numbers_chunks_per_item_and_keeps_metadata(tmp_path):
    store = make_store(tmp_path)
    assert [c.id for c in store.chunks()] == ["AAPL-FY2025-1A-000", "AAPL-FY2025-8-000"]
    table = store.get_chunk("AAPL-FY2025-8-000")
    assert (table.units, table.years_covered) == ("USD millions", [2024, 2025])
    assert store.get_chunk("AAPL-FY2025-1A-000").pages == [12, 12]
    assert store.filing("AAPL", 2025).company_name == "Apple Inc."
    assert store.section_titles("AAPL", 2025) == {"1A": "Item 1A", "8": "Item 8"}


def test_parent_child_children_are_searched_and_point_to_their_parent(tmp_path):
    store = make_store(tmp_path)
    assert store.has_children()
    [child] = store.child_search("research development")
    assert child.parent_id == "AAPL-FY2025-8-000"


def test_search_filters_by_company_year_and_item(tmp_path):
    store = make_store(tmp_path)
    assert [c.item for c in store.search("tariffs supply chain")] == ["1A"]
    assert store.search("tariffs", items=["8"]) == []
    assert store.search("tariffs", fiscal_years=[2024]) == []


def test_reloading_rebuilds_the_database(tmp_path):
    make_store(tmp_path)
    assert len(make_store(tmp_path).chunks("AAPL", 2025)) == 2


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
