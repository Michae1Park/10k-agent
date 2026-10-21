"""V0 validator: an Item heading set in a one-row table (AMZN) is an `item_heading`."""

from tenk_agent.preprocess.blocks import Block
from tenk_agent.preprocess.validation import is_item_table, role_fits_block


def table(*rows: list[str]) -> Block:
    return Block(
        id="b0053", kind="table", text="\n".join(" | ".join(r) for r in rows), rows=list(rows)
    )


def test_one_row_item_table_can_be_item_heading():
    b = table(["Item 1A.", "Risk Factors"])
    assert is_item_table(b)
    assert role_fits_block(b, {"role": "item_heading", "item": "1A"}, None) is None


def test_other_tables_still_cannot_be_item_heading():
    for b in (
        table(["Net sales", "416,161"]),
        table(["Item 1.", "Business"], ["Item 1A.", "Risk"]),
    ):
        assert not is_item_table(b)
        assert role_fits_block(b, {"role": "item_heading", "item": "1"}, None) is not None
