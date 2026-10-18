"""V0 structure agent: label every block with the LLM, validate, repair, and assemble the
structured document. Spec: experiments/V0_PARSING_CHUNKING.md §2.2–2.3; prompt and output
format: experiments/v0_structure/system.md and labels.schema.json.
"""

import json
import re
from pathlib import Path

import openai
from blocks import Block

HERE = Path(__file__).resolve().parent
PROMPTS = HERE.parent / "v0_structure"
WINDOW = 150
CONTEXT = 10
REPAIR_ROUNDS = 2
ITEMS = [
    "1",
    "1A",
    "1B",
    "1C",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "7A",
    "8",
    "9",
    "9A",
    "9B",
    "9C",
    "10",
    "11",
    "12",
    "13",
    "14",
    "15",
    "16",
]
TABLE_ONLY = {"data_table", "layout_table", "index_table"}
TABLE_ROLES = TABLE_ONLY | {"toc", "furniture", "cover"}
FIELDS = {
    "item_heading": {"item"},
    "heading": {"level", "kind", "run_in"},
    "data_table": {"units", "units_from"},
    "layout_table": {"rows"},
}
DROPPED = {"cover", "toc", "part_heading", "furniture", "index_table"}
ITEM_PREFIX = re.compile(r"^items?\s+\d{1,2}[a-c]?(\s*(,|and|&)\s*\d{1,2}[a-c]?)*\s*[.:]?\s*", re.I)


def load_prompts() -> tuple[str, str, str]:
    """(system prompt, window template, repair template) from system.md."""
    text = (PROMPTS / "system.md").read_text(encoding="utf-8")
    system, templates = text.split("<!-- END SYSTEM PROMPT", 1)
    window = templates.split("## Template: window", 1)[1].split("## Template: repair", 1)[0]
    repair = templates.split("## Template: repair", 1)[1]
    return system.strip(), window.strip(), repair.strip()


class Labeler:
    """One schema-constrained call per window (temperature 0, no thinking); validate → repair."""

    def __init__(self, model: str, base_url: str, filing: dict):
        self.client = openai.OpenAI(base_url=base_url, api_key="not-needed", timeout=1800)
        self.model = model
        self.filing = filing
        self.system, self.window_tpl, self.repair_tpl = load_prompts()
        schema = json.loads((PROMPTS / "labels.schema.json").read_text())
        self.response_format = {
            "type": "json_schema",
            "json_schema": {"name": "labels", "schema": schema},
        }
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        self.log: list[dict] = []

    def label(self, blocks: list[Block]) -> dict[str, dict]:
        labels: dict[str, dict] = {}
        state = State()
        state.texts = {b.id: b.text[:80] for b in blocks}
        for start in range(0, len(blocks), WINDOW):
            window = blocks[start : start + WINDOW]
            context = blocks[max(0, start - CONTEXT) : start]
            user = self.window_tpl.format(
                company=self.filing["registrant"],
                ticker=self.filing["ticker"],
                fiscal_year=self.filing["fiscal_year"],
                open_item=state.item or "none",
                open_path=state.path_text(),
                context="\n".join(_context_line(b, labels[b.id]) for b in context) or "(none)",
                blocks="\n".join(_input_line(b) for b in window),
            )
            messages = [
                {"role": "system", "content": self.system},
                {"role": "user", "content": user},
            ]
            reply = self._call(messages)
            got = _by_id(reply)
            violations = validate(window, got, state)
            entry = {
                "start": window[0].id,
                "end": window[-1].id,
                "violations": [list(map(str, violations))],
            }
            for _ in range(REPAIR_ROUNDS):
                if not violations:
                    break
                involved = sorted({i for v in violations for i in v.ids}, key=lambda i: i)
                lines = [
                    f"{_input_line(b)}\n    your label: {json.dumps(got.get(b.id))}"
                    for b in window
                    if b.id in involved
                ]
                messages += [
                    {"role": "assistant", "content": json.dumps({"labels": list(got.values())})},
                    {
                        "role": "user",
                        "content": self.repair_tpl.format(
                            violations="\n".join(f"- {v}" for v in violations),
                            blocks="\n".join(lines),
                        ),
                    },
                ]
                got.update({k: v for k, v in _by_id(self._call(messages)).items() if k in involved})
                violations = validate(window, got, state)
                entry["violations"].append(list(map(str, violations)))
            for b in window:
                label = clean(b, got.get(b.id) or {"id": b.id, "role": "body", "uncertain": True})
                if any(b.id in v.ids for v in violations):
                    label["unresolved"] = True
                labels[b.id] = label
                state.advance(label)
            self.log.append(entry)
        return labels

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
            return {"labels": []}


