"""Passing rollouts -> chat-format SFT records, behind the D-022 guards.

A record is what the model saw and said, in the OpenAI chat format vLLM renders through
the model's chat template: {"messages": [system, user, assistant (tool_calls), tool, ...],
"tools": [...], "meta": {...}}. Tool-call arguments are objects, as vLLM passes them to
the template. Masking (loss on assistant turns only) happens at training time.

Guards: never Claude outputs, never a gold question, never an eval-corpus filing
(FY2023-2025 of the eval companies; their filings for FY2022 and earlier are allowed).
"""

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from tenk_agent import gold
from tenk_agent.corpus import EVAL_COMPANIES, EVAL_FISCAL_YEARS

CHUNK_ID = re.compile(r"\b([A-Z][A-Z.]{0,5})-FY(\d{4})-")
EVAL_TICKERS = {c.ticker for c in EVAL_COMPANIES}


@dataclass
class DatasetOptions:
    rollouts: Path
    out: Path
    split: str = "train"
    max_per_question: int = 2  # distinct passing attempts kept per question
    max_tool_errors: int = 0  # attempts with more failed tool calls are dropped


@dataclass
class Report:
    kept: int = 0
    rejected: Counter = field(default_factory=Counter)
    by_kind: Counter = field(default_factory=Counter)

    def to_dict(self) -> dict:
        return {"kept": self.kept, "rejected": dict(self.rejected), "by_kind": dict(self.by_kind)}


def build(options: DatasetOptions) -> Report:
    gold_ids = {q["id"] for q in gold.load()}
    gold_questions = {_norm(q["question"]) for q in gold.load()}
    report = Report()
    records, seen, per_question = [], set(), Counter()
    for line in options.rollouts.read_text().splitlines():
        row = json.loads(line)
        reason = rejection(row, options, gold_ids, gold_questions)
        if reason is None:
            record = to_record(row)
            digest = hashlib.sha1(
                json.dumps(
                    [m for m in record["messages"] if m["role"] == "assistant"], sort_keys=True
                ).encode()
            ).hexdigest()
            if digest in seen:
                reason = "duplicate attempt"
            elif per_question[row["id"]] >= options.max_per_question:
                reason = "question already has enough attempts"
            else:
                seen.add(digest)
                per_question[row["id"]] += 1
                records.append(record)
                report.kept += 1
                report.by_kind[row.get("kind")] += 1
        if reason:
            report.rejected[reason] += 1
    options.out.parent.mkdir(parents=True, exist_ok=True)
    options.out.write_text("".join(json.dumps(r) + "\n" for r in records))
    return report


def rejection(row: dict, options, gold_ids: set, gold_questions: set) -> str | None:
    """Why a rollout can't be training data, or None if it can."""
    # D-022 guards first: these are rules, not quality filters.
    if str(row.get("model", "")).startswith("anthropic:"):
        return "D-022: Claude output"
    if row["id"] in gold_ids or _norm(row["question"]) in gold_questions:
        return "D-022: gold question"
    if touches_eval_corpus(row):
        return "D-022: eval-corpus filing"
    if row.get("split") != options.split:
        return f"split {row.get('split')}"
    if row.get("error"):
        return "error"
    if not row.get("passed"):
        return "failed checks"
    if row.get("tool_errors", 0) > options.max_tool_errors:
        return "tool errors"
    return None


def touches_eval_corpus(row: dict) -> bool:
    if set(row.get("corpus", [])) & EVAL_TICKERS:
        return True
    text = json.dumps(row.get("transcript", {}))
    return any(
        ticker in EVAL_TICKERS and int(year) in EVAL_FISCAL_YEARS
        for ticker, year in CHUNK_ID.findall(text)
    )


def to_record(row: dict) -> dict:
    transcript = row["transcript"]
    messages = [{"role": "system", "content": transcript["system"]}]
    for m in transcript["messages"]:
        if m["role"] == "assistant":
            message = {"role": "assistant", "content": m["content"] or ""}
            if m.get("tool_calls"):
                message["tool_calls"] = [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": c["arguments"]},
                    }
                    for c in m["tool_calls"]
                ]
            messages.append(message)
        elif m["role"] == "tool":
            messages.append(
                {"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]}
            )
        else:
            messages.append({"role": m["role"], "content": m["content"]})
    tools = [{"type": "function", "function": t} for t in transcript["tools"]]
    meta = {k: row.get(k) for k in ("id", "sample", "kind", "model", "thinking", "temperature")}
    return {"messages": messages, "tools": tools, "meta": meta}


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()
