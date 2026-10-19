"""Preprocessing step 2: the LLM structure agent, then assembly into the structured document.

  label     the LLM gives every block a role (Item heading, heading, table, noise…),
            one window of ~150 blocks per call                      Labeler
  validate  code checks each window's labels (V1, V2, V4–V7)        validate (rules.py)
  repair    violations go back to the LLM, at most 2 rounds         Labeler._label_window
  assemble  code applies the labels to the blocks' own text (A1–A4) assemble

The LLM only labels; the document's text is always the filing's own. Spec:
experiments/V0_PARSING_CHUNKING.md §2.2–2.3; prompt and output format:
experiments/v0_structure/system.md and labels.schema.json.
"""

import json
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path

import openai
from blocks import Block
from rules import ITEMS, Context, Violation, check_block, keep_role_fields, missing_labels

PROMPTS = Path(__file__).resolve().parent.parent / "v0_structure"
WINDOW = 150  # blocks labeled per call
CONTEXT = 10  # labeled blocks from the previous window shown as context
REPAIR_ROUNDS = 2

DROPPED_ROLES = {"cover", "toc", "part_heading", "furniture", "index_table"}  # A2
STATEMENT_KINDS = {"statement", "notes_header", "auditor_report"}  # A3: where statements start
ITEM_PREFIX = re.compile(  # "Item 1A." / "Items 10 and 11:" before a section title
    r"^items?\s+\d{1,2}[a-c]?(\s*(,|and|&)\s*\d{1,2}[a-c]?)*\s*[.:]?\s*", re.I
)


def load_prompts() -> tuple[str, str, str]:
    """(system prompt, window template, repair template) from system.md."""
    text = (PROMPTS / "system.md").read_text(encoding="utf-8")
    system, templates = text.split("<!-- END SYSTEM PROMPT", 1)
    window = templates.split("## Template: window", 1)[1].split("## Template: repair", 1)[0]
    repair = templates.split("## Template: repair", 1)[1]
    return system.strip(), window.strip(), repair.strip()


# ---------- labeling ----------


@dataclass
class Outline:
    """Where the labels so far leave off: the open Item, the Items seen, the open heading path."""

    texts: dict[str, str]  # block id → text, to show the path in the prompt
    item: str | None = None
    items: list[str] = field(default_factory=list)
    path: list[tuple[int, str]] = field(default_factory=list)  # (level, heading block id)

    def advance(self, label: dict) -> None:
        """Move past one labeled block."""
        if label["role"] == "item_heading":
            self.item = label.get("item")
            if self.item in ITEMS:
                self.items.append(self.item)
            self.path = []
        elif label["role"] == "heading" and isinstance(label.get("level"), int):
            level = min(label["level"], len(self.path) + 1)
            self.path = self.path[: level - 1] + [(level, label["id"])]

    def path_text(self) -> str:
        return " › ".join(f"L{level} {self.texts.get(i, i)}" for level, i in self.path) or "(none)"

    def copy(self) -> "Outline":
        return replace(self, items=list(self.items), path=list(self.path))


