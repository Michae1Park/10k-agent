"""Multi-turn follow-ups: append vs rewrite (config chat.history), through the web API.

  docker compose up -d                       # vLLM (200k context) + era on :8000
  python experiments/multiturn/compare.py --no-judge          # 1. answers, with Qwen served
  docker compose stop vllm && <serve gpt-oss-20b on :8002>     # 2. the separate judge (D-029)
  python experiments/multiturn/compare.py --judge-only         # 3. judge, write the report
  options: [--mode research] [--only dash ...] [--rerun] (default: skip finished conversations)

Each conversation runs once per strategy, in its own chat, turn by turn. Per turn it records
the answer, the rewritten question (rewrite), seconds, tool calls, and the model's input tokens
(peak and total). Then a separate judge model (gpt-oss-20b, D-029) judges each follow-up pair
blind, in both orders (A/B then B/A, to cancel position bias): which answer better answers the
follow-up *as the user meant it in context*.
Writes output/multiturn/<mode>/results.json and report.md.
"""

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

API = "http://localhost:8000"
JUDGE_URL = "http://localhost:8002/v1"
JUDGE_MODEL = "openai/gpt-oss-20b"
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
    "expe": [
        "What was Expedia's B2B revenue growth in the latest quarter?",
        "Is that faster than B2C?",
        "What's driving the gap?",
    ],
    "cart": [
        "How did Instacart's GTV grow in the latest quarter?",
        "What about orders?",
        "And average order value?",
    ],
    "uber-lyft-fcf": [  # company switch
        "What was Uber's free cash flow over the last twelve months?",
        "What about Lyft?",
        "Which of them converts more of its adjusted EBITDA into free cash flow?",
    ],
    "trip": [
        "How did Tripadvisor's revenue change in the latest quarter?",
        "Which segment drove it?",
        "Is the company still buying back stock?",
    ],
    "bkng-abnb-switch": [  # turn 2 stands alone: the history must not bend it
        "What was Booking's room nights growth in the latest quarter?",
        "How has Airbnb's take rate trended over the past year?",
        "How do the two compare on marketing spend as a share of revenue?",
    ],
    "dash-margin": [
        "How has DoorDash's net revenue margin trended over the past year?",
        "Why did it change?",
        "Does management expect it to keep rising?",
    ],
    "travel-macro": [
        "How exposed are the online travel companies to a slowdown in consumer spending?",
        "Which of them is most exposed?",
        "What did that company say about demand in its latest results?",
    ],
    "lyft-cases": [
        "Give me the bull case for Lyft.",
        "Now the bear case.",
        "Which do you find more convincing, and why?",
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
    body = json.dumps({"model": JUDGE_MODEL, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": 4000, "temperature": 0}).encode()
    req = urllib.request.Request(f"{JUDGE_URL}/chat/completions", body,
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
    lines = [f"# Multi-turn follow-ups: append vs rewrite ({mode} mode)", "",
             f"{len(results)} conversations, answers by the served Qwen model; each follow-up "
             f"judged blind by {JUDGE_MODEL} in both orders (a win needs both).", ""]
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
    return "\n".join(lines[:4] + head + [""] + lines[4:])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", default="research", choices=("ask", "research", "investigate"))
    p.add_argument("--as-of", default=time.strftime("%Y-%m-%d"))
    p.add_argument("--only", nargs="*", help="conversation names")
    p.add_argument("--judge-only", action="store_true", help="rejudge saved results")
    p.add_argument("--no-judge", action="store_true", help="answers only")
    p.add_argument("--rerun", action="store_true", help="rerun finished conversations")
    args = p.parse_args()
    out = OUT / args.mode
    out.mkdir(parents=True, exist_ok=True)
    path = out / "results.json"
    results = json.loads(path.read_text()) if path.exists() else {}
    if not args.judge_only:
        for name, turns in CONVERSATIONS.items():
            if args.only and name not in args.only:
                continue
            if name in results and not args.rerun:
                continue
            print(name, flush=True)
            results[name] = {s: run_conversation(turns, args.mode, args.as_of, s)
                             for s in STRATEGIES}
            path.write_text(json.dumps(results, indent=1))
    if args.no_judge:
        return
    for conv in results.values():
        conv["judge"] = [judge(conv["append"], conv["rewrite"], i)
                         for i in range(1, len(conv["append"]))]
    path.write_text(json.dumps(results, indent=1))
    (out / "report.md").write_text(report(results, args.mode))
    print(f"wrote {out / 'report.md'}")


if __name__ == "__main__":
    main()
