#!/usr/bin/env python3
"""Stage 3 playground (Ask mode): retrieve -> one model call -> JSON claims -> verification.
Guide: docs/STAGE3_ANSWER.md. Needs a model: Claude (ANTHROPIC_API_KEY) or an open model on vLLM.

  python playground/answer.py "What was Apple's revenue in 2024?"
  python playground/answer.py --gold nvda-revenue-2024-shifted               # a gold question, scored
  python playground/answer.py "What will Apple's revenue be in 2027?" --k 4
  python playground/answer.py "..." --model openai:Qwen/Qwen3.5-9B           # vLLM on :8001 (scripts/serve_vllm.sh)
  python playground/answer.py "..." --dry-run                                # prompt only, no model call

Writes output/answer/<tag>.md: the prompt, the raw reply and the verified claims.
"""

import argparse
import json

from _common import settings, table, write_report

from tenk_agent import gold
from tenk_agent.answers import finalize
from tenk_agent.ask import build_prompt
from tenk_agent.evaluation.scoring import score_numbers
from tenk_agent.models import load_model, parse_json
from tenk_agent.retrieval import Retriever
from tenk_agent.store import Store


def main():
    s = settings()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("question", nargs="?")
    p.add_argument("--gold", help="use a gold question by id and score the answer")
    p.add_argument("--k", type=int, default=s.ask_k, help="chunks given to the model")
    p.add_argument("--model", help=f"default: {s.model}")
    p.add_argument("--no-verify", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="build the prompt, don't call the model")
    p.add_argument("--device", default=s.device)
    p.add_argument("--tag", default="play")
    a = p.parse_args()

    q = next((g for g in gold.load() if g["id"] == a.gold), None) if a.gold else None
    question = q["question"] if q else a.question
    if not question:
        p.error("give a question or --gold ID")
    s.device = a.device
    store = Store(s.db_path)

    # 1. retrieve (stage 2)
    chunks = Retriever.from_settings(store, s).search(question, k=a.k)
    print(f"Q: {question}\nretrieved: {', '.join(c.id for c in chunks)}")

    # 2. prompt: rules + corpus table (system), excerpts + question (user)
    system, messages = build_prompt(question, chunks, store)
    print(f"prompt: system {len(system):,} chars · user {len(messages[0]['content']):,} chars")
    report = f"# Answer\n\nQ: {question}\n\n## System prompt\n\n```\n{system}\n```\n\n"
    report += f"## User message\n\n```\n{messages[0]['content']}\n```\n\n"
    if a.dry_run:
        write_report("answer", a.tag, report)
        return

    # 3. one model call
    model = load_model(s, a.model)
    response = model.complete(messages, system=system, max_tokens=4000)
    print(
        f"{model.name} · {response.latency_s:.1f} s · {response.input_tokens:,} in / "
        f"{response.output_tokens:,} out · ${response.cost_usd:.4f}"
    )
    report += f"## Raw reply ({model.name})\n\n```\n{response.text}\n```\n\n"

    # 4. parse + verify (stage 5)
    try:
        raw = parse_json(response.text)
    except ValueError as e:
        print(f"reply is not JSON: {e}")
        write_report("answer", a.tag, report)
        return
    answer = finalize(raw, store, verify=not a.no_verify)
    rows = [
        [c["id"], c["status"], c["value"], c["unit"], ", ".join(c["chunk_ids"]), c["status_reason"]]
        for c in answer["claims"]
    ]
    claims = table(["#", "Status", "Value", "Unit", "Cites", "Why"], rows)
    print(f"\n{answer['answer']}\n")
    if answer["scope_notice"]:
        print(f"scope: {answer['scope_notice']}")
    print(f"abstained: {answer['abstained']}\n{claims}")
    report += f"## Answer\n\n{answer['answer']}\n\nabstained: {answer['abstained']}\n\n{claims}\n"

    if q:
        numbers = score_numbers(q, answer)
        expected = gold.expected_values(q)
        verdict = (
            f"abstained {answer['abstained']} (expected {q['should_abstain']})"
            if q.get("should_abstain")
            else f"numbers matched {numbers['matched']}/{numbers['expected']}"
            if numbers
            else "text answer: scored by the LLM judge in `tenk eval`"
        )
        print(
            f"\ngold: {json.dumps(expected) if expected else q.get('required_points')}\nscore: {verdict}"
        )
        report += (
            f"\n## Gold\n\n{json.dumps(expected or q.get('required_points'))}\n\nscore: {verdict}\n"
        )
    write_report("answer", a.tag, report)


if __name__ == "__main__":
    main()
