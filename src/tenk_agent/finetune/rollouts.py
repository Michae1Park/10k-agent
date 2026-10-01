"""Run the agent over generated questions and keep every attempt, scored.

Each attempt is checked with the eval's deterministic scoring: the right numbers, every
numeric claim verified in its cited chunk, the right abstention, no format error and no
tool budget overrun. Only attempts that pass become training data (rejection sampling).
"""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from tenk_agent.agent import run_research
from tenk_agent.config import Settings
from tenk_agent.corpus import COMPANIES
from tenk_agent.evaluation.scoring import agent_metrics, citation_accuracy, score_numbers
from tenk_agent.models import get_model
from tenk_agent.retrieval import Retriever
from tenk_agent.store import Store
from tenk_agent.tools import Toolbox

log = logging.getLogger(__name__)


@dataclass
class RolloutOptions:
    questions: Path
    out: Path
    split: str = "train"
    samples: int = 2  # attempts per question
    temperature: float = 0.7
    workers: int = 4
    limit: int | None = None


def run(options: RolloutOptions, settings: Settings) -> dict:
    if settings.model.startswith("anthropic:"):
        raise SystemExit("V7 trains on open-model outputs only, never Claude's (D-022).")
    model = get_model(
        settings.model,
        settings.gpu_hourly_usd,
        settings.base_url,
        settings.thinking,
        options.temperature,
    )
    store = Store(settings.db_path)
    retriever = Retriever.from_settings(store, settings)
    toolbox = Toolbox.from_settings(store, retriever, settings)
    questions = [
        q
        for q in map(json.loads, options.questions.read_text().splitlines())
        if q["split"] == options.split
    ][: options.limit]

    done = set()
    if options.out.exists():
        for line in options.out.read_text().splitlines():
            row = json.loads(line)
            done.add((row["id"], row["sample"]))
    jobs = [(q, s) for q in questions for s in range(options.samples) if (q["id"], s) not in done]
    options.out.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()

    def attempt(job) -> bool:
        q, sample = job
        row = rollout(q, sample, store, toolbox, model, settings)
        with lock, options.out.open("a") as f:
            f.write(json.dumps(row) + "\n")
        log.info("%s #%d %s", q["id"], sample, "passed" if row["passed"] else "failed")
        return row["passed"]

    with ThreadPoolExecutor(max_workers=options.workers) as pool:
        passed = list(pool.map(attempt, jobs))
    return {"attempts": len(jobs), "passed": sum(passed), "skipped_done": len(done)}


def rollout(q: dict, sample: int, store, toolbox, model, settings: Settings) -> dict:
    row = {
        "id": q["id"],
        "sample": sample,
        "split": q["split"],
        "kind": q.get("kind"),
        "question": q["question"],
        "model": model.name,
        "thinking": settings.thinking,
        "temperature": getattr(model, "temperature", None),
        "corpus": sorted(c.ticker for c in COMPANIES),
    }
    transcript: dict = {}
    try:
        answer = run_research(
            q["question"],
            store,
            toolbox,
            model,
            max_tool_calls=settings.max_tool_calls,
            verify=settings.verify,
            repair=settings.repair,
            repair_calls=settings.repair_calls,
            transcript=transcript,
        )
    except Exception as e:  # a failed attempt is data about the model, not a crash
        return row | {"passed": False, "error": f"{type(e).__name__}: {e}"}
    trace = store.get_trace(answer["trace_id"]) or {}
    checks = score(q, answer, trace, store)
    return row | {
        "passed": all(checks.values()),
        "checks": checks,
        "tool_errors": sum(s["type"] == "tool" and s["is_error"] for s in trace.get("spans", [])),
        "usage": answer["usage"],
        "transcript": serialize(transcript),
    }


def score(q: dict, answer: dict, trace: dict, store) -> dict[str, bool]:
    checks = {"format": not answer.get("format_error", False)}
    if q.get("should_abstain"):
        return checks | {"abstained": bool(answer["abstained"])}
    numbers = score_numbers(q, answer)
    cites = citation_accuracy(answer, store)
    agent = agent_metrics(q, trace)
    return checks | {
        "answered": not answer["abstained"],
        "numbers": bool(numbers and numbers["all"]),
        "citations": cites["accurate"] == cites["numeric_claims"],
        "within_budget": agent["over_budget_calls"] == 0,
    }


def serialize(transcript: dict) -> dict:
    """The neutral messages as JSON (tool calls as dicts; provider replay data dropped)."""
    messages = []
    for m in transcript.get("messages", []):
        if m["role"] == "assistant":
            m = {
                "role": "assistant",
                "content": m["content"],
                "tool_calls": [c.to_dict() for c in m.get("tool_calls") or []],
            }
        messages.append(m)
    tools = [
        {"name": t.name, "description": t.description, "parameters": t.parameters}
        for t in transcript.get("tools", [])
    ]
    return {"system": transcript.get("system"), "tools": tools, "messages": messages}
