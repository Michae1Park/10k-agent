import numpy as np

from tenk_agent.ask import fit_context
from tenk_agent.config import db_file
from tenk_agent.edgar import Filing
from tenk_agent.hierarchy import HierarchySizes, build
from tenk_agent.parse import Block, Section
from tenk_agent.retrieval import Retriever, items_in
from tenk_agent.store import Store
from tenk_agent.tables import table_rows

FILING = Filing(
    "MSFT", 789019, "0000950170-25-100235", "2025-07-30", "2025-06-30", 2025, "m.htm", "https://x"
)

SEGMENTS = [
    ["(In millions)"],
    ["Year Ended June 30,", "2025", "2024", "2023"],
    ["Productivity and Business Processes"],
    ["Revenue", "$120,810", "$106,820", "$94,151"],
    ["Intelligent Cloud"],
    ["Revenue", "$106,265", "$87,464", "$72,944"],
    ["Operating income", "$44,589", "$37,813", "$28,411"],
]


def test_rows_carry_their_group_and_each_columns_fiscal_year():
    rows = table_rows(SEGMENTS)

    assert [(r.group, r.label) for r in rows] == [
        ("Productivity and Business Processes", "Revenue"),
        ("Intelligent Cloud", "Revenue"),
        ("Intelligent Cloud", "Operating income"),
    ]
    assert rows[1].value_for(2024) == "$87,464"
    assert rows[1].text() == (
        "Intelligent Cloud › Revenue: FY2025 = $106,265; FY2024 = $87,464; FY2023 = $72,944"
    )


def test_oldest_first_columns_and_change_columns():
    rows = table_rows(
        [
            ["Year Ended December 31,"],
            ["2022", "2023", "2024"],
            ["Operating lease cost", "$8,847", "$10,550", "$11,961"],
        ]
    )
    assert rows[0].value_for(2024) == "$11,961"

    tesla = table_rows(
        [
            ["(Dollars in millions)", "2024", "2023", "2024 vs. 2023 Change"],
            ["Automotive regulatory credits", "2,763", "1,790", "973", "54%"],
        ]
    )
    assert tesla[0].value_for(2024) == "2,763"
    assert [c.fiscal_year for c, _ in tesla[0].cells] == [2024, 2023, None, None]


def test_transposed_segment_table_takes_the_year_above_the_columns():
    rows = table_rows(
        [
            ["2025"],
            ["Americas", "Europe", "Japan", "Total"],
            ["Net sales", "$178,353", "$111,032", "$28,703", "$416,161"],
        ]
    )
    cells = rows[0].cells
    assert [(c.label, c.fiscal_year, v) for c, v in cells][2] == ("Japan", 2025, "$28,703")


def test_a_total_row_closes_its_group():
    rows = table_rows(
        [
            ["2025", "2024"],
            ["Net sales:"],
            ["Products", "$307,003", "$294,866"],
            ["Total net sales", "416,161", "391,035"],
            ["Research and development", "34,550", "31,370"],
        ]
    )
    assert [r.group for r in rows] == ["Net sales", "", ""]


def segment_sections() -> list[Section]:
    prose = [Block("paragraph", f"Sentence {i} about cloud demand." + " x" * 60) for i in range(40)]
    return [
        Section("7", "MD&A", prose),
        Section(
            "8",
            "Financial Statements",
            [
                Block("paragraph", "Segment revenue and operating income were as follows:"),
                Block("table", rows=SEGMENTS),
                Block("table", rows=[["2024"], ["Revenue", "$1", "$2", "$3"]]),
            ],
        ),
    ]


def test_parents_and_children():
    parents, children = build("MSFT-FY2025", segment_sections(), HierarchySizes(2000, 400, 7200))

    prose = [p for p in parents if p.kind == "prose"]
    assert len(prose) > 1 and all(len(p.text) <= 2000 for p in prose)
    table = next(p for p in parents if p.kind == "table")
    rows = [c for c in children if c.parent_id == table.id and c.kind == "row"]
    assert len(rows) == 3
    assert "Intelligent Cloud › Revenue" in rows[1].text
    assert rows[1].row["cells"][1] == ["2024", 2024, "$87,464"]
    # Every prose child belongs to a parent, and is small.
    prose_children = [c for c in children if c.kind == "prose"]
    assert {c.parent_id for c in prose_children} == {p.id for p in prose}
    assert all(len(c.text) <= 600 for c in prose_children)


def test_a_continuation_table_inherits_units():
    parents, _ = build("MSFT-FY2025", segment_sections())
    tables = [p for p in parents if p.kind == "table"]
    assert tables[0].units == "USD millions"
    assert tables[1].units == "USD millions"  # no caption of its own


