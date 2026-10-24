#!/usr/bin/env python3
"""V3 experiment: the agent against V2 on the same questions, with and without SEC tools.
Spec and results: experiments/V3_AGENT.md.

  v2    V1's top 8 ─► one model call                        (V2, unchanged)
  text  agent: search_filings, get_section, calculator
  full  agent: + xbrl_facts, edgar_filings                 (SEC data from the cache, offline)
  ─► correct, declined, grounded, gold cited (evaluation/scoring.py) + tool use (tools.json)

  taskset -c 16-31 python experiments/v3/run.py
  taskset -c 16-31 python experiments/v3/run.py --variant full --question tsla-revenue-fy2024-xbrl
  taskset -c 16-31 python experiments/v3/run.py --resume          # skip (question, variant) done

Needs the model served (scripts/serve_vllm.sh qwen3.6-27b-fp8). Text answers are judged
afterwards: serve the judge, then experiments/v2/judge.py --config experiments/v3/config.yaml.
Writes output/generation/<label>/: config.yaml, results.json (per question and variant: the
answer, claims, scores, every step), scores.json (per variant), tools.json (tool use).

Questions with `text_tools: abstain` need SEC data: under variant text (and v2) they count
as decline questions, since declining is then the right answer. Questions with `with_sec_data`
are the reverse: declines that SEC data answers, so under full they count as answerable.
"""

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v2"))
from run import _pct, _verdict, _write, print_scores  # noqa: E402  (experiments/v2/run.py)

