#!/usr/bin/env python3
"""V1 experiment: run the fixed retrieval pipeline on the retrieval gold questions and score
it. Spec and results: experiments/V1_RETRIEVAL.md.

  slots → keyword (BM25) + dense → RRF → rerank → round-robin → top k   (retrieve.py)
  Recall@1, Recall@5, MRR against the gold chunks                        (score.py)

  taskset -c 16-31 python experiments/v1/v1_src/run.py
  taskset -c 16-31 python experiments/v1/v1_src/run.py --question aapl-tariffs-fy2025
  taskset -c 16-31 python experiments/v1/v1_src/run.py --config my-config.yaml
  taskset -c 16-31 python experiments/v1/v1_src/run.py --db data/v0-chunking/rule.db --label v0-rule

Settings are in config.yaml. Needs a database with vectors from the configured embedder
(`tenk embed`, or build_corpus.py for the V0 chunkings). A question whose evidence quote no
chunk contains is unscorable on that database: listed, not scored. Questions that should be
declined have no gold: they get no Recall or MRR, only the reranker's top score.
Writes output/retrieval/<label>/: config.yaml (the settings used),
results.json (per question: slots, top k, gold ranks, each search's candidates), scores.json.
"""

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from retrieve import Pipeline  # noqa: E402
from score import first_gold_rank, recall_at, reciprocal_rank  # noqa: E402

from tenk_agent.embeddings import get_embedder, get_reranker  # noqa: E402
from tenk_agent.evaluation.retrieval import gold_chunks  # noqa: E402
from tenk_agent.store import Store  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CONFIG = Path(__file__).resolve().parent / "config.yaml"


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", type=Path, default=CONFIG, help="default: %(default)s")
    ap.add_argument("--question", action="append", help="only these gold ids (default: all)")
    ap.add_argument("--db", help="override run.db, e.g. data/v0-chunking/rule.db")
    ap.add_argument("--label", help="override run.label")
    args = ap.parse_args()
    config = yaml.safe_load(args.config.read_text())
    run, settings, at = config["run"], config["retrieval"], config["score"]["recall_at"]
    run.update({k: v for k, v in (("db", args.db), ("label", args.label)) if v})
    out = ROOT / "output" / "retrieval" / run["label"]
    out.mkdir(parents=True, exist_ok=True)

    store = Store(ROOT / run["db"])
    embedder = get_embedder(settings["embedder"], settings["device"])
    if store.embedding_model() != embedder.name:
        sys.exit(f"{run['db']} vectors are {store.embedding_model()!r}, not {embedder.name!r}")
    pipeline = Pipeline(
        store,
        embedder,
        get_reranker(settings["reranker"], settings["device"]),
        candidates=settings["candidates"],
        rrf_k=settings["rrf_k"],
        slot_search=settings["slot_search"],
    )
    questions = [json.loads(line) for line in (ROOT / run["gold"]).read_text().splitlines()]
    if args.question:
        questions = [q for q in questions if q["id"] in args.question]

    results, declined, unscorable = [], [], []
    for q in questions:
        if q.get("should_abstain"):
            result = pipeline.search(q["question"], settings["k"])
            declined.append(
                {
                    "id": q["id"],
                    "kind": q.get("kind"),
                    "question": q["question"],
                    "top_score": top_score(result),
                    "retrieved": [c.id for c in result.chunks],
                }
            )
            print(f"{q['id']:<40} decline: top score {declined[-1]['top_score']:.2f}")
            continue
        gold = gold_chunks(store, q)
        if not all(gold.per_source):  # an evidence quote no chunk contains (split by chunking)
            unscorable.append(q["id"])
            print(f"{q['id']:<40} unscorable: evidence not found in one chunk")
            continue
        start = time.perf_counter()
        result = pipeline.search(q["question"], settings["k"])
        seconds = time.perf_counter() - start
        ids = [c.id for c in result.chunks]
        row = {
            "id": q["id"],
            "split": q["split"],
            "kind": q.get("kind"),
            "question": q["question"],
            "sources": len(gold.per_source),
            **{f"recall@{k}": recall_at(ids, gold, k) for k in at},
            "rr": reciprocal_rank(ids, gold),
            "first_gold_rank": first_gold_rank(ids, gold),
            "top_score": top_score(result),
            "seconds": round(seconds, 3),
            "retrieved": ids,
            "gold": [sorted(s) for s in gold.per_source],
            "searches": [asdict(s) for s in result.searches],
        }
        results.append(row)
        print(
            f"{q['id']:<40} "
            + " ".join(f"R@{k}={row[f'recall@{k}']:.2f}" for k in at)
            + f" RR={row['rr']:.2f} slots={len(result.searches)} {seconds:.1f}s"
        )

    n = len(results)
    scores = {
        "questions": n,
        "sources": sum(r["sources"] for r in results),
        **{f"recall@{k}": sum(r[f"recall@{k}"] for r in results) / n for k in at},
        "mrr": sum(r["rr"] for r in results) / n,
        "by_kind": {
            kind: {
                "questions": len(rows),
                **{f"recall@{k}": sum(r[f"recall@{k}"] for r in rows) / len(rows) for k in at},
                "mrr": sum(r["rr"] for r in rows) / len(rows),
            }
            for kind in dict.fromkeys(r["kind"] for r in results)
            for rows in [[r for r in results if r["kind"] == kind]]
        },
        "missed": [r["id"] for r in results if r["first_gold_rank"] is None],
        "unscorable": unscorable,
        "top_score_mean": {
            "answerable": sum(r["top_score"] for r in results) / n,
            "decline": sum(d["top_score"] for d in declined) / len(declined) if declined else None,
        },
        "seconds_mean": sum(r["seconds"] for r in results) / n,
    }
    print(json.dumps(scores, indent=1))
    _write(out / "results.json", results + declined)
    _write(out / "scores.json", scores)
    (out / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))


def top_score(result) -> float:
    """The reranker's best score over the question's searches: how well anything matched."""
    return max((s for search in result.searches for _, s in search.reranked[:1]), default=0.0)


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
