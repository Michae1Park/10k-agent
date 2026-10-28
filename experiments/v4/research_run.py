"""V4.5: the Research agent (tool set "research") on the Research gold set.

  taskset -c 16-31 uv run python experiments/v4/research_run.py [--question ID] [--resume]
  taskset -c 16-31 uv run python experiments/v4/research_run.py --score

Needs the model served (scripts/serve_vllm.sh qwen3.6-27b-fp8) and TENK_COMPANIES=companies.yaml.
Bars, frozen 2026-10-27 (design doc §13.1):
  pass               ≥ 90%: a claim cites a model: id the question expects and verifies, the
                     answer lists its assumptions, and no numeric claim is unverified
  from_model         100%: every forward-looking numeric claim (2026Q3 on, 2026E, 2027E) cites
                     a model: id
Writes output/generation/<label>/results.json and scores.json.

--score --alternative rescores with two corrections proposed after run 1, for the user to rule
on (the frozen scoring stays the gate): a variance review may cite the bridge's own model ids
(var-<period>), and a claim citing only company guidance (guide:) is reported data, not a
computed number.
"""

import argparse
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import yaml

from tenk_agent.agent.loop import Agent
from tenk_agent.agent.tools import Toolbox
from tenk_agent.asof import AsOf
from tenk_agent.evaluation import gold
from tenk_agent.models import get_model, parse_json
from tenk_agent.research.book import Book
from tenk_agent.retrieval.search import SubQuestionRetriever
from tenk_agent.store import Store

ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(__file__).resolve().parent / "ask_config.yaml"  # the same model and budgets
GOLD = ROOT / "evaluation" / "gold" / "research-gold.jsonl"
FIRST_FORECAST = "2026Q3"  # at the gold's as-of, 2026-09-01


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--question", action="append")
    ap.add_argument("--label", default="v4.5-research-qwen3.6-27b")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--alternative", action="store_true", default=True,
                    help="the two corrections proposed after run 1, adopted by the user "
                         "2026-10-28 (the default)")
    args = ap.parse_args()
    config = yaml.safe_load(CONFIG.read_text())
    out = ROOT / "output" / "generation" / args.label
    out.mkdir(parents=True, exist_ok=True)
    questions = {q["id"]: q for q in gold.load(GOLD)}
    if args.score:
        rows = json.loads((out / "results.json").read_text())
        return report(out, [score(r, questions[r["id"]], args.alternative) for r in rows],
                      "scores-alternative.json" if args.alternative else "scores.json")

    db = ROOT / config["run"]["db"]
    retrieval = yaml.safe_load((ROOT / config["retrieval"]).read_text())["retrieval"]
    base = SubQuestionRetriever.from_settings(Store(db), retrieval | {"device": "cpu"})
    generation, agent_config = config["generation"], config["agent"]
    model = get_model(generation["model"], base_url=generation["base_url"], thinking=False)
    lock, search_lock = threading.Lock(), threading.Lock()
    books: dict[str, Book] = {}

    def agent(as_of: str) -> Agent:
        """A fresh agent per question (its own scratch runs), sharing the as-of's book of base
        models and valuations."""
        store = Store(db, AsOf.of(as_of))
        with lock:
            book = books.setdefault(as_of, Book(store))
        # shared base forecasts; own scratch runs and valuations (scratch-n is per session)
        session = Book(store, book.settings, book.forecasts, {}, {}, book.earlier)
        search = replace(base, decomposer=replace(base.decomposer, store=store),
                         hybrid=replace(base.hybrid, store=store))
        toolbox = Toolbox(store, search, None, "research", session)
        call = toolbox.call

        def locked(name, arguments):
            if name == "search_filings":
                with search_lock:
                    return call(name, arguments)
            with lock:  # model tools share the book's caches
                return call(name, arguments)

        toolbox.call = locked
        return Agent(model, toolbox, max_calls=agent_config["max_calls"],
                     max_turns=agent_config["max_turns"], max_tokens=generation["max_tokens"],
                     result_chars=agent_config["result_chars"],
                     max_context=agent_config["max_context"])

    done = json.loads((out / "results.json").read_text()) if args.resume and (
        out / "results.json").exists() else []
    finished = {r["id"] for r in done}
    todo = [q for q in questions.values() if (not args.question or q["id"] in args.question)
            and q["id"] not in finished]
    print(f"{len(todo)} questions ({len(finished)} kept)")

    def answer(q: dict) -> None:
        try:
            run = agent(q["as_of"]).run(q["question"])
        except Exception as e:  # --resume retries it
            print(f"{q['id']:<40} FAILED {type(e).__name__}: {e}"[:300])
            return
        row = {"id": q["id"], "category": q["category"], "question": q["question"],
               **run.answer, "reply": run.reply, "stop": run.stop, "seconds": run.seconds,
               "calls": len(run.tool_calls), "steps": [asdict(s) for s in run.steps]}
        row = score(row, q)
        with lock:
            done.append(row)
            (out / "results.json").write_text(json.dumps(done, indent=1, ensure_ascii=False))
        print(f"{q['id']:<40} {'pass' if row['pass'] else 'FAIL':<4} ref {row['model_ref']!s:<5} "
              f"assumptions {row['assumptions_ok']!s:<5} {row['calls']:>2} calls "
              f"{row['seconds']:.0f}s")

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(answer, todo))
    report(out, done)


