"""The V0 structure prompt, its output schema and the validator list the same values.

The model never sees labels.schema.json (guided decoding only constrains its tokens), so the
prompt must name every value the schema allows, and the validator must check the same ones.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "experiments" / "v0" / "v0_structure"
sys.path.insert(0, str(ROOT / "experiments" / "v0" / "v0_src"))

from rules import ITEMS, ROLE_FIELDS  # noqa: E402

PROMPT = (PROMPTS / "system.md").read_text(encoding="utf-8").split("<!-- END SYSTEM PROMPT")[0]
LABEL = json.loads((PROMPTS / "labels.schema.json").read_text())["properties"]["labels"]["items"]
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
