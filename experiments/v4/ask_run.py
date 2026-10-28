"""V4.2: the Ask agent (tool set "ask") on the Ask gold set, each question at its own as-of.

  taskset -c 16-31 uv run python experiments/v4/ask_run.py [--question ID] [--resume]
  taskset -c 16-31 uv run python experiments/v4/ask_run.py --score    # rescore, after judge.py

Needs the model served (scripts/serve_vllm.sh qwen3.6-27b-fp8) and TENK_COMPANIES=companies.yaml.
Text answers are judged afterwards: serve the judge, run experiments/v2/judge.py --config
experiments/v4/ask_config.yaml, then --score. Writes output/generation/<label>/results.json and
scores.json: V3's scores (evaluation/scoring.py) plus the two frozen bars (design doc §13.1):
  correct_verified  correct, and every expected number stated by a verified or calculated claim
                    (text: no unverified numeric claim; declines: declined)
  cited_resolve     share of the ids cited by claims that resolve at the question's as-of
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v2"))
from run import _verdict, _write, print_scores  # noqa: E402  (experiments/v2/run.py)

from tenk_agent.agent.loop import Agent  # noqa: E402
from tenk_agent.agent.tools import Toolbox  # noqa: E402
from tenk_agent.asof import AsOf  # noqa: E402
from tenk_agent.evaluation import gold  # noqa: E402
from tenk_agent.evaluation.answer_metrics import matches  # noqa: E402
from tenk_agent.evaluation.scoring import grade, summarize  # noqa: E402
from tenk_agent.models import get_model  # noqa: E402
from tenk_agent.research.refs import Resolver  # noqa: E402
from tenk_agent.retrieval.search import SubQuestionRetriever  # noqa: E402
from tenk_agent.store import Store, period_parts  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(__file__).resolve().parent / "ask_config.yaml"
CONTEXT = "ask"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--question", action="append", help="only these gold ids")
    ap.add_argument("--label", help="override run.label")
    ap.add_argument("--resume", action="store_true", help="keep finished rows, run the rest")
    ap.add_argument("--score", action="store_true", help="rescore results.json, no model calls")
    args = ap.parse_args()
    config = yaml.safe_load(args.config.read_text())
    run = config["run"]
    run["label"] = args.label or run["label"]
    out = ROOT / "output" / "generation" / run["label"]
    out.mkdir(parents=True, exist_ok=True)
    questions = {q["id"]: q for path in run["gold"] for q in gold.load(ROOT / path)}
    db = ROOT / run["db"]

    if args.score:
        rows = json.loads((out / "results.json").read_text())
        return report(out, rows, questions, db, config)

    todo = [q for q in questions.values() if not args.question or q["id"] in args.question]
    retrieval = yaml.safe_load((ROOT / config["retrieval"]).read_text())["retrieval"]
    retrieval["device"] = config.get("device", retrieval["device"])
    base = SubQuestionRetriever.from_settings(Store(db), retrieval)
    generation, agent_config = config["generation"], config["agent"]
    model = get_model(generation["model"], base_url=generation["base_url"],
                      thinking=generation["thinking"])
    search_lock, agents = threading.Lock(), {}

    def agent_at(as_of: str) -> Agent:
        """One agent per as-of: its store, search and tools see only what was public then."""
        if as_of not in agents:
            store = Store(db, AsOf.of(as_of))
            search = replace(base, decomposer=replace(base.decomposer, store=store),
                             hybrid=replace(base.hybrid, store=store))
            toolbox = Toolbox(store, search, None, "ask")
            call = toolbox.call
            toolbox.call = lambda name, arguments: _locked(call, search_lock, name, arguments)
            agents[as_of] = Agent(model, toolbox, max_calls=agent_config["max_calls"],
                                  max_turns=agent_config["max_turns"],
                                  max_tokens=generation["max_tokens"],
                                  result_chars=agent_config["result_chars"],
                                  max_context=agent_config["max_context"])
        return agents[as_of]

    done = json.loads((out / "results.json").read_text()) if args.resume and (
        out / "results.json").exists() else []
    finished = {r["id"] for r in done}
    tasks = [q for q in todo if q["id"] not in finished]
    print(f"{len(tasks)} questions ({len(finished)} kept)")
    lock = threading.Lock()
    with lock:
        for q in tasks:
            agent_at(q["as_of"])  # build the agents before the threads share them

    def answer(q: dict) -> None:
        try:
            agent = agent_at(q["as_of"])
            result = agent.run(q["question"])
            steps = [asdict(s) for s in result.steps]
            context = seen_ids(result.steps)
            row = {
                "id": q["id"], "context": CONTEXT, "gold_set": q["gold_set"], "split": q["split"],
                "kind": q["kind"], "category": q["category"], "question": q["question"],
                "as_of": q["as_of"], "should_abstain": bool(q["should_abstain"]),
                "required_points": q["required_points"], "expected_tools": q["expected_tools"],
                "chunks": context, **result.answer,
                **grade(q, result.answer, context, gold_ids(agent.toolbox.store, q)),
                "calls": len(result.tool_calls), "stop": result.stop, "seconds": result.seconds,
                "input_tokens": sum(s.input_tokens for s in result.steps),
                "output_tokens": sum(s.output_tokens for s in result.steps), "steps": steps,
            }
        except Exception as e:  # one failed question shouldn't end the run; --resume retries it
            print(f"{q['id']:<44} FAILED {type(e).__name__}: {e}"[:300])
            return
        with lock:
            done.append(row)
            _write(out / "results.json", done)
        print(f"{q['id']:<44} {_verdict(row)} {row['calls']:>2} calls {row['seconds']:.0f}s")

    with ThreadPoolExecutor(max_workers=run["workers"]) as pool:
        list(pool.map(answer, tasks))
    (out / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    report(out, done, questions, db, config)


def _locked(call, lock, name, arguments):
    if name == "search_filings":  # the embedder and reranker are shared between workers
        with lock:
            return call(name, arguments)
    return call(name, arguments)


def seen_ids(steps) -> list[str]:
    """Every id a tool returned: chunks, data rows, and the chunks data rows came from."""
    ids = []
    for step in steps:
        result = step.result or {}
        for row in result.get("chunks", []) + result.get("rows", []):
            ids += [row[k] for k in ("chunk_id", "id", "actual_id") if row.get(k)]
    return list(dict.fromkeys(ids))


def gold_ids(store: Store, q: dict) -> list[set[str]]:
    """Each gold source's chunks: those of its company, period and form showing its evidence."""
    out = []
    for src in q.get("sources") or []:
        fy, fp = period_parts(src["period"])
        want = _norm(src["evidence"])
        out.append({c.id for c in store.chunks(src["company"], fy)
                    if c.fiscal_period == fp and c.form == src["form"] and want in _norm(c.text)})
    return out