from tenk_agent.agent.loop import Agent  # noqa: E402
from tenk_agent.agent.sec import SecData  # noqa: E402
from tenk_agent.agent.tools import Toolbox  # noqa: E402
from tenk_agent.evaluation.retrieval_metrics import gold_chunks  # noqa: E402
from tenk_agent.evaluation.scoring import grade, summarize  # noqa: E402
from tenk_agent.generation.generate import build_prompt, generate  # noqa: E402
from tenk_agent.models import get_model  # noqa: E402
from tenk_agent.retrieval.search import SubQuestionRetriever  # noqa: E402
from tenk_agent.store import Store  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(__file__).resolve().parent / "config.yaml"
VARIANTS = ("v2", "text", "full")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", type=Path, default=CONFIG, help="default: %(default)s")
    ap.add_argument("--question", action="append", help="only these gold ids (default: all)")
    ap.add_argument("--variant", action="append", choices=VARIANTS, help="default: run.variants")
    ap.add_argument("--label", help="override run.label")
    ap.add_argument("--resume", action="store_true", help="keep finished rows, run the rest")
    args = ap.parse_args()
    config = yaml.safe_load(args.config.read_text())
    run, generation, agent_config = config["run"], config["generation"], config["agent"]
    run.update({k: v for k, v in (("label", args.label), ("variants", args.variant)) if v})
    retrieval = yaml.safe_load((ROOT / config["retrieval"]).read_text())["retrieval"]
    config["retrieval"] = {"from": config["retrieval"]} | retrieval
    out = ROOT / "output" / "generation" / run["label"]
    out.mkdir(parents=True, exist_ok=True)

    store = Store(ROOT / run["db"])
    questions = []
    for path in run["gold"]:
        for line in (ROOT / path).read_text().splitlines():
            q = json.loads(line)
            questions.append(q | {"gold_set": q.get("gold_set", Path(path).stem)})
    if args.question:
        questions = [q for q in questions if q["id"] in args.question]
    if not config["score"]["restatements"]:
        questions = [q | {"acceptable_sources": []} for q in questions]

    try:
        pipeline = SubQuestionRetriever.from_settings(store, retrieval)
    except RuntimeError as e:
        sys.exit(str(e))
    sec = SecData(ROOT / agent_config["sec_cache"])  # no client: offline
    model = get_model(
        generation["model"], base_url=generation["base_url"], thinking=generation["thinking"]
    )
    agents = {
        name: Agent(
            model,
            Toolbox(store, pipeline.hybrid, sec, name, agent_config["section_chunks"]),
            max_calls=agent_config["max_calls"],
            max_turns=agent_config["max_turns"],
            max_tokens=generation["max_tokens"],
            result_chars=agent_config["result_chars"],
            max_context=agent_config["max_context"],
            prompt=ROOT / agent_config["prompt"],
        )
        for name in ("text", "full")
    }
    search_lock = threading.Lock()  # the embedder and reranker are shared between workers
    for agent in agents.values():
        agent.toolbox.call = _locked(agent.toolbox.call, search_lock)

    done = []
    if args.resume and (out / "results.json").exists():
        done = json.loads((out / "results.json").read_text())
    finished = {(r["id"], r["context"]) for r in done}
    tasks = [(q, v) for q in questions for v in run["variants"] if (q["id"], v) not in finished]
    print(f"{len(tasks)} runs ({len(finished)} kept)")
    lock = threading.Lock()

    def answer(task) -> dict:
        q, variant = task
        q = scored_as(q, variant)
        per_source = sources(store, q)
        steps = []
        if variant == "v2":
            with search_lock:
                chunks = pipeline.search(q["question"], retrieval["k"]).chunks
            system, user = build_prompt(q["question"], chunks, store, ROOT / generation["prompt"])
            result = generate(model, system, user, store, generation["max_tokens"])
            answer, context = result.answer, [c.id for c in chunks]
            seconds, tokens_in, tokens_out = (
                result.seconds,
                result.input_tokens,
                result.output_tokens,
            )
            calls, stop = len(result.replies), "answered"
        else:
            result = agents[variant].run(q["question"])
            steps = [asdict(s) for s in result.steps]
            answer, context = result.answer, seen_ids(result.steps)
            seconds = result.seconds
            tokens_in = sum(s.input_tokens for s in result.steps)
            tokens_out = sum(s.output_tokens for s in result.steps)
            calls, stop = len(result.tool_calls), result.stop
        row = {
            "id": q["id"],
            "context": variant,
            "gold_set": q["gold_set"],
            "split": q.get("split"),
            "kind": q.get("kind"),
            "category": q.get("category"),
            "question": q["question"],
            "should_abstain": bool(q.get("should_abstain")),
            "required_points": q.get("required_points") or [],
            "expected_tools": q.get("expected_tools") or [],
            "chunks": context,
            **answer,
            **grade(q, answer, context, per_source),
            "calls": calls,
            "stop": stop,
            "seconds": seconds,
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "steps": steps,
        }
        with lock:
            done.append(row)
            _write(out / "results.json", done)  # after every answer, for --resume
        print(f"{q['id']:<42} {variant:<5} {_verdict(row)} {calls:>2} calls {seconds:.0f}s")
        return row

    def attempt(task):
        try:
            return answer(task)
        except Exception as e:  # one failed question shouldn't end the run; --resume retries it
            print(f"{task[0]['id']:<42} {task[1]:<5} FAILED {type(e).__name__}: {e}"[:300])

    with ThreadPoolExecutor(max_workers=run["workers"]) as pool:
        list(pool.map(attempt, tasks))

    variants = [v for v in VARIANTS if any(r["context"] == v for r in done)]
    scores = {v: summarize([r for r in done if r["context"] == v]) for v in variants}
    _write(out / "results.json", done)
    _write(out / "scores.json", scores)
    _write(out / "tools.json", tool_use(done, variants))
    (out / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    print_scores(scores)
    print_tools(tool_use(done, variants))
    unjudged = sum(len(s["unjudged"]) for s in scores.values())
    if unjudged:
        print(f"\n{unjudged} text answers unjudged: serve the judge, then run v2/judge.py")


def scored_as(q: dict, variant: str) -> dict:
    """A question needing SEC data is a decline question for the variants without it
    (`text_tools: abstain`); a decline question SEC data can answer is answerable under full
    (`with_sec_data`: the fields that change)."""
    if variant != "full" and q.get("text_tools") == "abstain":
        return q | {"should_abstain": True}
    if variant == "full" and q.get("with_sec_data"):
        return q | q["with_sec_data"]
    return q


def sources(store: Store, q: dict) -> list[set[str]]:
    """Each gold source's ids: chunks for 10-K text (restatements included), the fact id for
    XBRL, nothing for EDGAR metadata (not citable)."""
    per_source = gold_chunks(store, q).per_source
    for i, source in enumerate(q.get("sources", [])):
        if source.get("source_type") == "xbrl":
            per_source[i] = {source["evidence"].split(" = ")[0]}
    return per_source


def seen_ids(steps) -> list[str]:
    """Every chunk and XBRL fact id a tool returned: what the agent was shown."""
    ids = []
    for step in steps:
        result = step.result or {}
        ids += [c["chunk_id"] for c in result.get("chunks", [])]
        ids += [v["xbrl_id"] for v in result.get("values", [])]
    return list(dict.fromkeys(ids))


def tool_use(rows: list[dict], variants: list[str]) -> dict:
    """Per agent variant: calls per question, errors, repeats, budget stops, and how the
    tools called match each question's expected_tools (recall: expected tools called;
    precision: called tools that were expected, over questions that expect some)."""
    out = {}
    for variant in variants:
        group = [r for r in rows if r["context"] == variant and r["steps"]]
        if not group:
            continue
        recall, precision, calls, errors, repeats = [], [], [], 0, 0
        by_tool: dict[str, int] = {}
        for r in group:
            tools = [s for s in r["steps"] if s["kind"] == "tool"]
            used = {s["name"] for s in tools}
            for s in tools:
                by_tool[s["name"]] = by_tool.get(s["name"], 0) + 1
            errors += sum("error" in (s["result"] or {}) for s in tools)
            keys = [json.dumps([s["name"], s["arguments"]], sort_keys=True) for s in tools]
            repeats += len(keys) - len(set(keys))
            calls.append(len(tools))
            expected = set(r["expected_tools"]) & set(by_tool_set(variant))
            if expected:
                recall.append(len(expected & used) / len(expected))
                if used:
                    precision.append(len(expected & used) / len(used))
        out[variant] = {
            "questions": len(group),
            "calls_mean": sum(calls) / len(calls),
            "calls_max": max(calls),
            "by_tool": dict(sorted(by_tool.items())),
            "error_calls": errors,
            "repeated_calls": repeats,
            "budget_stops": sum(r["stop"] == "budget" for r in group),
            "expected_tools_recall": sum(recall) / len(recall) if recall else None,
            "expected_tools_precision": sum(precision) / len(precision) if precision else None,
            "seconds_mean": sum(r["seconds"] for r in group) / len(group),
            "input_tokens_mean": sum(r["input_tokens"] for r in group) / len(group),
        }
    return out


def by_tool_set(variant: str) -> tuple[str, ...]:
    from tenk_agent.agent.tools import TOOL_SETS

    return TOOL_SETS[variant]


def print_tools(stats: dict) -> None:
    for variant, s in stats.items():
        print(
            f"\n{variant}: {s['calls_mean']:.1f} calls/question (max {s['calls_max']}), "
            f"{s['error_calls']} errors, {s['repeated_calls']} repeats, "
            f"{s['budget_stops']} budget stops, expected tools recall "
            f"{_pct(s['expected_tools_recall'])} precision {_pct(s['expected_tools_precision'])}"
        )
        print("  " + ", ".join(f"{k} {n}" for k, n in s["by_tool"].items()))


def _locked(call, lock):
    def run(name, arguments):
        if name in ("search_filings",):
            with lock:
                return call(name, arguments)
        return call(name, arguments)

    return run


if __name__ == "__main__":
    main()