class Labeler:
    """Labels a filing window by window: one schema-constrained call (temperature 0, thinking
    off), then validate → repair."""

    def __init__(self, model: str, base_url: str, filing: dict):
        self.client = openai.OpenAI(base_url=base_url, api_key="not-needed", timeout=1800)
        self.model = model
        self.filing = filing
        self.system, self.window_template, self.repair_template = load_prompts()
        schema = json.loads((PROMPTS / "labels.schema.json").read_text())
        self.response_format = {
            "type": "json_schema",
            "json_schema": {"name": "labels", "schema": schema},
        }
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        self.log: list[dict] = []  # per window: its violations after each round

    def label(self, blocks: list[Block]) -> dict[str, dict]:
        """block id → label. Blocks the repair couldn't fix are marked `unresolved`."""
        labels: dict[str, dict] = {}
        outline = Outline({b.id: b.text[:80] for b in blocks})
        for start in range(0, len(blocks), WINDOW):
            window = blocks[start : start + WINDOW]
            context = blocks[max(0, start - CONTEXT) : start]
            got, violations = self._label_window(window, context, labels, outline)
            for b in window:
                missing = {"id": b.id, "role": "body", "uncertain": True}
                label = keep_role_fields(b, got.get(b.id) or missing)
                if any(b.id in v.ids for v in violations):
                    label["unresolved"] = True
                labels[b.id] = label
                outline.advance(label)
        return labels

    def _label_window(
        self, window: list[Block], context: list[Block], labels: dict, outline: Outline
    ) -> tuple[dict[str, dict], list[Violation]]:
        """The window's labels after up to REPAIR_ROUNDS repairs, and the violations left."""
        user = self.window_template.format(
            company=self.filing["registrant"],
            ticker=self.filing["ticker"],
            fiscal_year=self.filing["fiscal_year"],
            open_item=outline.item or "none",
            open_path=outline.path_text(),
            context="\n".join(_context_line(b, labels[b.id]) for b in context) or "(none)",
            blocks="\n".join(_input_line(b) for b in window),
        )
        messages = [{"role": "system", "content": self.system}, {"role": "user", "content": user}]
        got = _by_id(self._call(messages))
        violations = validate(window, got, outline)
        rounds = [list(map(str, violations))]
        for _ in range(REPAIR_ROUNDS):
            if not violations:
                break
            # Show the LLM its own reply, the violations and the flagged blocks with their labels.
            flagged = {i for v in violations for i in v.ids}
            lines = [
                f"{_input_line(b)}\n    your label: {json.dumps(got.get(b.id))}"
                for b in window
                if b.id in flagged
            ]
            repair = self.repair_template.format(
                violations="\n".join(f"- {v}" for v in violations), blocks="\n".join(lines)
            )
            messages += [
                {"role": "assistant", "content": json.dumps({"labels": list(got.values())})},
                {"role": "user", "content": repair},
            ]
            fixed = _by_id(self._call(messages))
            got.update({i: label for i, label in fixed.items() if i in flagged})
            violations = validate(window, got, outline)
            rounds.append(list(map(str, violations)))
        self.log.append({"start": window[0].id, "end": window[-1].id, "violations": rounds})
        return got, violations

    def _call(self, messages: list[dict]) -> dict:
        r = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0,
            max_tokens=14000,
            response_format=self.response_format,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        self.usage["calls"] += 1
        self.usage["prompt_tokens"] += r.usage.prompt_tokens
        self.usage["completion_tokens"] += r.usage.completion_tokens
        try:
            return json.loads(r.choices[0].message.content)
        except json.JSONDecodeError:
            return {"labels": []}  # every block then fails V1 and goes to repair


def _input_line(b: Block) -> str:
    """How the LLM sees a block: `id | features | text`, long text and tables shortened."""
    if b.kind == "table":
        rows = b.rows if len(b.rows) <= 6 else b.rows[:3] + b.rows[-1:]
        return (
            f"{b.id} | TABLE rows={len(b.rows)} cols={max(len(r) for r in b.rows)} "
            + f"{b.features()} | "
            + " ‖ ".join(" | ".join(r) for r in rows)
        )
    text = b.text if len(b.text) <= 300 else f"{b.text[:200]} … {b.text[-60:]}"
    return f"{b.id} | {b.features()} | {text}"


def _context_line(b: Block, label: dict) -> str:
    """An already-labeled block from the previous window, shown with its label."""
    extra = " ".join(f"{k}={label[k]}" for k in ("item", "level", "kind") if k in label)
    return f"{b.id} | {label['role']} {extra} | {b.text[:100]}"


def _by_id(reply: dict) -> dict[str, dict]:
    return {lb["id"]: lb for lb in reply.get("labels", []) if isinstance(lb, dict) and "id" in lb}


# ---------- validation ----------


def validate(window: list[Block], got: dict[str, dict], outline: Outline) -> list[Violation]:
    """V1, V2, V4–V7 (§2.2, rules.py) on one window's labels. V3 (every required Item present)
    needs a full filing, so it isn't checked here."""
    out = missing_labels(window, got)
    outline = outline.copy()  # walk the window without moving the caller's outline
    previous: dict = {}
    for b in window:
        label = got.get(b.id)
        if label:
            ctx = Context(
                item=outline.item, items=outline.items, depth=len(outline.path), previous=previous
            )
            out += check_block(b, label, ctx)
            outline.advance({**label, "id": b.id})
        previous = label or {}
    return out


