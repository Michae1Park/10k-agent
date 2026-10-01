"""Results tables from finished runs: one column per run, only measured values.

    tenk eval report V1=v1-retrieval V2=v2-ask V3=v3-research V4=v4-research

A cell is "—" when the metric doesn't apply to that system and "n/m" when it applies but
wasn't measured (e.g. judge metrics without judge credentials).
"""

import json
from pathlib import Path

from tenk_agent.evaluation.runner import RUNS_DIR

# (row label, section, key, systems it applies to, format)
ROWS = [
    ("Retrieval (Recall@5)", "retrieval", "recall@5", {"retrieval", "ask"}, "pct"),
    ("Retrieval (MRR)", "retrieval", "mrr", {"retrieval", "ask"}, "num"),
    ("Evidence recall (agent saw gold chunk)", "retrieval", "evidence_recall", {"research"}, "pct"),
    ("Context relevance (judge)", "retrieval", "context_relevance", {"retrieval"}, "pct"),
    ("Answer correctness", "answers", "correctness", {"ask", "research"}, "pct"),
    ("Answer faithfulness (judge)", "answers", "faithfulness", {"ask", "research"}, "pct"),
    ("Citation accuracy (numeric)", "answers", "citation_accuracy", {"ask", "research"}, "pct"),
    ("Tool-call correctness", "agent", "tool_call_correctness", {"research"}, "pct"),
    ("Tool selection", "agent", "tool_selection", {"research"}, "pct"),
    ("Unnecessary calls / question", "agent", "unnecessary_calls", {"research"}, "num"),
    ("Task completion", "answers", "task_completion", {"ask", "research"}, "pct"),
    ("Unsupported-answer rate", "answers", "unsupported_answer_rate", {"ask", "research"}, "pct"),
    ("False-abstention rate", "answers", "false_abstention_rate", {"ask", "research"}, "pct"),
    ("Mean latency (s)", "cost", "mean_latency_s", {"ask", "research"}, "num"),
    ("Mean tokens in / out", "cost", None, {"ask", "research"}, "tokens"),
    ("Mean cost / question (USD)", "cost", "mean_cost_usd", {"ask", "research"}, "usd"),
]


def load_summary(label: str) -> dict:
    return json.loads((RUNS_DIR / label / "summary.json").read_text())


def _cell(summary: dict, section: str, key: str | None, systems: set, fmt: str) -> str:
    if summary["config"]["system"] not in systems:
        return "—"
    data = summary.get(section, {})
    if fmt == "tokens":
        i, o = data.get("mean_input_tokens"), data.get("mean_output_tokens")
        return f"{i:,.0f} / {o:,.0f}" if i is not None and o is not None else "n/m"
    value = data.get(key)
    if value is None:
        return "n/m"
    if fmt == "pct":
        return f"{value * 100:.1f}%"
    if fmt == "usd":
        return f"${value:.4f}"
    return f"{value:.2f}"


def table(columns: list[tuple[str, str]]) -> str:
    summaries = [(name, load_summary(label)) for name, label in columns]
    lines = [
        "| Metric | " + " | ".join(name for name, _ in summaries) + " |",
        "| --- | " + " | ".join("---" for _ in summaries) + " |",
    ]
    for label, section, key, systems, fmt in ROWS:
        cells = [_cell(s, section, key, systems, fmt) for _, s in summaries]
        if all(c == "—" for c in cells):
            continue
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    notes = []
    for name, s in summaries:
        c = s["config"]
        parts = [
            f"`{c['label']}`",
            c["system"],
            c["model"] or c["retriever"],
            f"{s['questions']} {c['split'] or 'selected'} questions",
        ]
        scored = s.get("answers", {}).get("correctness_scored")
        if scored is not None and scored < s["questions"]:
            parts.append(f"correctness and task completion scored on {scored} (no judge)")
        if c.get("includes_unverified"):
            parts.append(f"**includes unverified gold ({s['verified_questions']} verified)**")
        if s.get("errors"):
            parts.append(f"{s['errors']} errors (scored as failures)")
        notes.append(f"- {name}: " + ", ".join(parts))
    return "\n".join(lines) + "\n\n" + "\n".join(notes) + "\n"


def write(columns: list[tuple[str, str]], path: Path) -> str:
    text = table(columns)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return text
