"""Multi-turn follow-ups: append vs rewrite (config chat.history), through the web API.

  docker compose up -d                       # vLLM (200k context) + era on :8000
  python experiments/multiturn/compare.py [--mode research] [--only dash] [--judge-only]

Each conversation runs once per strategy, in its own chat, turn by turn. Per turn it records
the answer, the rewritten question (rewrite), seconds, tool calls, and the model's input tokens
(peak and total). Then the served model judges each follow-up pair blind, in both orders (A/B
then B/A, to cancel position bias): which answer better answers the follow-up *as the user
meant it in context*. Writes output/multiturn/<mode>/results.json and report.md.
"""

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

API = "http://localhost:8000"
LLM = "http://localhost:8001/v1"
MODEL = "Qwen/Qwen3.6-27B-FP8"
OUT = Path(__file__).resolve().parents[2] / "output" / "multiturn"
STRATEGIES = ("append", "rewrite")

CONVERSATIONS = {
    "dash": [  # the conversation that started this
        "What do you think is the price target for DASH and why?",
        "what other reasons are there besides on based on financial models? Consider macro, "
        "risks, and other factors too",
        "How sensitive is that target to the take rate?",
    ],
    "uber": [
        "How did Uber's mobility gross bookings grow in the latest quarter?",
        "And delivery?",
        "Which of the two segments had the better adjusted EBITDA margin?",
    ],
    "abnb-bkng": [
        "Compare Airbnb's and Booking's revenue growth in their latest quarters.",
        "Why the difference?",
        "What did each guide for next quarter?",
    ],
    "lyft": [
        "What was Lyft's adjusted EBITDA margin last quarter?",
        "How does that compare to a year earlier?",
        "What could pressure it from here?",
    ],
}

JUDGE = """You compare two answers to a FOLLOW-UP question in an equity research chat.

Conversation before the follow-up:
{context}

Follow-up: {question}

Answer A:
{a}

Answer B:
{b}

Which answer better answers the follow-up as the user meant it, given the conversation
(right company and topic carried over, builds on what was said, specific and grounded)?
Reply with JSON only: {{"winner": "A" | "B" | "tie", "why": "<one sentence>"}}"""


def post_chat(chat_id, mode, question, as_of, strategy) -> dict:
    """One chat turn; reads the SSE stream until done/error → {chat_id, message, events}."""
    body = json.dumps({"chat_id": chat_id, "mode": mode, "question": question,
                       "as_of": as_of, "history": strategy}).encode()
    req = urllib.request.Request(f"{API}/api/chat", body,
                                 {"Content-Type": "application/json"})
    events, out = [], {"chat_id": chat_id}
    with urllib.request.urlopen(req, timeout=3600) as r:
        for line in r:
            line = line.decode().strip()
            if not line.startswith("data: "):
                continue
            e = json.loads(line[6:])
            events.append(e)
            if e["type"] == "chat":
                out["chat_id"] = e["chat_id"]
            if e["type"] in ("done", "error"):
                out["message"] = e["message"]
                break
    return out


def summarize_turn(question, message, seconds) -> dict:
    models = [e for e in message.get("trace", []) if e.get("type") == "model"]
    answer = message.get("answer") or {}
    return {
        "question": question, "rewritten": message.get("rewritten"),
        "answer": answer.get("answer") or message.get("error") or "",
        "abstained": answer.get("abstained"), "stop": message.get("stop"),
        "error": message.get("error"), "seconds": round(seconds, 1),
        "tool_calls": sum(e.get("type") == "tool" for e in message.get("trace", [])),
        "input_tokens_peak": max((e.get("input_tokens") or 0 for e in models), default=0),
        "input_tokens_total": sum(e.get("input_tokens") or 0 for e in models),
        "output_tokens": sum(e.get("output_tokens") or 0 for e in models),
    }


def run_conversation(turns, mode, as_of, strategy) -> list[dict]:
    chat_id, out = None, []
    for q in turns:
        started = time.time()
        r = post_chat(chat_id, mode, q, as_of, strategy)
        chat_id = r["chat_id"]
        out.append(summarize_turn(q, r.get("message") or {}, time.time() - started))
        print(f"  [{strategy}] {q[:60]!r}: {out[-1]['seconds']}s, "
              f"peak {out[-1]['input_tokens_peak']} tok"
              + (f", rewrite: {out[-1]['rewritten']!r}" if out[-1]["rewritten"] else ""),
              flush=True)
    return out