class OneHot:
    """Embeds each text onto the axis of the first keyword it contains."""

    name = "test:one-hot"
    WORDS = ("cloud", "revenue", "operating", "zzz")

    def _vector(self, text: str) -> np.ndarray:
        lowered = text.lower()
        index = next((i for i, w in enumerate(self.WORDS) if w in lowered), 3)
        return np.eye(len(self.WORDS))[index]

    def embed_documents(self, texts):
        return np.array([self._vector(t) for t in texts])

    def embed_query(self, text):
        return self._vector(text)


def test_children_are_searched_and_parents_returned(tmp_path):
    from tenk_agent.retrieval import embed_corpus

    store = Store(db_file(tmp_path, "parent_child"))
    parents, children = build("MSFT-FY2025", segment_sections())
    store.add_filing(FILING, "Microsoft", segment_sections(), parents, children=children)
    store.rebuild_search_index()
    embedder = OneHot()
    embed_corpus(store, embedder)

    retriever = Retriever(store, "hybrid", embedder, search_children=True)
    found = retriever.search("Intelligent Cloud operating income", tickers=["MSFT"], k=3)

    assert store.has_children()
    assert len({c.id for c in found}) == len(found)  # each parent once
    assert all(c.id in {p.id for p in parents} for c in found)
    table = next(c for c in found if c.kind == "table")
    assert [r.row["label"] for r in store.child_rows(table.id)] == [
        "Revenue",
        "Revenue",
        "Operating income",
    ]


def test_reingest_replaces_children(tmp_path):
    store = Store(db_file(tmp_path, "parent_child"))
    for _ in range(2):
        parents, children = build("MSFT-FY2025", segment_sections())
        store.add_filing(FILING, "Microsoft", segment_sections(), parents, children=children)
    count = store.db.execute("SELECT count(*) FROM children").fetchone()[0]
    assert count == len(children)


def test_the_naive_database_gets_no_children_table(tmp_path):
    store = Store(db_file(tmp_path))
    assert not store.has_children()
    assert db_file(tmp_path).name == "tenk.db"


def test_items_named_in_a_question():
    assert items_in("What does Item 7A say about interest rates?") == ["7A"]
    assert items_in("Summarize Tesla's risk factors") == ["1A"]
    assert items_in("What was Apple's revenue?") == []


class Text:
    def __init__(self, n):
        self.text = "x" * n


def test_fit_context_keeps_leading_chunks_within_the_budget():
    chunks = [Text(3000), Text(3000), Text(3000)]
    assert len(fit_context(chunks, 7000)) == 2
    assert len(fit_context(chunks, 0)) == 3
    assert len(fit_context([Text(9000)], 1000)) == 1  # never empty


def segment_store(tmp_path, layout="parent_child") -> tuple[Store, str]:
    from tenk_agent.chunking import chunk_sections

    store = Store(db_file(tmp_path, layout))
    if layout == "parent_child":
        parents, children = build("MSFT-FY2025", segment_sections())
    else:
        parents, children = chunk_sections("MSFT-FY2025", segment_sections()), None
    store.add_filing(FILING, "Microsoft", segment_sections(), parents, children=children)
    table = next(p for p in parents if p.kind == "table")
    return store, table.id


def claim(value, year, chunk_id, company="Microsoft"):
    return {
        "value": value,
        "unit": "USD millions",
        "company": company,
        "fiscal_year": year,
        "chunk_ids": [chunk_id],
    }


def test_a_value_must_sit_in_its_fiscal_years_column(tmp_path):
    from tenk_agent.verify import verify_claim

    store, table = segment_store(tmp_path)

    assert verify_claim(claim(87464, 2024, table), store)["status"] == "verified"
    wrong_year = verify_claim(claim(87464, 2025, table), store)
    assert wrong_year["status"] == "unverified"
    assert wrong_year["status_reason"] == "found '$87,464' under FY2024, claim says FY2025"


def test_a_value_from_another_companys_filing_fails(tmp_path):
    from tenk_agent.verify import verify_claim

    store, table = segment_store(tmp_path)
    result = verify_claim(claim(87464, 2024, table, company="Apple"), store)

    assert result["status"] == "unverified"
    assert result["status_reason"] == "value found only in MSFT filings"


def test_the_naive_database_keeps_value_only_verification(tmp_path):
    from tenk_agent.verify import verify_claim

    store, table = segment_store(tmp_path, layout="flat")
    assert verify_claim(claim(87464, 2025, table), store)["status"] == "verified"
