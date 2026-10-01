#!/usr/bin/env python3
"""Stage 2 playground: question -> slots -> candidates (dense / keyword) -> rerank -> top k.
Guide: docs/STAGE2_RETRIEVE.md. Needs `tenk embed` once; local models run on the GPU (or --device cpu).

  python playground/retrieve.py "How much did Apple spend on R&D in fiscal 2024?"
  python playground/retrieve.py --gold aapl-msft-rd-fy2023-2025          # a gold question; gold chunks marked
  python playground/retrieve.py --gold aapl-msft-rd-fy2023-2025 --no-slot-search --tag noslots
  python playground/retrieve.py "Tesla competition" --mode hybrid --reranker none --k 5
  python playground/retrieve.py "net sales by category" --tickers AAPL --years 2025 --items 7

Writes output/retrieve/<tag>.md: every search with its candidates and scores, and the final top k.
"""

import argparse
import time

from _common import preview, settings, table, write_report

from tenk_agent import gold
from tenk_agent.evaluation.retrieval import gold_chunks, recall_at_k, reciprocal_rank
from tenk_agent.retrieval import Retriever, companies_in, years_in
from tenk_agent.store import Store


def main():
    s = settings()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("question", nargs="?")
    p.add_argument("--gold", help="use a gold question by id and mark its gold chunks")
    p.add_argument("--k", type=int, default=s.ask_k)
    p.add_argument("--mode", default=s.retrieval, choices=("keyword", "dense", "hybrid"))
    p.add_argument("--reranker", default=s.reranker, help="model spec, or none")
    p.add_argument("--candidates", type=int, default=s.candidates)
    p.add_argument("--no-slot-search", action="store_true")
    p.add_argument("--tickers", nargs="*")
    p.add_argument("--years", nargs="*", type=int)
    p.add_argument("--items", nargs="*")
    p.add_argument("--device", default=s.device, help="cpu to keep local models off the GPU")
    p.add_argument("--tag", default="play")
    a = p.parse_args()

    q = next((g for g in gold.load() if g["id"] == a.gold), None) if a.gold else None
    question = q["question"] if q else a.question
    if not question:
        p.error("give a question or --gold ID")

    s.device = a.device
    store = Store(s.db_path)
    retriever = Retriever.from_settings(
        store,
        s,
        mode=a.mode,
        reranker=a.reranker,
        candidates=a.candidates,
        slot_search=not a.no_slot_search,
    )
    gold_set = gold_chunks(store, q) if q else None
    gold_ids = gold_set.all if gold_set else set()

    # 1. what the question names -> slots (one search each)
    print(f"Q: {question}")
    print(f"retriever: {retriever.name}")
    print(f"companies: {companies_in(question)} · years: {years_in(question)}")
    if not (a.tickers or a.years) and retriever.slot_search:
        print(f"slots: {retriever.slots(question)}")

    # 2. search (timed), recording each search's rankings
    explain: list[dict] = []
    t0 = time.perf_counter()
    hits = retriever.search(question, a.tickers, a.years, a.items, k=a.k, explain=explain)
    elapsed = time.perf_counter() - t0
    print(f"{len(explain)} search(es) · {elapsed:.2f} s\n")

    # 3. report: each search, then the merged top k
    sections = []
    for i, e in enumerate(explain, 1):
        f = e["filters"]
        name = f"search {i}: tickers={f['tickers']} years={f['fiscal_years']} items={f['items']}"
        rows = [
            [
                rank,
                c.id + (" ★" if c.id in gold_ids else ""),
                c.kind,
                *(e["ranks"][m].get(c.id, "–") for m in e["ranks"]),
                f"{score:.3f}" if score is not None else "",
                preview(c.text, 90),
            ]
            for rank, (c, score) in enumerate(e["results"], 1)
        ]
        headers = ["#", "Chunk", "Kind", *(f"{m} rank" for m in e["ranks"]), "Rerank", "Text"]
        sections.append(f"### {name}\n\n" + table(headers, rows))

    final = table(
        ["#", "Chunk", "Kind", "Gold", "Text"],
        [
            [i, c.id, c.kind, "★" if c.id in gold_ids else "", preview(c.text, 110)]
            for i, c in enumerate(hits, 1)
        ],
    )
    print(final)
    metrics = ""
    if gold_set:
        ids = [c.id for c in hits]
        metrics = (
            f"gold sources {len(gold_set.per_source)} · Recall@5 {recall_at_k(ids, gold_set, 5):.2f} · "
            f"Recall@{a.k} {recall_at_k(ids, gold_set, a.k):.2f} · MRR {reciprocal_rank(ids, gold_set):.2f}"
        )
        print("\n" + metrics)
        missing = [sorted(src)[:2] for src in gold_set.per_source if not src & set(ids)]
        if missing:
            print(f"gold chunks not retrieved (per source): {missing}")

    write_report(
        "retrieve",
        a.tag,
        f"# Retrieve\n\nQ: {question}\n\nretriever: `{retriever.name}` · {elapsed:.2f} s\n\n"
        f"## Top {a.k}\n\n{final}\n\n{metrics}\n\n## Each search\n\n"
        + "\n\n".join(sections)
        + "\n",
    )


if __name__ == "__main__":
    main()
