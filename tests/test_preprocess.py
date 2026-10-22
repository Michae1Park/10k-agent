"""V0 preprocessing: the structure prompt, its output schema and the validator list the same
values; the validator's rules.

The model never sees structure.schema.json (guided decoding only constrains its tokens), so the
prompt must name every value the schema allows, and the validator must check the same ones.
"""

import json
import re

from tenk_agent.preprocess.blocks import Block
from tenk_agent.preprocess.validation import ITEMS, ROLE_FIELDS, is_item_table, role_fits_block
from tenk_agent.prompts import PROMPTS

PROMPT = (PROMPTS / "structure.md").read_text(encoding="utf-8").split("<!-- END SYSTEM PROMPT")[0]
LABEL = json.loads((PROMPTS / "structure.schema.json").read_text())["properties"]["labels"]["items"]
FIELDS = LABEL["properties"]
ROW_FIELDS = FIELDS["rows"]["items"]["properties"]


def section(heading: str) -> str:
    """The prompt text under `# heading`, up to the next top-level heading."""
    return re.search(rf"^# {heading}\n(.*?)(?=^# |\Z)", PROMPT, re.S | re.M).group(1)


def table_keys(text: str) -> list[str]:
    """The backticked first cells of a Markdown table's rows, header excluded."""
    return re.findall(r"^\| `(\w+)` \|", text, re.M)[1:]


def test_roles_match_schema():
    assert table_keys(section("Roles")) == FIELDS["role"]["enum"]


def test_heading_kinds_match_schema():
    kinds = table_keys(section("Headings"))
    assert kinds == FIELDS["kind"]["enum"]
    assert kinds == ROW_FIELDS["kind"]["enum"]


def test_items_match_schema_and_validator():
    listed = re.search(r"Valid `item` values, in the required order: ([^.]+)\.", PROMPT).group(1)
    items = listed.split(", ")
    assert items == FIELDS["item"]["enum"]
    assert items == ITEMS


def test_table_fields_match_schema():
    tables = section("Tables")
    units_from = re.search(r"- `units_from`: (.*)", tables).group(1)
    assert re.findall(r"`(\w+)` \(", units_from) == [v for v in FIELDS["units_from"]["enum"] if v]
    row_roles = re.search(r"with `role` \(([^)]*)\)", tables).group(1)
    assert re.findall(r"`(\w+)`", row_roles) == ROW_FIELDS["role"]["enum"]


def test_role_fields_match_schema_and_validator():
    rule = re.search(r"- Fields only where they belong: (.*)", section("Output")).group(1)
    stated = {}
    for part in rule.rstrip(".").split("; "):
        fields, role = part.split(" on ")
        stated[role.strip("`")] = set(re.findall(r"`(\w+)`", fields))
    assert stated == ROLE_FIELDS
    assert set().union(*stated.values()) | {"id", "role", "uncertain"} == set(FIELDS)


def table(*rows: list[str]) -> Block:
    """A table block; an Item heading set in a one-row table (AMZN) is an `item_heading`."""
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
