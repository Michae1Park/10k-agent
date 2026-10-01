import numpy as np

from tenk_agent.chunking import chunk_sections
from tenk_agent.corpus import resolve_company
from tenk_agent.edgar import Filing
from tenk_agent.evaluation.retrieval import GoldChunks, recall_at_k, reciprocal_rank
from tenk_agent.parse import Block, Section
from tenk_agent.retrieval import Retriever, _fuse, companies_in, years_in
from tenk_agent.store import Store


def filing(ticker: str, year: int, period_end: str) -> Filing:
    return Filing(
        ticker, 1, f"{ticker}-{year}", "2025-01-01", period_end, year, "x.htm", "https://x"
    )


def make_store(tmp_path) -> Store:
    store = Store(tmp_path / "t.db")
    for ticker, year, end in [
        ("AAPL", 2024, "2024-09-28"),
        ("AAPL", 2025, "2025-09-27"),
        ("NVDA", 2024, "2024-01-28"),
        ("NVDA", 2025, "2025-01-26"),
    ]:
        sections = [
            Section("7", "MD&A", [Block("paragraph", f"{ticker} revenue grew in fiscal {year}.")])
        ]
        store.add_filing(
            filing(ticker, year, end),
            ticker,
            sections,
            chunk_sections(f"{ticker}-FY{year}", sections),
        )
    store.rebuild_search_index()
    return store


def test_company_aliases_resolve_to_corpus_tickers():
    assert resolve_company("Google").ticker == "GOOGL"
    assert resolve_company("Facebook's").ticker == "META"
    assert resolve_company("Alphabet Inc.").ticker == "GOOGL"
    assert resolve_company("Oracle") is None
    assert companies_in("Compare Apple's and Microsoft's R&D") == ["AAPL", "MSFT"]
    assert companies_in("What did Amazon Web Services earn?") == ["AMZN"]


def test_years_in_expands_ranges_and_marks_fiscal_years():
    assert years_in("R&D from 2023–2025") == [(2023, False), (2024, False), (2025, False)]
    assert years_in("in fiscal 2024") == [(2024, True)]
    assert years_in("FY2025 revenue") == [(2025, True)]
    assert [y for y, _ in years_in("over the last three years")] == [2023, 2024, 2025]


def test_calendar_year_includes_january_fiscal_year_end(tmp_path):
    retriever = Retriever(make_store(tmp_path), mode="keyword")
    assert retriever.slots("NVIDIA revenue in 2024") == [("NVDA", [2024, 2025])]
    assert retriever.slots("NVIDIA revenue in fiscal 2024") == [("NVDA", [2024])]
    assert retriever.slots("Apple revenue in 2024") == [("AAPL", [2024])]
    assert retriever.slots("revenue in 2024") == [(None, None)]


def test_slot_search_returns_evidence_from_every_filing(tmp_path):
    retriever = Retriever(make_store(tmp_path), mode="keyword")
    hits = retriever.search("Apple and NVIDIA revenue in fiscal 2024 and fiscal 2025", k=4)
    assert sorted(c.id for c in hits) == [
        "AAPL-FY2024-7-000",
        "AAPL-FY2025-7-000",
        "NVDA-FY2024-7-000",
        "NVDA-FY2025-7-000",
    ]


def test_vector_search_orders_by_cosine_distance(tmp_path):
    store = make_store(tmp_path)
    ids = [c.id for c in store.chunks()]
    store.reset_embeddings("test", 2)
    vectors = np.array([[1, 0], [0.8, 0.6], [0, 1], [-1, 0]], dtype=np.float32)
    store.add_embeddings(ids, vectors)
    hits = store.vector_search(np.array([1, 0], dtype=np.float32), k=2)
    assert [c.id for c in hits] == ids[:2]
    assert (
        store.vector_search(np.array([1, 0], dtype=np.float32), tickers=["NVDA"], k=1)[0].id
        == ids[2]
    )


def test_rrf_fusion_rewards_agreement():
    store_chunks = {name: type("C", (), {"id": name})() for name in "abc"}
    fused = _fuse([[store_chunks["a"], store_chunks["b"]], [store_chunks["b"], store_chunks["c"]]])
    assert [c.id for c in fused][0] == "b"


def test_recall_counts_sources_and_mrr_uses_first_relevant_rank():
    gold = GoldChunks([{"x1", "x2"}, {"y1"}])
    assert recall_at_k(["x2", "z", "q"], gold, 5) == 0.5
    assert recall_at_k(["z", "y1", "x1"], gold, 5) == 1.0
    assert reciprocal_rank(["z", "q", "y1"], gold) == 1 / 3
    assert reciprocal_rank(["z"], gold) == 0.0
