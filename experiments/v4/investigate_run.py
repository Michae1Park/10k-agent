"""Investigate gold set (evaluation/gold/investigate-gold.jsonl): runs and scoring.

  taskset -c 16-31 uv run python experiments/v4/investigate_run.py --baseline [--question ID]
  taskset -c 16-31 uv run python experiments/v4/investigate_run.py --baseline --score

--baseline answers each question with one plain Research run (tool set "research"), the
baseline Investigate must beat (spec §5, design doc §12.2). Investigate itself is not built yet.
Needs the model served (scripts/serve_vllm.sh qwen3.6-27b-fp8) and TENK_COMPANIES=companies.yaml.

Automatic score, per question:
  ask part       every expected value stated by a verified or calculated claim (a guidance
                 midpoint: or both ends of its range)
                 (pick "cheapest": only the company the run's compare_peers ranked cheapest)
  research part  every expected model: id pattern cited by a verified, calculated or (a
                 rating) cited claim; "any": one is enough (naming the cheapest)
  parts_ok       every part passes;  unverified: numeric claims not verified or calculated
  auto_pass      parts_ok and no unverified numeric claim
required_points and expected_gaps are judged by hand: review.md lists them beside each answer.
Strain, to see whether a single run runs out of room: calls, stop (budget/turns), peak input
tokens of a model turn against the context cap.
Writes output/generation/<label>/results.json, scores.json and review.md.
"""

import argparse
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_run  # noqa: E402  (its agent factory: same model, budgets and tool set)

