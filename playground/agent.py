#!/usr/bin/env python3
"""Stage 4 playground, part 2: the Research agent, step by step, then its whole conversation.
Guide: docs/STAGE4_AGENT.md. Needs a model: Claude (ANTHROPIC_API_KEY) or an open model on vLLM.

  python playground/agent.py "Compare Apple's and Microsoft's R&D spending from 2023-2025."
  python playground/agent.py --gold aapl-rd-yoy-fy2023-2025                  # a gold question, scored
  python playground/agent.py "..." --max-calls 5 --tag tight                 # small tool budget
  python playground/agent.py "..." --no-repair                               # V3: no repair round
  python playground/agent.py "..." --model openai:Qwen/Qwen3.5-9B            # vLLM on :8001

Writes output/agent/<tag>.md: every model turn and tool call with tokens and latency, and the answer.
"""

import argparse
import json

from _common import preview, settings, table, write_report

from tenk_agent import gold
from tenk_agent.agent import research
from tenk_agent.evaluation.scoring import agent_metrics, score_numbers
from tenk_agent.models import load_model
from tenk_agent.retrieval import Retriever
from tenk_agent.store import Store
from tenk_agent.tools import Toolbox


def main():
    s = settings()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("question", nargs="?")
    p.add_argument("--gold", help="use a gold question by id and score the answer")
    p.add_argument("--model", help=f"default: {s.model}")
    p.add_argument("--max-calls", type=int, default=s.max_tool_calls)
    p.add_argument("--no-repair", action="store_true")
    p.add_argument("--no-verify", action="store_true")
    p.add_argument("--structured-data", action="store_true", help="V5: add the XBRL tool")
    p.add_argument("--device", default=s.device)
    p.add_argument("--tag", default="play")
    a = p.parse_args()

    q = next((g for g in gold.load() if g["id"] == a.gold), None) if a.gold else None
    question = q["question"] if q else a.question
    if not question:
        p.error("give a question or --gold ID")
    s.device = a.device
    s.structured_data = a.structured_data or s.structured_data
    store = Store(s.db_path)
    toolbox = Toolbox.from_settings(store, Retriever.from_settings(store, s), s)
    model = load_model(s, a.model)
    print(f"Q: {question}\nmodel: {model.name} · budget {a.max_calls} tool calls\n")

    # 1. run, printing each step as it streams
    answer = None
    for event in research(
        question,
        store,
        toolbox,
        model,
        a.max_calls,
        verify=not a.no_verify,
        repair=not a.no_repair,
        repair_calls=s.repair_calls,
    ):
        if event["type"] == "thought":
            print(f"  · {preview(event['text'], 150)}")
        elif event["type"] == "step":
            mark = "✗" if event["is_error"] else "✓"
            print(f"{event['n']:>2} {mark} {event['label']}\n     {event['summary'][:150]}")
        elif event["type"] == "error":
            print(f"error: {event['message']}")
            return
        else:
            answer = event["answer"]

    # 2. the answer and its claims
    rows = [
        [
            c["id"],
            c["status"],
            c["value"],
            c["unit"],
            ", ".join(c["chunk_ids"]),
            c["status_reason"][:60],
        ]
        for c in answer["claims"]
    ]
    claims = table(["#", "Status", "Value", "Unit", "Cites", "Why"], rows)
    u = answer["usage"]
    usage = (
        f"{u['llm_calls']} model calls · {u['tool_calls']} tool calls · {u['latency_s']:.0f} s · "
        f"{u['input_tokens']:,} in / {u['output_tokens']:,} out · ${u['cost_usd']:.4f}"
        + (" · repaired" if answer.get("repaired") else "")
    )
    print(f"\n{answer['answer']}\n\n{claims}\n\n{usage}")

    # 3. the trace: every span in order
    trace = store.get_trace(answer["trace_id"])
    spans = [
        [
            i,
            sp["type"],
            sp["name"],
            json.dumps(sp["input"])[:80]
            if sp["type"] == "tool"
            else f"{sp['input']['messages']} messages",
            f"{sp.get('input_tokens', '')}/{sp.get('output_tokens', '')}"
            if sp["type"] == "llm"
            else "",
            f"{sp['latency_s']:.2f}",
        ]
        for i, sp in enumerate(trace["spans"], 1)
    ]
    trace_table = table(["#", "Type", "Name", "Input", "Tokens in/out", "Seconds"], spans)

    scored = ""
    if q:
        metrics = agent_metrics(q, trace)
        numbers = score_numbers(q, answer)
        scored = (
            f"expected tools {q.get('expected_tools')} · used {metrics['tools_used']} · "
            f"tool selection {metrics['tool_selection']} · duplicate calls {metrics['duplicate_calls']} · "
            f"over budget {metrics['over_budget_calls']}"
            + (f" · numbers matched {numbers['matched']}/{numbers['expected']}" if numbers else "")
        )
        print(scored)
    write_report(
        "agent",
        a.tag,
        f"# Agent\n\nQ: {question}\n\n{usage}\n\n## Answer\n\n{answer['answer']}\n\n{claims}\n\n"
        f"{scored}\n\n## Trace ({answer['trace_id']})\n\n{trace_table}\n",
    )


if __name__ == "__main__":
    main()