class Violation:
    def __init__(self, rule: str, ids: list[str], text: str):
        self.rule, self.ids, self.text = rule, ids, text

    def __str__(self) -> str:
        return f"{self.rule}: {self.text}"


class State:
    """What is open at the end of the labels so far: the Item, Items seen, the heading path."""

    def __init__(self):
        self.item: str | None = None
        self.items: list[str] = []
        self.path: list[tuple[int, str, str]] = []  # (level, kind, block id)
        self.texts: dict[str, str] = {}

    def advance(self, label: dict) -> None:
        if label["role"] == "item_heading":
            self.item = label["item"]
            self.items.append(label["item"])
            self.path = []
        elif label["role"] == "heading" and isinstance(label.get("level"), int):
            level = min(label["level"], len(self.path) + 1)
            self.path = self.path[: level - 1] + [(level, label.get("kind", "other"), label["id"])]

    def path_text(self) -> str:
        return " › ".join(f"L{lv} {self.texts.get(i, i)}" for lv, _, i in self.path) or "(none)"

    def copy(self) -> "State":
        s = State()
        s.item, s.items, s.path, s.texts = self.item, list(self.items), list(self.path), self.texts
        return s


def validate(window: list[Block], got: dict[str, dict], state: State) -> list[Violation]:
    """V1–V7 (§2.2). V3 (every required Item) needs a full filing, so it is not checked here."""
    out: list[Violation] = []
    missing = [b.id for b in window if b.id not in got]
    if missing:
        out.append(
            Violation(
                "V1",
                missing,
                f"no label for {', '.join(missing)}; give exactly one label per block",
            )
        )
    s = state.copy()
    prev: Block | None = None
    for b in window:
        label = got.get(b.id)
        if not label:
            prev = b
            continue
        role = label.get("role")
        if (b.kind == "table" and role not in TABLE_ROLES) or (
            b.kind == "paragraph" and role in TABLE_ONLY
        ):
            out.append(Violation("V4", [b.id], f"{b.id} is a {b.kind}, which can't be `{role}`"))
        if role == "item_heading":
            item = label.get("item")
            if item not in ITEMS:
                out.append(Violation("V4", [b.id], f"{b.id}: item_heading needs a valid `item`"))
            elif s.items and ITEMS.index(item) <= ITEMS.index(s.items[-1]):
                out.append(
                    Violation(
                        "V2",
                        [b.id],
                        f"{b.id}: Item {item} after Item {s.items[-1]}; "
                        "Items appear once, in order",
                    )
                )
        if role == "heading":
            if not isinstance(label.get("level"), int) or not label.get("kind"):
                out.append(Violation("V4", [b.id], f"{b.id}: a heading needs `level` and `kind`"))
            elif label["level"] > len(s.path) + 1:
                out.append(
                    Violation(
                        "V5",
                        [b.id],
                        f"{b.id}: level {label['level']} jumps from {len(s.path)}; "
                        "go down one level at a time",
                    )
                )
            kind = label.get("kind", "")
            if kind.startswith("risk_") and s.item != "1A":
                out.append(
                    Violation(
                        "V6", [b.id], f"{b.id}: `{kind}` outside Item 1A (open Item: {s.item})"
                    )
                )
            if kind in {"statement", "note", "notes_header"} and s.item not in {"8", "15"}:
                out.append(
                    Violation(
                        "V6", [b.id], f"{b.id}: `{kind}` outside Item 8 (open Item: {s.item})"
                    )
                )
        if role == "layout_table" and len(label.get("rows") or []) != len(b.rows):
            out.append(
                Violation(
                    "V4", [b.id], f"{b.id}: `rows` needs one label per row ({len(b.rows)} rows)"
                )
            )
        if b.xbrl_start == "note":
            here = role == "heading" and label.get("kind") == "note"
            before = prev is not None and (got.get(prev.id) or {}).get("kind") == "note"
            if not (here or before):
                out.append(
                    Violation(
                        "V7",
                        [b.id],
                        f"{b.id}: an XBRL note starts here; "
                        "it should be (or follow) a `note` heading",
                    )
                )
        s.advance({**label, "id": b.id})
        prev = b
    return out


