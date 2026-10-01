"""Run a system over the gold set and write eval/runs/<label>/{config,results,summary}.

    retrieval  the retriever alone (V1): Recall@5/10, MRR, context relevance
    ask        the Ask pipeline (V2)
    research   the agent (V3; V4 with verification on)

Results are JSONL, one scored row per question, so any metric can be recomputed later.
"""

import json
import logging
import statistics
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tenk_agent import gold
from tenk_agent.agent import run_research
from tenk_agent.ask import ask
from tenk_agent.config import Settings
from tenk_agent.evaluation import retrieval as rmetrics
from tenk_agent.evaluation.judge import Judge
from tenk_agent.evaluation.scoring import agent_metrics, citation_accuracy, score_numbers
from tenk_agent.models import get_model, load_model
from tenk_agent.retrieval import Retriever
from tenk_agent.store import Store
from tenk_agent.tools import Toolbox

log = logging.getLogger(__name__)
RUNS_DIR = Path("eval/runs")
REFERENCE_PATH = Path("eval/gold/reference_trajectories.json")
SYSTEMS = ("retrieval", "ask", "research")


@dataclass
class RunOptions:
    system: str
    label: str
    split: str | None = "dev"
    ids: list[str] | None = None
    include_unverified: bool = False
    judge: bool = True
    verify: bool = True
    workers: int = 4
    limit: int | None = None
    retrieval_overrides: dict | None = None
    max_tool_calls: int = 15
    ask_k: int = 8
    repair: bool = True  # V4: send failed claims back to the agent once
    repair_calls: int = 4
    resume: bool = False  # keep finished rows in results.jsonl, run only the rest
    gold_path: Path = gold.GOLD_PATH  # V7: generated questions over the training corpus


def select_questions(options: RunOptions) -> list[dict]:
    questions = [q for q in gold.load(options.gold_path) if not q.get("retired")]
    if options.ids:
        questions = [q for q in questions if q["id"] in options.ids]
    elif options.split:
        questions = [q for q in questions if q["split"] == options.split]
    if not options.include_unverified:
        questions = [q for q in questions if gold.is_verified(q)]
    return questions[: options.limit] if options.limit else questions


