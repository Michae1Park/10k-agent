from tenk_agent.chunking import MAX_CHARS, chunk_sections
from tenk_agent.parse import Block, Section


def paragraph(text: str) -> Block:
    return Block("paragraph", text)


def test_tables_are_single_chunks_with_caption_units_and_years():
    table = Block("table", rows=[["2025", "2024"], ["Revenue", "$100", "$90"]])
    section = Section("7", "MD&A", [paragraph("Revenue was as follows (in millions):"), table])

    chunks = chunk_sections("TEST-FY2025", [section])

    assert [c.kind for c in chunks] == ["prose", "table"]
    assert chunks[1].id == "TEST-FY2025-7-001"
    assert chunks[1].text.splitlines() == [
        "Revenue was as follows (in millions):",
        "| 2025 | 2024 |",
        "| Revenue | $100 | $90 |",
    ]
    assert chunks[1].units == "USD millions"
    assert chunks[1].years_covered == [2024, 2025]


def test_prose_is_packed_under_the_limit_with_short_paragraph_overlap():
    paragraphs = [paragraph(f"Paragraph {i}. " + "x" * 500) for i in range(10)]
    chunks = chunk_sections("TEST-FY2025", [Section("1A", "Risk Factors", paragraphs)])

    assert len(chunks) > 1
    assert all(len(c.text) <= MAX_CHARS for c in chunks)
    first_last = chunks[0].text.splitlines()[-1]
    assert chunks[1].text.splitlines()[0] == first_last


def test_chunks_never_cross_sections():
    sections = [
        Section("1", "Business", [paragraph("A.")]),
        Section("1A", "Risks", [paragraph("B.")]),
    ]
    chunks = chunk_sections("TEST-FY2025", sections)
    assert [(c.item, c.text) for c in chunks] == [("1", "A."), ("1A", "B.")]
