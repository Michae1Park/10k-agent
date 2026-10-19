"""The V0 chunkers' sentence cuts (C5), table captions (C3) and table row children."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "v0"))

import chunkers  # noqa: E402

chunkers.CAPTION_CHARS = 300


def test_sentences_skip_abbreviations_and_join_back():
    text = "Sales outside the U.S. Department of Commerce rose. Apple Inc. sells; it grows. End"
    pieces = chunkers.sentences(text)
    assert pieces == [
        "Sales outside the U.S. Department of Commerce rose. ",
        "Apple Inc. sells; ",
        "it grows. ",
        "End",
    ]
    assert "".join(pieces) == text


def test_captions_are_the_short_blocks_before_each_table():
    blocks = [
        {"id": "h", "kind": "heading", "text": "Segment Operating Performance"},
        {"id": "c", "kind": "paragraph", "text": "The following table shows net sales:"},
        {"id": "t", "kind": "table", "text": "", "rows": []},
        {"id": "p", "kind": "paragraph", "text": "Americas net sales increased."},
    ]
    section = {"blocks": blocks}
    assert chunkers.captions(section, blocks[1:]) == {"h", "c"}


def test_row_children_name_columns_and_groups():
    rows = [
        ["2025", "Change", "2024"],
        ["Research and development", "$34,550", "10%", "$31,370"],
        ["Percentage of total net sales", "8%", "8%"],
        ["Federal:"],
        ["Current", "$11,487", "5%", "$5,571"],
        ["Total", "9,683", "3%", "2,491"],
        ["Other", "1", "2%", "3"],
    ]
    assert chunkers.header_rows(rows) == 1
    assert chunkers.row_children(rows) == {
        1: "Research and development: 2025 = $34,550; Change = 10%; 2024 = $31,370",
        2: "Percentage of total net sales: 2025 = 8%; 2024 = 8%",
        4: "Federal › Current: 2025 = $11,487; Change = 5%; 2024 = $5,571",
        5: "Federal › Total: 2025 = 9,683; Change = 3%; 2024 = 2,491",
        6: "Other: 2025 = 1; Change = 2%; 2024 = 3",
    }


def test_row_children_without_a_header():
    rows = [["2026", "$12,393"], ["2027", "10,078"]]
    assert chunkers.header_rows(rows) == 0
    assert chunkers.row_children(rows) == {0: "2026: $12,393", 1: "2027: 10,078"}


def test_header_rows():
    dates = [
        ["Years ended"],
        ["September 27, 2025", "September 28, 2024"],
        ["Net income", "$1", "$2"],
    ]
    assert chunkers.header_rows(dates) == 2
    assert chunkers.header_rows([["2025", "2024"], ["Federal:"], ["Current", "$1", "$2"]]) == 1
    assert chunkers.header_rows([["Name", "Role"], ["Tim Cook", "CEO"]]) == 1  # no numbers


def test_row_groups_repeat_the_caption_and_header_only(monkeypatch):
    monkeypatch.setattr(chunkers, "tokens", len)
    monkeypatch.setattr(chunkers, "TABLE_CHARS", 80, raising=False)  # set by configure()
    rows = [["2025", "2024"]] + [[f"Line {n}", f"${n},000", f"${n},500"] for n in range(6)]
    blocks = [
        {"id": "c", "kind": "paragraph", "text": "(In millions)", "heading_path": [], "page": 1},
        {"id": "t", "kind": "table", "text": "", "rows": rows, "heading_path": [], "page": 1},
    ]
    section = {"item": "8", "title": "Financial Statements", "blocks": blocks}
    chunks = chunkers.table_chunks(section, 1, children=True)
    assert len(chunks) > 1
    for c in chunks:
        assert c["text"].startswith("(In millions)\n| 2025 | 2024 |\n| Line ")
        assert c["children"][0] == {
            "kind": "table_title",
            "text": "(In millions)\n| 2025 | 2024 |",
            "tokens": 29,
            "heading_path": [],
        }
    # every data row is in exactly one group, and has exactly one row child
    body = [ln for c in chunks for ln in c["text"].splitlines()[2:]]
    assert body == ["| " + " | ".join(r) + " |" for r in rows[1:]]
    kids = [k["text"] for c in chunks for k in c["children"] if k["kind"] == "row"]
    assert kids == [f"Line {n}: 2025 = ${n},000; 2024 = ${n},500" for n in range(6)]
