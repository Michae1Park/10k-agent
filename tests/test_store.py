"""The SQLite store, as V1's index loads it: chunk ids and metadata, children, search filters."""

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
