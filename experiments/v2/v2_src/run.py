#!/usr/bin/env python3
"""V2 experiment: answer the gold questions from V1's retrieval with one model call each, and
score the answers. Spec and results: experiments/V2_GENERATION.md.

  V1 pipeline (unchanged) ─► context: retrieved · oracle · none ─► one model call ─► claims
  correct, declined, grounded, gold cited   (score.py; text answers after judge.py)

  taskset -c 16-31 python experiments/v2/v2_src/run.py
  taskset -c 16-31 python experiments/v2/v2_src/run.py --question aapl-tariffs-fy2025
  taskset -c 16-31 python experiments/v2/v2_src/run.py --context retrieved
  taskset -c 16-31 python experiments/v2/v2_src/run.py --model openai:Qwen/Qwen3.5-9B --label 9b
  taskset -c 16-31 python experiments/v2/v2_src/run.py --dry-run --question nvda-rd-fy2025

Needs the model served (scripts/serve_vllm.sh qwen3.6-27b-fp8) and the GPU for V1's embedder
and reranker. Text answers are scored afterwards by the LLM judge: stop the model, serve the
judge, run judge.py. Writes output/generation/<label>/: config.yaml (the settings used),
results.json (per question and context: the chunks shown, answer, claims, scores, raw
replies), scores.json (per context). --dry-run writes the prompts to prompts/ instead.
"""

import argparse
import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
V1_SRC = ROOT / "experiments" / "v1" / "v1_src"
CONFIG = Path(__file__).resolve().parent / "config.yaml"
sys.path[:0] = [str(Path(__file__).resolve().parent), str(V1_SRC)]

from generate import build_prompt, generate, oracle_context  # noqa: E402
from retrieve import HybridSearch, QueryDecomposer, SubQuestionRetriever  # noqa: E402
from score import grade, summarize  # noqa: E402

from tenk_agent import gold as gold_set  # noqa: E402
from tenk_agent.embeddings import get_embedder, get_reranker  # noqa: E402
from tenk_agent.models import get_model  # noqa: E402
from tenk_agent.store import Store  # noqa: E402

CONTEXTS = ("retrieved", "oracle", "none")