from tenk_agent.evaluation.answer_metrics import matches  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "evaluation" / "gold" / "investigate-gold.jsonl"
GOOD = ("verified", "calculated")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", action="store_true", required=True)
    ap.add_argument("--question", action="append")
    ap.add_argument("--label", default="v4-investigate-baseline-qwen3.6-27b")
    ap.add_argument("--score", action="store_true")
    args = ap.parse_args()
    config = yaml.safe_load(research_run.CONFIG.read_text())
    out = ROOT / "output" / "generation" / args.label
    out.mkdir(parents=True, exist_ok=True)
    questions = {q["id"]: q for q in map(json.loads, GOLD.read_text().splitlines())}
    if args.score:
        rows = json.loads((out / "results.json").read_text())
        return report(out, [score(r, questions[r["id"]], config) for r in rows], questions)

    agent = research_run.agent_factory(config)
    rows = []
    for q in questions.values():
        if args.question and q["id"] not in args.question:
            continue
        try:
            run = agent(q["as_of"]).run(q["question"])
        except Exception as e:
            print(f"{q['id']:<32} FAILED {type(e).__name__}: {e}"[:300])
            continue
        row = {"id": q["id"], "category": q["category"], "question": q["question"],
               **run.answer, "reply": run.reply, "stop": run.stop, "seconds": run.seconds,
               "calls": len(run.tool_calls), "steps": [asdict(s) for s in run.steps]}
        row = score(row, q, config)
        rows.append(row)
        (out / "results.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
        print(f"{q['id']:<32} {'pass' if row['auto_pass'] else 'FAIL':<4} parts "
              f"{row['parts_passed']}/{len(row['parts'])} unverified {len(row['unverified'])} "
              f"{row['calls']:>2} calls stop {row['stop']:<8} peak {row['peak_tokens']} tok "
              f"{row['seconds']:.0f}s")
    report(out, rows, questions)


def score(row: dict, q: dict, config: dict) -> dict:
    claims = row.get("claims") or []
    numeric = [c for c in claims if c.get("value") is not None and c["status"] in GOOD]
    parts = []
    for part in q["parts"]:
        if part["mode"] == "ask":
            def found(v: dict) -> bool:
                return any(matches(v, c["value"], c.get("unit"), q["tolerance"])
                           for c in numeric)

            missing = [v.get("label") or v.get("id") for v in _expected(part, row)
                       if not found(v) and not (  # a guidance midpoint: or the range itself
                           "low" in v and found(v | {"value": v["low"]})
                           and found(v | {"value": v["high"]}))]
        else:
            # a rating is text: its claim is "cited", not verified
            ids = [i for c in claims if c["status"] in GOOD + ("cited",) for i in c["chunk_ids"]]
            missing = [p for p in part["expected_refs"]
                       if not any(re.match(p + "$", i) for i in ids)]
            if part.get("any") and len(missing) < len(part["expected_refs"]):
                missing = []
        parts.append({"mode": part["mode"], "ok": not missing, "missing": missing})
    unverified = [c["id"] for c in claims
                  if c.get("value") is not None and c["status"] not in GOOD]
    peak = max((s["input_tokens"] for s in row["steps"] if s["kind"] == "model"), default=0)
    parts_ok = all(p["ok"] for p in parts)
    return row | {
        "parts": parts, "parts_passed": sum(p["ok"] for p in parts), "parts_ok": parts_ok,
        "unverified": unverified, "auto_pass": parts_ok and not unverified
        and not row.get("abstained"),
        "peak_tokens": peak, "context_cap": config["agent"]["max_context"],
        "budget_hit": row["stop"] != "answered",
    }


def _expected(part: dict, row: dict) -> list[dict]:
    """The values to find; a guide row (low, high) is both ends. pick "cheapest": only the
    company ranked cheapest by the run's own compare_peers call (none: every value)."""
    values = []
    for v in part["expected_answer"]:
        if "value" in v:
            values.append(v)
        else:
            values += [v | {"value": v["low"], "label": f"{v['id']} low"},
                       v | {"value": v["high"], "label": f"{v['id']} high"}]
    if part.get("pick") == "cheapest":
        ranked = [r for s in row["steps"] if s["kind"] == "tool" and s["name"] == "compare_peers"
                  for r in (s.get("result") or {}).get("rows", [])
                  if isinstance(r.get("value"), (int, float))]
        if ranked:
            cheapest = min(ranked, key=lambda r: r["value"])["company"]
            values = [v for v in values if v["company"] == cheapest]
    return values


def report(out: Path, rows: list[dict], questions: dict) -> None:
    n = len(rows) or 1
    scores = {
        "questions": len(rows),
        "auto_pass": sum(r["auto_pass"] for r in rows) / n,
        "parts_ok": sum(r["parts_ok"] for r in rows) / n,
        "parts": f"{sum(r['parts_passed'] for r in rows)}/{sum(len(r['parts']) for r in rows)}",
        "with_unverified": sum(bool(r["unverified"]) for r in rows),
        "budget_hit": sum(r["budget_hit"] for r in rows),
        "calls_mean": sum(r["calls"] for r in rows) / n,
        "calls_max": max((r["calls"] for r in rows), default=0),
        "peak_tokens_max": max((r["peak_tokens"] for r in rows), default=0),
        "context_cap": rows[0]["context_cap"] if rows else None,
        "failed": [r["id"] for r in rows if not r["auto_pass"]],
    }
    (out / "scores.json").write_text(json.dumps(scores, indent=1))
    lines = ["# Investigate baseline: hand review\n",
             "Mark each point and gap ✓ or ✗. Automatic parts are already scored.\n"]
    for r in rows:
        q = questions[r["id"]]
        lines += [f"\n## {r['id']}  ({'pass' if r['auto_pass'] else 'FAIL'} auto)\n",
                  f"**Q:** {q['question']}\n",
                  f"**A:** {r.get('answer') or '(no answer)'}\n",
                  "**Parts:** " + "; ".join(
                      f"{p['mode']} {'ok' if p['ok'] else 'missing ' + ', '.join(p['missing'])}"
                      for p in r["parts"]) + "\n",
                  f"**Strain:** {r['calls']} calls, stop {r['stop']}, peak {r['peak_tokens']} "
                  f"of {r['context_cap']} tokens\n"]
        lines += [f"- [ ] point: {p}" for p in q["required_points"]]
        lines += [f"- [ ] gap: {g}" for g in q["expected_gaps"]]
    (out / "review.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(scores, indent=1))


if __name__ == "__main__":
    sys.exit(main())