def run(options: RunOptions, settings: Settings) -> dict:
    store = Store(settings.db_path)
    retriever = Retriever.from_settings(store, settings, **(options.retrieval_overrides or {}))
    model = load_model(settings) if options.system != "retrieval" else None
    judge = Judge(get_model(settings.judge_model)) if options.judge else None
    toolbox = Toolbox.from_settings(store, retriever, settings)
    references = json.loads(REFERENCE_PATH.read_text()) if REFERENCE_PATH.exists() else {}
    questions = select_questions(options)
    if not questions:
        raise SystemExit("No questions selected (unverified questions need --include-unverified).")

    out_dir = RUNS_DIR / options.label
    out_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "label": options.label,
        "system": options.system,
        "started_at": datetime.now(UTC).isoformat(),
        "model": model.name if model else None,
        "thinking": settings.thinking if model else None,
        "judge_model": settings.judge_model if judge else None,
        "retriever": retriever.name,
        "verify": options.verify,
        "repair": options.repair and options.verify,
        "structured_data": settings.structured_data,
        "split": options.split,
        "gold": str(options.gold_path),
        "questions": len(questions),
        "includes_unverified": any(not gold.is_verified(q) for q in questions),
    }
    (out_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")

    lock = threading.Lock()
    results_path = out_dir / "results.jsonl"
    done: list[dict] = []
    if options.resume and results_path.exists():
        done = [json.loads(line) for line in results_path.read_text().splitlines() if line]
        finished = {r["id"] for r in done if not r.get("error")}
        done = [r for r in done if r["id"] in finished]
        results_path.write_text("".join(json.dumps(r) + "\n" for r in done))
        questions = [q for q in questions if q["id"] not in finished]
    else:
        results_path.write_text("")

    def evaluate(q: dict) -> dict:
        try:
            row = _evaluate(q, options, store, retriever, model, toolbox, judge, references)
        except Exception as e:  # one failure must not end the run; it's scored as a failure
            log.error("%s failed: %s", q["id"], e)
            row = _base(q) | {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()}
        with lock, results_path.open("a") as f:
            f.write(json.dumps(row) + "\n")
        log.info("%s done%s", q["id"], " (error)" if row.get("error") else "")
        return row

    with ThreadPoolExecutor(max_workers=options.workers) as pool:
        rows = done + list(pool.map(evaluate, questions))

    summary = summarize(rows, options.system) | {"config": config}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def rescore(label: str, settings: Settings) -> dict:
    """Recompute deterministic metrics for a finished run from its stored answers and traces.

    Model calls are not repeated; judge verdicts already in the rows are kept.
    """
    store = Store(settings.db_path)
    out_dir = RUNS_DIR / label
    config = json.loads((out_dir / "config.json").read_text())
    questions = {q["id"]: q for q in gold.load(Path(config.get("gold", gold.GOLD_PATH)))}
    references = json.loads(REFERENCE_PATH.read_text()) if REFERENCE_PATH.exists() else {}
    rows = [json.loads(line) for line in (out_dir / "results.jsonl").read_text().splitlines()]
    for row in rows:
        q = questions[row["id"]]
        answer = row.get("answer")
        if row.get("error") or answer is None:
            continue
        answer = answer | {"citations": {}}
        row["numbers"] = score_numbers(q, answer)
        row["citations"] = citation_accuracy(answer, store)
        if config["system"] == "research":
            trace = store.get_trace(answer["trace_id"]) or {}
            row["agent"] = agent_metrics(q, trace, references.get(q["id"]))
        row["correctness"] = _correctness(q, row)
        row["task_complete"] = _task_complete(q, row)
    (out_dir / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    summary = summarize(rows, config["system"]) | {"config": config}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def _base(q: dict) -> dict:
    return {
        "id": q["id"],
        "split": q["split"],
        "category": q["category"],
        "failure_mode": q.get("failure_mode"),
        "answer_type": q["answer_type"],
        "should_abstain": q.get("should_abstain", False),
        "verified": gold.is_verified(q),
    }


def _evaluate(q, options, store, retriever, model, toolbox, judge, references) -> dict:
    row = _base(q)
    scorable = rmetrics.is_scorable(store, q)
    gold_set = rmetrics.gold_chunks(store, q) if scorable else None

    if options.system == "retrieval":
        chunks = retriever.search(q["question"], k=10)
        ids = [c.id for c in chunks]
        row["retrieved"] = ids
        if gold_set:
            row["recall@5"] = rmetrics.recall_at_k(ids, gold_set, 5)
            row["recall@10"] = rmetrics.recall_at_k(ids, gold_set, 10)
            row["mrr"] = rmetrics.reciprocal_rank(ids, gold_set)
        if judge and chunks:
            verdicts = judge.relevance(q["question"], [c.text for c in chunks[:5]])
            row["context_relevance"] = sum(verdicts) / len(verdicts)
        return row

    if options.system == "ask":
        answer = ask(q["question"], store, retriever, model, verify=options.verify, k=options.ask_k)
        retrieved = answer["retrieved"]
    else:
        answer = run_research(
            q["question"],
            store,
            toolbox,
            model,
            max_tool_calls=options.max_tool_calls,
            verify=options.verify,
            repair=options.repair,
            repair_calls=options.repair_calls,
        )
        retrieved = None
    trace = store.get_trace(answer["trace_id"]) or {}
    row["answer"] = {k: v for k, v in answer.items() if k != "citations"}
    row["usage"] = answer["usage"]

    if gold_set:
        if retrieved is not None:
            row["recall@5"] = rmetrics.recall_at_k(retrieved, gold_set, 5)
            row["mrr"] = rmetrics.reciprocal_rank(retrieved, gold_set)
        else:
            seen = _chunks_seen(trace)
            row["evidence_recall"] = sum(bool(seen & s) for s in gold_set.per_source) / len(
                gold_set.per_source
            )

    row["abstained"] = answer["abstained"]
    row["format_error"] = answer.get("format_error", False)
    row["numbers"] = score_numbers(q, answer)
    row["citations"] = citation_accuracy(answer, store)

    if judge:
        if q.get("required_points"):
            covered = judge.points_covered(q["question"], q["required_points"], answer["answer"])
            row["points_covered"] = covered
        claims = [c for c in answer["claims"] if c["chunk_ids"]]
        if claims:
            supported = judge.faithfulness(claims, answer["citations"])
            row["faithfulness"] = {
                "claims": len(claims),
                "supported": sum(supported),
                "verdicts": {str(c["id"]): v for c, v in zip(claims, supported, strict=True)},
            }

    if options.system == "research":
        row["agent"] = agent_metrics(q, trace, references.get(q["id"]))

    row["correctness"] = _correctness(q, row)
    row["task_complete"] = _task_complete(q, row)
    return row


def _chunks_seen(trace: dict) -> set[str]:
    """Chunk IDs returned to the agent by any search or section read."""
    seen = set()
    for span in trace.get("spans", []):
        if span["type"] != "tool" or span["is_error"]:
            continue
        try:
            output = json.loads(span["output"])
        except json.JSONDecodeError:
            continue  # truncated in the trace
        for item in output.get("results", []) + output.get("chunks", []):
            seen.add(item["chunk_id"])
    return seen


def _correctness(q: dict, row: dict) -> float | None:
    if q.get("should_abstain"):
        return float(row["abstained"])
    if row["numbers"]:
        return row["numbers"]["matched"] / row["numbers"]["expected"]
    if "points_covered" in row:
        return sum(row["points_covered"]) / len(row["points_covered"])
    return None


def _task_complete(q: dict, row: dict) -> bool | None:
    if q.get("should_abstain"):
        return row["abstained"]
    if row["abstained"]:
        return False
    parts = []
    if row["numbers"]:
        parts.append(row["numbers"]["all"])
    if q.get("required_points"):
        if "points_covered" not in row:
            return None  # not measurable without the judge
        parts.append(all(row["points_covered"]))
    return all(parts) if parts else None


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(statistics.mean(values), 4) if values else None


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def summarize(rows: list[dict], system: str) -> dict:
    ok = [r for r in rows if not r.get("error")]
    summary: dict = {
        "questions": len(rows),
        "errors": len(rows) - len(ok),
        "verified_questions": sum(r["verified"] for r in rows),
    }
    retrieval = {
        "recall@5": _mean(r.get("recall@5") for r in ok),
        "recall@10": _mean(r.get("recall@10") for r in ok),
        "mrr": _mean(r.get("mrr") for r in ok),
        "evidence_recall": _mean(r.get("evidence_recall") for r in ok),
        "context_relevance": _mean(r.get("context_relevance") for r in ok),
        "scored": sum(("recall@5" in r) or ("evidence_recall" in r) for r in ok),
    }
    summary["retrieval"] = {k: v for k, v in retrieval.items() if v is not None}
    if system == "retrieval":
        return summary

    # Rates are over completed questions; errors are reported separately (and count as
    # failed task completion), so an erroring run can't look well calibrated.
    abstain = [r for r in ok if r["should_abstain"]]
    answerable = [r for r in ok if not r["should_abstain"]]
    faith = [r["faithfulness"] for r in ok if r.get("faithfulness")]
    cites = [r["citations"] for r in ok if r.get("citations")]
    completion = [r.get("task_complete") for r in rows if not r.get("error")] + [
        False for r in rows if r.get("error")
    ]
    summary["answers"] = {
        "correctness": _mean(r.get("correctness") for r in ok),
        "task_completion": _mean(float(c) for c in completion if c is not None),
        "faithfulness": _ratio(sum(f["supported"] for f in faith), sum(f["claims"] for f in faith)),
        "citation_accuracy": _ratio(
            sum(c["accurate"] for c in cites), sum(c["numeric_claims"] for c in cites)
        ),
        # Answered although the corpus can't answer.
        "unsupported_answer_rate": _ratio(sum(not r["abstained"] for r in abstain), len(abstain)),
        "false_abstention_rate": _ratio(
            sum(bool(r.get("abstained")) for r in answerable), len(answerable)
        ),
        "format_error_rate": _ratio(sum(bool(r.get("format_error")) for r in ok), len(ok)),
        # Text answers need the judge; without it, correctness covers only these questions.
        "correctness_scored": sum(r.get("correctness") is not None for r in ok),
    }
    if system == "research":
        agents = [r["agent"] for r in ok if r.get("agent")]
        calls = sum(a["tool_calls"] for a in agents)
        summary["agent"] = {
            "tool_selection": _mean(float(a["tool_selection"]) for a in agents),
            "tool_call_correctness": _ratio(sum(a["valid_calls"] for a in agents), calls),
            "mean_tool_calls": _mean(a["tool_calls"] for a in agents),
            "unnecessary_calls": _mean(
                (
                    a["calls_beyond_reference"]
                    if a["calls_beyond_reference"] is not None
                    else a["duplicate_calls"]
                )
                + a.get("over_budget_calls", 0)
                for a in agents
            ),
            "over_budget_rate": _mean(float(a.get("over_budget_calls", 0) > 0) for a in agents),
        }
    usage = [r["usage"] for r in ok if r.get("usage")]
    latencies = sorted(u["latency_s"] for u in usage)
    summary["cost"] = {
        "mean_latency_s": _mean(latencies),
        "p95_latency_s": latencies[int(0.95 * (len(latencies) - 1))] if latencies else None,
        "mean_input_tokens": _mean(u["input_tokens"] for u in usage),
        "mean_output_tokens": _mean(u["output_tokens"] for u in usage),
        "mean_cost_usd": _mean(u["cost_usd"] for u in usage),
        "total_cost_usd": round(sum(u["cost_usd"] for u in usage), 4),
    }
    by_category: dict[str, list[dict]] = {}
    for r in rows:
        by_category.setdefault(r["category"], []).append(r)
    summary["by_category"] = {
        name: {
            "questions": len(group),
            "correctness": _mean(r.get("correctness") for r in group if not r.get("error")),
            "task_completion": _mean(
                float(r["task_complete"]) if r.get("task_complete") is not None else None
                for r in group
            ),
        }
        for name, group in sorted(by_category.items())
    }
    return summary