def v1_score():
    """V1's score.py (gold_chunks), loaded under another name: V2 has its own score.py."""
    spec = importlib.util.spec_from_file_location("v1_score", V1_SRC / "score.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", type=Path, default=CONFIG, help="default: %(default)s")
    ap.add_argument("--question", action="append", help="only these gold ids (default: all)")
    ap.add_argument("--context", action="append", choices=CONTEXTS, help="default: run.contexts")
    ap.add_argument("--model", help="override generation.model")
    ap.add_argument("--label", help="override run.label")
    ap.add_argument("--dry-run", action="store_true", help="write the prompts, call no model")
    args = ap.parse_args()
    config = yaml.safe_load(args.config.read_text())
    run, generation = config["run"], config["generation"]
    run.update({k: v for k, v in (("label", args.label), ("contexts", args.context)) if v})
    if args.model:
        generation["model"] = args.model
    retrieval = yaml.safe_load((ROOT / config["retrieval"]).read_text())["retrieval"]
    config["retrieval"] = {"from": config["retrieval"]} | retrieval
    out = ROOT / "output" / "generation" / run["label"]
    out.mkdir(parents=True, exist_ok=True)

    store = Store(ROOT / run["db"])
    questions = [json.loads(line) for line in (ROOT / run["gold"]).read_text().splitlines()]
    if args.question:
        questions = [q for q in questions if q["id"] in args.question]
    if not config["score"]["restatements"]:
        questions = [q | {"acceptable_sources": []} for q in questions]

    # 1. Retrieve (V1) and resolve each question's gold chunks.
    pipeline = v1_pipeline(store, retrieval)
    gold_chunks = v1_score().gold_chunks
    prepared = []
    for q in questions:
        per_source = gold_chunks(store, q).per_source
        primary = gold_set.resolve_evidence(store, q)[: len(q.get("sources", []))]
        retrieved = pipeline.search(q["question"], retrieval["k"]).chunks
        contexts = {
            "retrieved": retrieved,
            "oracle": oracle_context(retrieved, per_source, primary, store, retrieval["k"]),
            "none": [],
        }
        for name in run["contexts"]:
            prepared.append((q, name, contexts[name], per_source))
        print(f"{q['id']:<40} retrieved {len(retrieved)}, oracle +{_added(contexts)}")

    prompt = ROOT / generation["prompt"]
    if args.dry_run:
        folder = out / "prompts"
        folder.mkdir(exist_ok=True)
        for q, name, chunks, _ in prepared:
            system, user = build_prompt(prompt, q["question"], chunks, store)
            (folder / f"{q['id']}.{name}.md").write_text(
                f"# System\n\n{system}\n\n# User\n\n{user}\n"
            )
        print(f"prompts in {folder}")
        return

    # 2. Generate: one call per (question, context), in parallel.
    model = get_model(
        generation["model"], base_url=generation["base_url"], thinking=generation["thinking"]
    )

    def answer(task) -> dict:
        q, name, chunks, per_source = task
        system, user = build_prompt(prompt, q["question"], chunks, store)
        result = generate(model, system, user, store, generation["max_tokens"])
        context = [c.id for c in chunks]
        row = {
            "id": q["id"],
            "context": name,
            "split": q.get("split"),
            "kind": q.get("kind"),
            "question": q["question"],
            "should_abstain": bool(q.get("should_abstain")),
            "required_points": q.get("required_points") or [],
            "chunks": context,
            **result.answer,
            **grade(q, result.answer, context, per_source),
            "calls": len(result.replies),
            "seconds": result.seconds,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "replies": result.replies,
        }
        print(f"{q['id']:<40} {name:<9} {_verdict(row)} {result.seconds:.1f}s")
        return row

    with ThreadPoolExecutor(max_workers=run["workers"]) as pool:
        rows = list(pool.map(answer, prepared))

    scores = {n: summarize([r for r in rows if r["context"] == n]) for n in run["contexts"]}
    _write(out / "results.json", rows)
    _write(out / "scores.json", scores)
    (out / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    print_scores(scores)
    unjudged = sum(len(s["unjudged"]) for s in scores.values())
    if unjudged:
        print(f"\n{unjudged} text answers unjudged: serve the judge, then run judge.py")


def v1_pipeline(store: Store, settings: dict) -> SubQuestionRetriever:
    """V1's retrieval pipeline, built from V1's settings as run.py builds it there."""
    embedder = get_embedder(settings["embedder"], settings["device"])
    if store.embedding_model() != embedder.name:
        sys.exit(f"vectors are {store.embedding_model()!r}, not {embedder.name!r}")
    decompose = settings["decompose"]
    return SubQuestionRetriever(
        QueryDecomposer(store, rewrite=decompose["rewrite"], fan_out=decompose["fan_out"]),
        HybridSearch(
            store,
            embedder,
            get_reranker(settings["reranker"], settings["device"]),
            candidates=settings["candidates"],
            rrf_k=settings["rrf_k"],
        ),
        slot_search=settings["slot_search"],
    )


def print_scores(scores: dict) -> None:
    rows = [
        ("Correct", "correct"),
        ("Declined when it should", "declined_when_should"),
        ("Declined when answerable", "declined_when_answerable"),
        ("Numbers stated", "numbers_matched"),
        ("Numbers in claims", "numbers_claimed"),
        ("Points covered", "points_covered"),
        ("Numeric claims grounded", "grounded"),
        ("Gold sources cited", "gold_cited"),
        ("Correct | all gold shown", "correct_with_all_gold"),
        ("Correct | gold missing", "correct_with_gold_missing"),
    ]
    names = list(scores)
    print("\n" + f"{'':<26}" + "".join(f"{n:>11}" for n in names))
    for label, key in rows:
        cells = [scores[n][key] for n in names]
        print(f"{label:<26}" + "".join(f"{_pct(v):>11}" for v in cells))
    print(f"{'Seconds (mean)':<26}" + "".join(f"{scores[n]['seconds_mean']:>11.1f}" for n in names))


def _added(contexts: dict) -> int:
    shown = {c.id for c in contexts["retrieved"]}
    return sum(c.id not in shown for c in contexts["oracle"])


def _verdict(row: dict) -> str:
    if row["format_error"]:
        return "format error"
    mark = {True: "right", False: "wrong", None: "unjudged"}[row["correct"]]
    n = row["numeric_claims"]
    grounded = f"grounded {row['grounded']}/{n}" if n else ""
    return f"{mark:<8} {'declined' if row['abstained'] else 'answered':<8} {grounded}"


def _pct(value) -> str:
    return "–" if value is None else f"{value:.1%}"


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
