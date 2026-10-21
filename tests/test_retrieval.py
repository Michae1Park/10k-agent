import numpy as np

from tenk_agent.corpus import resolve_company
from tenk_agent.evaluation.retrieval import GoldChunks, recall_at, reciprocal_rank
from tenk_agent.retrieval.decompose import QueryDecomposer, companies_in, slots, years_in
from tenk_agent.retrieval.index import embed_corpus, load
from tenk_agent.retrieval.search import HybridSearch, SubQuestionRetriever, fuse
from tenk_agent.store import Store
from test_store_and_gold import filing, prose


def make_store(tmp_path) -> Store:
    filings = [
        filing(ticker, year, end, [prose("7", f"{ticker} revenue grew in fiscal {year}.")])
        for ticker, year, end in [
            ("AAPL", 2024, "2024-09-28"),
            ("AAPL", 2025, "2025-09-27"),
            ("NVDA", 2024, "2024-01-28"),
            ("NVDA", 2025, "2025-01-26"),
        ]
    ]
    load(tmp_path / "t.db", filings)
    return Store(tmp_path / "t.db")


class FakeEmbedder:
    name = "fake"

    def embed_documents(self, texts):
        return np.ones((len(texts), 2), dtype=np.float32)

    def embed_query(self, text):
        return np.ones(2, dtype=np.float32)


class FakeReranker:
    """Scores by the words a text shares with the query."""

    name = "fake"

    def scores(self, query, texts):
        words = set(query.lower().split())
        return [len(words & set(t.lower().split())) for t in texts]


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
    store = make_store(tmp_path)
    assert slots(store, "NVIDIA revenue in 2024") == [("NVDA", [2024, 2025])]
    assert slots(store, "NVIDIA revenue in fiscal 2024") == [("NVDA", [2024])]
    assert slots(store, "Apple revenue in 2024") == [("AAPL", [2024])]
    assert slots(store, "revenue in 2024") == [(None, None)]


def test_sub_questions_name_only_their_company_and_fan_out(tmp_path):
    decomposer = QueryDecomposer(make_store(tmp_path))
    subs = decomposer.decompose("Compare Apple's and NVIDIA's revenue in fiscal 2025")
    assert [(s.text, s.ticker, s.fiscal_years) for s in subs] == [
        ("Compare Apple's revenue in fiscal 2025", "AAPL", [2025]),
        ("Compare NVIDIA's revenue in fiscal 2025", "NVDA", [2025]),
    ]
    fanned = decomposer.decompose("Which company grew revenue most in fiscal 2025?")
    assert [s.text for s in fanned] == [
        "Apple Inc. (AAPL): Which company grew revenue most in fiscal 2025?",
        "NVIDIA Corporation (NVDA): Which company grew revenue most in fiscal 2025?",
    ]


def test_sub_question_retrieval_returns_evidence_from_every_filing(tmp_path):
    store = make_store(tmp_path)
    embed_corpus(store, FakeEmbedder())
    retriever = SubQuestionRetriever(
        QueryDecomposer(store), HybridSearch(store, FakeEmbedder(), FakeReranker())
    )
    result = retriever.search("Apple and NVIDIA revenue in fiscal 2024 and fiscal 2025", k=4)
    assert len(result.searches) == 4
    assert sorted(c.id for c in result.chunks) == [
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
    chunks = {name: type("C", (), {"id": name})() for name in "abc"}
    fused = fuse([[chunks["a"], chunks["b"]], [chunks["b"], chunks["c"]]], rrf_k=60)
    assert [c.id for c in fused][0] == "b"


def test_recall_counts_sources_and_mrr_uses_first_relevant_rank():
    gold = GoldChunks([{"x1", "x2"}, {"y1"}])
    assert recall_at(["x2", "z", "q"], gold, 5) == 0.5
    assert recall_at(["z", "y1", "x1"], gold, 5) == 1.0
    assert reciprocal_rank(["z", "q", "y1"], gold) == 1 / 3
    assert reciprocal_rank(["z"], gold) == 0.0