def complete(prompt: str) -> str:
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": 300, "temperature": 0,
                       "chat_template_kwargs": {"enable_thinking": False}}).encode()
    req = urllib.request.Request(f"{LLM}/chat/completions", body,
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)["choices"][0]["message"]["content"] or ""


def judge(turns_a, turns_b, i) -> dict:
    """Follow-up i judged in both orders; a strategy wins only if it wins both ways."""
    context = "\n\n".join(f"User: {t['question']}\nAnalyst: {t['answer'][:3000]}"
                          for t in turns_a[:i])
    q = turns_a[i]["question"]
    votes = []
    for first, second, names in ((turns_a, turns_b, STRATEGIES),
                                 (turns_b, turns_a, STRATEGIES[::-1])):
        text = complete(JUDGE.format(context=context, question=q, a=first[i]["answer"][:6000],
                                     b=second[i]["answer"][:6000]))
        m = re.search(r"\{.*\}", text, re.S)
        v = json.loads(m.group()) if m else {"winner": "tie", "why": "unparsed"}
        votes.append({"winner": {"A": names[0], "B": names[1]}.get(v.get("winner"), "tie"),
                      "why": v.get("why", "")})
    winner = votes[0]["winner"] if votes[0]["winner"] == votes[1]["winner"] else "tie"
    return {"winner": winner, "votes": votes}


def report(results: dict, mode: str) -> str:
    lines = [f"# Multi-turn follow-ups: append vs rewrite ({mode} mode)", ""]
    wins = {s: 0 for s in (*STRATEGIES, "tie")}
    stats = {s: {"seconds": [], "peak": []} for s in STRATEGIES}
    for name, conv in results.items():
        lines += [f"## {name}", ""]
        for i, q in enumerate(conv["append"]):
            lines.append(f"**Turn {i + 1}:** {q['question']}")
            if i and (rw := conv["rewrite"][i]["rewritten"]):
                lines.append(f"- rewrite → *{rw}*")
            for s in STRATEGIES:
                t = conv[s][i]
                stats[s]["seconds"].append(t["seconds"])
                stats[s]["peak"].append(t["input_tokens_peak"])
                lines.append(f"- **{s}** ({t['seconds']}s, {t['tool_calls']} tools, peak "
                             f"{t['input_tokens_peak']} tok): {t['answer'][:500]}")
            if i:
                j = conv["judge"][i - 1]
                wins[j["winner"]] += 1
                lines.append(f"- judge: **{j['winner']}** — "
                             + " / ".join(v["why"] for v in j["votes"]))
            lines.append("")
    head = ["## Summary", "", "| | append | rewrite |", "|---|---|---|",
            f"| follow-up wins (both orders agree) | {wins['append']} | {wins['rewrite']} |",
            f"| ties / split votes | {wins['tie']} | |"]
    for key, label in (("seconds", "mean seconds per turn"), ("peak", "mean peak input tokens")):
        vals = [sum(stats[s][key]) / max(len(stats[s][key]), 1) for s in STRATEGIES]
        head.append(f"| {label} | {vals[0]:.0f} | {vals[1]:.0f} |")
    head.append(f"| max peak input tokens | {max(stats['append']['peak'] or [0])} | "
                f"{max(stats['rewrite']['peak'] or [0])} |")
    return "\n".join(lines[:2] + head + [""] + lines[2:])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", default="research", choices=("ask", "research", "investigate"))
    p.add_argument("--as-of", default=time.strftime("%Y-%m-%d"))
    p.add_argument("--only", nargs="*", help="conversation names")
    p.add_argument("--judge-only", action="store_true", help="rejudge saved results")
    args = p.parse_args()
    out = OUT / args.mode
    out.mkdir(parents=True, exist_ok=True)
    path = out / "results.json"
    results = json.loads(path.read_text()) if path.exists() else {}
    if not args.judge_only:
        for name, turns in CONVERSATIONS.items():
            if args.only and name not in args.only:
                continue
            print(name, flush=True)
            results[name] = {s: run_conversation(turns, args.mode, args.as_of, s)
                             for s in STRATEGIES}
            path.write_text(json.dumps(results, indent=1))
    for conv in results.values():
        conv["judge"] = [judge(conv["append"], conv["rewrite"], i)
                         for i in range(1, len(conv["append"]))]
    path.write_text(json.dumps(results, indent=1))
    (out / "report.md").write_text(report(results, args.mode))
    print(f"wrote {out / 'report.md'}")


if __name__ == "__main__":
    main()