# ---------- assembly ----------


def assemble(blocks: list[Block], labels: dict[str, dict], filing: dict) -> dict:
    """The structured document (§2.3) from blocks + labels: sections, run-in splits and layout
    rows (A1–A2), statements moved into Item 8 (A3), heading paths (A4)."""
    sections: list[dict] = []
    dropped: Counter[str] = Counter()
    review: list[str] = []
    for b in blocks:
        label = labels[b.id]
        role = label["role"]
        if label.get("uncertain") or label.get("unresolved"):
            review.append(b.id)
        if role == "item_heading":  # A2: each Item heading opens a section
            title = ITEM_PREFIX.sub("", b.text) or b.text
            sections.append({"item": label.get("item"), "title": title, "blocks": []})
        elif not sections:  # everything before the first Item is the cover
            dropped["cover"] += 1
        elif role in DROPPED_ROLES:
            dropped[role] += 1
        else:
            sections[-1]["blocks"] += _document_blocks(b, label)
    _move_statements_into_item8(sections)
    _add_heading_paths(sections)
    return {"filing": filing, "sections": sections, "dropped": dict(dropped), "review": review}


def _document_blocks(b: Block, label: dict) -> list[dict]:
    """The document block(s) one labeled block becomes."""
    role = label["role"]
    if role == "heading" and label.get("run_in") and b.emph == "runin":
        # A1: the emphasized lead is the heading, the rest a paragraph.
        return [
            _heading(f"{b.id}.h", b.text[: b.runin], label, b),
            _paragraph(f"{b.id}.b", b.text[b.runin :].strip(), b),
        ]
    if role == "heading":
        return [_heading(b.id, b.text, label, b)]
    if role == "data_table":
        return [
            {
                "id": b.id,
                "kind": "table",
                "text": "\n".join("| " + " | ".join(r) + " |" for r in b.rows),
                "rows": b.rows,
                "units": label.get("units"),
                "units_from": label.get("units_from"),
                "xbrl": b.xbrl,
            }
        ]
    if role == "layout_table":
        # A2: each row becomes a heading or a paragraph; furniture rows are dropped.
        row_labels = label.get("rows") or [{"role": "body"}] * len(b.rows)
        out = []
        for k, (row, row_label) in enumerate(zip(b.rows, row_labels, strict=False)):
            if row_label.get("role") == "heading":
                out.append(_heading(f"{b.id}.r{k}", " ".join(row), row_label, b))
            elif row_label.get("role") == "body":
                out.append(_paragraph(f"{b.id}.r{k}", " ".join(row), b))
        return out
    return [_paragraph(b.id, b.text, b)]


def _heading(id_: str, text: str, label: dict, b: Block) -> dict:
    return {
        "id": id_,
        "kind": "heading",
        "text": text,
        "level": label.get("level") or 1,
        "heading_kind": label.get("kind") or "other",
        "xbrl": b.xbrl,
    }


def _paragraph(id_: str, text: str, b: Block) -> dict:
    return {"id": id_, "kind": "paragraph", "text": text, "xbrl": b.xbrl}


def _move_statements_into_item8(sections: list[dict]) -> None:
    """A3: if Item 8 has no statement, move the span from the first statement / notes / auditor
    heading after it, up to the end of that section, into Item 8 (NVIDIA, Netflix: Item 15)."""
    item8 = next((s for s in sections if s["item"] == "8"), None)
    if item8 is None or any(b.get("heading_kind") == "statement" for b in item8["blocks"]):
        return
    for section in sections[sections.index(item8) + 1 :]:
        for i, b in enumerate(section["blocks"]):
            if b.get("heading_kind") in STATEMENT_KINDS:
                item8["blocks"] += section["blocks"][i:]
                del section["blocks"][i:]
                return


def _add_heading_paths(sections: list[dict]) -> None:
    """A4: every block's heading_path, the headings above it (a heading includes itself).
    Levels that jump by more than one are clamped."""
    for section in sections:
        path: list[str] = []
        for b in section["blocks"]:
            if b["kind"] == "heading":
                b["level"] = max(1, min(b["level"], len(path) + 1))
                path = path[: b["level"] - 1] + [b["text"]]
            b["heading_path"] = list(path)