def score(row: dict, q: dict, alternative: bool = False) -> dict:
    claims = row.get("claims") or []
    refs = list(q["expected_refs"])
    if alternative and q["category"] == "variance_review":
        refs.append(r"model:" + q["company"] + r":[\d-]+:var-2026Q2:\w+")
    patterns = [re.compile(p + "$") for p in refs]
    good = ("verified", "calculated")
    model_ref = any(c["status"] in good and any(p.match(i) for p in patterns
                                                for i in c["chunk_ids"]) for c in claims)
    try:
        assumptions = parse_json(row.get("reply") or "").get("assumptions") or []
    except (ValueError, AttributeError):
        assumptions = []
    assumptions_ok = bool([a for a in assumptions if isinstance(a, dict) and a.get("driver")])
    numeric = [c for c in claims if c.get("value") is not None]
    forward = [c for c in numeric if _forward(c) and not (
        alternative and all(i.startswith("guide:") for i in c["chunk_ids"]))]
    from_model = [c for c in forward if any(i.startswith("model:") for i in c["chunk_ids"])]
    unverified = [c["id"] for c in numeric if c["status"] not in good]
    return row | {
        "model_ref": model_ref, "assumptions_ok": assumptions_ok, "unverified": unverified,
        "forward_claims": len(forward), "forward_from_model": len(from_model),
        "pass": model_ref and assumptions_ok and not unverified and not row.get("abstained"),
    }


def _forward(claim: dict) -> bool:
    """A claim about a period after the last reported quarter, or citing a model run."""
    period = str(claim.get("period") or "")
    if any(i.startswith("model:") for i in claim["chunk_ids"]):
        return True
    if re.fullmatch(r"FY\d{4}", period):
        return period >= f"FY{FIRST_FORECAST[:4]}"
    return bool(re.fullmatch(r"\d{4}Q\d", period)) and period >= FIRST_FORECAST


def report(out: Path, rows: list[dict], name: str = "scores.json") -> None:
    passed = [r["pass"] for r in rows]
    forward = sum(r["forward_claims"] for r in rows)
    from_model = sum(r["forward_from_model"] for r in rows)
    by_category = {}
    for r in rows:
        by_category.setdefault(r["category"], []).append(r["pass"])
    scores = {
        "questions": len(rows), "pass": sum(passed) / len(rows) if rows else None,
        "model_ref": sum(r["model_ref"] for r in rows) / len(rows) if rows else None,
        "assumptions": sum(r["assumptions_ok"] for r in rows) / len(rows) if rows else None,
        "from_model": from_model / forward if forward else None,
        "forward_claims": forward,
        "by_category": {k: sum(v) / len(v) for k, v in by_category.items()},
        "failed": [r["id"] for r in rows if not r["pass"]],
    }
    scores["gate"] = bool(scores["pass"] is not None and scores["pass"] >= 0.9
                          and scores["from_model"] == 1.0)
    (out / name).write_text(json.dumps(scores, indent=1))
    print(json.dumps({k: v for k, v in scores.items() if k != "failed"}, indent=1))
    print("failed:", scores["failed"])


if __name__ == "__main__":
    sys.exit(main())
