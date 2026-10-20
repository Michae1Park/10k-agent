"""The structure agent's validator rules (spec §2.2), one function per check.

  V1  one label per block                          missing_labels
  V2  Items once each, in Form 10-K order          items_in_order
  V3  every required Item                          (needs a full filing, not checked)
  V4  fields match the role                        role_fits_block, item_is_valid,
                                                   heading_has_level_and_kind, layout_rows_match,
                                                   keep_role_fields (dropped, not repaired)
  V5  heading levels go down one at a time         levels_step_down
  V6  heading kinds only in their Item             risk_kinds_in_item_1a, statement_kinds_in_item_8
  V7  a `note` heading where an XBRL note starts   note_at_xbrl_note

A block rule gets the block, its label and the context open before it. It returns what the LLM
is told when the label breaks the rule, or None when it doesn't.
"""

from collections.abc import Callable
from dataclasses import dataclass

from blocks import Block

ITEMS = "1 1A 1B 1C 2 3 4 5 6 7 7A 8 9 9A 9B 9C 10 11 12 13 14 15 16".split()  # Form 10-K order
TABLE_ONLY_ROLES = {"data_table", "layout_table", "index_table"}  # V4: tables only
TABLE_ROLES = TABLE_ONLY_ROLES | {"toc", "furniture", "cover"}  # V4: all a table can be
ROLE_FIELDS = {  # V4: fields a role may carry besides id, role and uncertain
    "item_heading": {"item"},
    "heading": {"level", "kind", "run_in"},
    "data_table": {"units", "units_from"},
    "layout_table": {"rows"},
}
ITEM8_KINDS = {"statement", "note", "notes_header"}  # V6: headings only in Item 8 (or 15)
ITEM8_HOMES = {"8", "15"}  # V6: some filers put the statements in Item 15 (A3 moves them)


@dataclass
class Violation:
    rule: str  # V1–V7
    ids: list[str]  # blocks to relabel
    text: str  # what the LLM is told

    def __str__(self) -> str:
        return f"{self.rule}: {self.text}"


@dataclass
class Context:
    """What's open just before a block."""

    item: str | None  # the open Item
    items: list[str]  # the Items seen so far, in order
    depth: int  # the open heading path's depth
    previous: dict  # the previous block's label


# ---------- the rule registry ----------

Check = Callable[[Block, dict, Context], str | None]


@dataclass
class Rule:
    name: str  # V1–V7
    role: str | None  # the only role it checks (None: every role)
    check: Check


RULES: list[Rule] = []


def rule(name: str, role: str | None = None) -> Callable[[Check], Check]:
    """Register a block rule under its spec id, for labels with `role` (or every label)."""

    def register(check: Check) -> Check:
        RULES.append(Rule(name, role, check))
        return check

    return register


def check_block(b: Block, label: dict, ctx: Context) -> list[Violation]:
    """Every rule that applies to the label's role."""
    out = []
    for r in RULES:
        if r.role in (None, label.get("role")) and (text := r.check(b, label, ctx)):
            out.append(Violation(r.name, [b.id], text))
    return out


# ---------- V1: the whole window ----------


def missing_labels(window: list[Block], got: dict[str, dict]) -> list[Violation]:
    missing = [b.id for b in window if b.id not in got]
    if not missing:
        return []
    text = f"no label for {', '.join(missing)}; give exactly one label per block"
    return [Violation("V1", missing, text)]


# ---------- every block ----------


@rule("V4")
def role_fits_block(b: Block, label: dict, ctx: Context) -> str | None:
    """Tables and paragraphs have different roles."""
    role = label.get("role")
    fits = role in TABLE_ROLES if b.kind == "table" else role not in TABLE_ONLY_ROLES
    if not fits:
        return f"{b.id} is a {b.kind}, which can't be `{role}`"
    return None


# ---------- Item headings ----------


@rule("V4", role="item_heading")
def item_is_valid(b: Block, label: dict, ctx: Context) -> str | None:
    if label.get("item") not in ITEMS:
        return f"{b.id}: item_heading needs a valid `item`"
    return None


@rule("V2", role="item_heading")
def items_in_order(b: Block, label: dict, ctx: Context) -> str | None:
    """Only for a valid Item (an invalid one is V4) after an earlier one."""
    item = label.get("item")
    if item not in ITEMS or not ctx.items:
        return None
    last = ctx.items[-1]
    if ITEMS.index(item) <= ITEMS.index(last):
        return f"{b.id}: Item {item} after Item {last}; Items appear once, in order"
    return None


# ---------- headings ----------


def _well_formed(heading: dict) -> bool:
    return isinstance(heading.get("level"), int) and bool(heading.get("kind"))


@rule("V4", role="heading")
def heading_has_level_and_kind(b: Block, label: dict, ctx: Context) -> str | None:
    if not _well_formed(label):
        return f"{b.id}: a heading needs `level` and `kind`"
    return None


@rule("V5", role="heading")
def levels_step_down(b: Block, label: dict, ctx: Context) -> str | None:
    """Only for a well-formed heading (a malformed one is V4)."""
    if _well_formed(label) and label["level"] > ctx.depth + 1:
        jump = f"level {label['level']} jumps from {ctx.depth}"
        return f"{b.id}: {jump}; go down one level at a time"
    return None


@rule("V6", role="heading")
def risk_kinds_in_item_1a(b: Block, label: dict, ctx: Context) -> str | None:
    kind = label.get("kind") or ""
    if kind.startswith("risk_") and ctx.item != "1A":
        return f"{b.id}: `{kind}` outside Item 1A (open Item: {ctx.item})"
    return None


@rule("V6", role="heading")
def statement_kinds_in_item_8(b: Block, label: dict, ctx: Context) -> str | None:
    kind = label.get("kind")
    if kind in ITEM8_KINDS and ctx.item not in ITEM8_HOMES:
        return f"{b.id}: `{kind}` outside Item 8 (open Item: {ctx.item})"
    return None


# ---------- tables ----------


@rule("V4", role="layout_table")
def layout_rows_match(b: Block, label: dict, ctx: Context) -> str | None:
    """A layout table labels each of its rows."""
    if len(label.get("rows") or []) != len(b.rows):
        return f"{b.id}: `rows` needs one label per row ({len(b.rows)} rows)"
    return None


# ---------- XBRL notes ----------


@rule("V7")
def note_at_xbrl_note(b: Block, label: dict, ctx: Context) -> str | None:
    """Where an XBRL note text block starts, there is (or just was) a `note` heading."""
    is_note = label.get("role") == "heading" and label.get("kind") == "note"
    follows_note = ctx.previous.get("kind") == "note"
    if b.xbrl_start == "note" and not (is_note or follows_note):
        return f"{b.id}: an XBRL note starts here; it should be (or follow) a `note` heading"
    return None


# ---------- V4: extra fields (dropped, not repaired) ----------


def keep_role_fields(b: Block, label: dict) -> dict:
    """The label with only its role's own fields."""
    keep = {"id", "role", "uncertain"} | ROLE_FIELDS.get(label.get("role", "body"), set())
    return {k: v for k, v in label.items() if k in keep} | {"id": b.id}