def clean(block: Block, label: dict) -> dict:
    """V4 for extra fields: keep only the role's own fields (dropped, not repaired)."""
    role = label.get("role", "body")
    keep = {"id", "role", "uncertain"} | FIELDS.get(role, set())
    return {k: v for k, v in label.items() if k in keep} | {"id": block.id}


def assemble(blocks: list[Block], labels: dict[str, dict], filing: dict) -> dict:
    """A1–A4: sections from Item headings, run-in splits, layout-table rows, heading paths."""
    sections: list[dict] = []
    dropped: dict[str, int] = {}
    review: list[str] = []
    for b in blocks:
        label = labels[b.id]
        role = label["role"]
        if label.get("uncertain") or label.get("unresolved"):
            review.append(b.id)
        if role == "item_heading":
            sections.append(
                {
                    "item": label["item"],
                    "title": ITEM_PREFIX.sub("", b.text) or b.text,
                    "blocks": [],
                }
            )
            continue
        if role in DROPPED or not sections:
            dropped[role if sections else "cover"] = (
                dropped.get(role if sections else "cover", 0) + 1
            )
            continue
        out = sections[-1]["blocks"]
        if role == "heading":
            if label.get("run_in") and b.emph == "runin":  # A1
                out.append(_heading(f"{b.id}.h", b.text[: b.runin], label, b))
                out.append(_para(f"{b.id}.b", b.text[b.runin :].strip(), b))
            else:
                out.append(_heading(b.id, b.text, label, b))
        elif role == "data_table":
            out.append(
                {
                    "id": b.id,
                    "kind": "table",
                    "text": "\n".join("| " + " | ".join(r) + " |" for r in b.rows),
                    "rows": b.rows,
                    "units": label.get("units"),
                    "units_from": label.get("units_from"),
                    "xbrl": b.xbrl,
                }
            )
        elif role == "layout_table":  # A2: rows become headings or paragraphs
            for k, (row, rl) in enumerate(
                zip(b.rows, label.get("rows") or [{"role": "body"}] * len(b.rows), strict=False)
            ):
                text = " ".join(row)
                if rl.get("role") == "heading":
                    out.append(_heading(f"{b.id}.r{k}", text, rl, b))
                elif rl.get("role") == "body":
                    out.append(_para(f"{b.id}.r{k}", text, b))
        else:
            out.append(_para(b.id, b.text, b))
    _move_statements(sections)  # A3
    for section in sections:  # A4
        stack: list[str] = []
        for blk in section["blocks"]:
            if blk["kind"] == "heading":
                level = max(1, min(blk["level"], len(stack) + 1))
                blk["level"] = level
                stack = stack[: level - 1] + [blk["text"]]
            blk["heading_path"] = list(stack)
    return {"filing": filing, "sections": sections, "dropped": dropped, "review": review}


def _heading(id_: str, text: str, label: dict, b: Block) -> dict:
    return {
        "id": id_,
        "kind": "heading",
        "text": text,
        "level": label.get("level") or 1,
        "heading_kind": label.get("kind") or "other",
        "xbrl": b.xbrl,
    }


def _para(id_: str, text: str, b: Block) -> dict:
    return {"id": id_, "kind": "paragraph", "text": text, "xbrl": b.xbrl}


def _move_statements(sections: list[dict]) -> None:
    item8 = next((s for s in sections if s["item"] == "8"), None)
    if item8 is None or any(b.get("heading_kind") == "statement" for b in item8["blocks"]):
        return
    for s in sections[sections.index(item8) + 1 :]:
        for i, b in enumerate(s["blocks"]):
            if b.get("heading_kind") in {"statement", "notes_header", "auditor_report"}:
                item8["blocks"].extend(s["blocks"][i:])
                del s["blocks"][i:]
                return


def _input_line(b: Block) -> str:
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
    extra = " ".join(f"{k}={label[k]}" for k in ("item", "level", "kind") if k in label)
    return f"{b.id} | {label['role']} {extra} | {b.text[:100]}"


def _by_id(reply: dict) -> dict[str, dict]:
    return {lb["id"]: lb for lb in reply.get("labels", []) if isinstance(lb, dict) and "id" in lb}