def _norm(text: str) -> str:
    return re.sub(r"[\s​*]+", " ", text.replace("$", "").lower()).strip()


def bars(row: dict, q: dict, store: Store) -> dict:
    resolver = Resolver(store)
    cited = [i for c in row["claims"] for i in c["chunk_ids"]]
    resolved = [resolver.resolve(i) is not None for i in cited]
    good = ("verified", "calculated")
    if row["correct"] is None:
        verified = None
    elif q["should_abstain"] or not row["correct"]:
        verified = row["correct"]
    elif gold.expected_values(q):
        tolerance = q.get("tolerance", 0.005)
        verified = all(
            any(matches(v, c["value"], c.get("unit"), tolerance) and c["status"] in good
                for c in row["claims"] if c["value"] is not None)
            for v in gold.expected_values(q)
        )
    else:
        verified = all(c["status"] in good for c in row["claims"] if c["value"] is not None)
    return {"correct_verified": verified, "cited": len(cited), "cited_resolved": sum(resolved),
            "unresolved": [i for i, ok in zip(cited, resolved, strict=True) if not ok]}


def report(out: Path, rows: list[dict], questions: dict, db: Path, config: dict) -> None:
    stores = {}
    for r in rows:
        as_of = questions[r["id"]]["as_of"]
        stores.setdefault(as_of, Store(db, AsOf.of(as_of)))
        r.update(bars(r, questions[r["id"]], stores[as_of]))
    scores = summarize(rows)
    judged = [r["correct_verified"] for r in rows if r["correct_verified"] is not None]
    cited = sum(r["cited"] for r in rows)
    scores |= {
        "correct_verified": sum(judged) / len(judged) if judged else None,
        "cited_resolve": sum(r["cited_resolved"] for r in rows) / cited if cited else None,
        "unresolved": {r["id"]: r["unresolved"] for r in rows if r["unresolved"]},
        "by_category": {
            cat: sum(r["correct_verified"] for r in group) / len(group)
            for cat in dict.fromkeys(r["category"] for r in rows)
            for group in [[r for r in rows if r["category"] == cat
                           and r["correct_verified"] is not None]] if group
        },
    }
    s = config["score"]
    scores["pass"] = bool(
        scores["correct_verified"] is not None and not scores["unjudged"]
        and scores["correct_verified"] >= s["bar_correct_verified"]
        and (scores["cited_resolve"] or 0) >= s["bar_cited_resolve"]
    )
    _write(out / "results.json", rows)
    _write(out / "scores.json", {CONTEXT: scores})
    print_scores({CONTEXT: scores})
    print(f"\ncorrect and verified {_pct(scores['correct_verified'])} (bar "
          f"{s['bar_correct_verified']:.0%}) · cited ids resolve {_pct(scores['cited_resolve'])} "
          f"(bar {s['bar_cited_resolve']:.0%}) · pass {scores['pass']}"
          + (f" · {len(scores['unjudged'])} unjudged" if scores["unjudged"] else ""))
    for cat, share in scores["by_category"].items():
        print(f"  {cat:<22} {share:.0%}")


def _pct(v) -> str:
    return "–" if v is None else f"{v:.1%}"


if __name__ == "__main__":
    main()
