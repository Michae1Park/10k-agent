"""Rule-based structure for 10-Qs and releases (preprocess/rules.py)."""

from tenk_agent.preprocess.blocks import Block
from tenk_agent.preprocess.forms import RELEASE, TEN_Q
from tenk_agent.preprocess.rules import label_blocks, label_release, release_blocks


def para(i, text, emph="B", **kw):
    return Block(id=f"b{i:04d}", text=text, emph=emph, **kw)


def roles(labels):
    return [(lb["role"], lb.get("item")) for lb in labels.values()]


def test_ten_q_items_carry_their_part_and_skip_toc_links():
    blocks = [
        para(1, "Item 2. MD&A", link=True),  # table of contents
        para(2, "PART I - FINANCIAL INFORMATION"),
        para(3, "Item 1. Financial Statements"),
        para(4, "Revenue grew.", emph="-"),
        para(5, "I tem 2. Management's Discussion"),  # TRIP's split initial
        para(6, "Part II. Item 1A. Risk Factors"),  # EXPE: Part and Item on one line
        para(7, "Item 1. Financial Statements"),  # backwards: not a new section
    ]
    assert roles(label_blocks(blocks, TEN_Q)) == [
        ("body", None),
        ("part_heading", None),
        ("item_heading", "I.1"),
        ("body", None),
        ("item_heading", "I.2"),
        ("item_heading", "II.1A"),
        ("body", None),
    ]


def test_tables_get_units_and_page_numbers_are_furniture():
    blocks = [
        para(1, "(in millions, except per share)", emph="-"),
        Block(id="b0002", kind="table", text="a | b\nc | d", rows=[["a", "b"], ["c", "d"]]),
        para(3, "12", emph="-", pb=True),
    ]
    labels = label_blocks(blocks, TEN_Q)
    assert labels["b0002"] == {
        "id": "b0002",
        "role": "data_table",
        "units": "USD millions",
        "units_from": "caption",
    }
    assert labels["b0003"]["role"] == "furniture"


def test_release_exhibits_become_pseudo_items():
    blocks = release_blocks([("EX-99.1", "<p>Revenue up</p>"), ("EX-99.2", "<p>Letter</p>")])
    labels = label_release(blocks, RELEASE)
    items = [lb["item"] for lb in labels.values() if lb["role"] == "item_heading"]
    assert items == ["EX99", "EX99.2"]
    assert [b.id for b in blocks][0] == "x1"


def test_ten_k_quirks_and_statement_headings():
    from tenk_agent.preprocess.forms import TEN_K

    blocks = [
        para(1, "Ite m 1A. Risk Factors"),  # TRIP
        para(2, "Part III. Item 10. Directors", emph="-"),  # EXPE: not bold, Part on the line
        para(3, "CONSOLIDATED BALANCE SHEETS"),
        para(4, "Report of Independent Registered Public Accounting Firm"),
    ]
    labels = label_blocks(blocks, TEN_K)
    assert [lb.get("item") or lb.get("kind") for lb in labels.values()] == [
        "1A", "10", "statement", "auditor_report",
    ]
