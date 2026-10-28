"""V4.1: extract every release in the gold set and score it against the labels.

  taskset -c 16-31 uv run python experiments/v4/extract_pilot.py [--model anthropic:<id>]

--releases all extracts every release in the store (V4.2: what `tenk build --stage kpis` loads);
--reuse <label> takes releases already extracted by that run instead of calling the model again.
"""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tenk_agent.asof import AsOf
from tenk_agent.evaluation.extraction import load_gold, score
from tenk_agent.facts.extract import extract, to_json
from tenk_agent.models import get_model
from tenk_agent.store import Store

DB = Path("data/v4/build/parent_child.db")
GOLD = Path("evaluation/gold/extraction-gold.jsonl")
OUT = Path("output/extraction")
FILED = {}  # (ticker, period) → filing date, from the store


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="openai:Qwen/Qwen3.6-27B-FP8")
    ap.add_argument("--label", default="pilot-qwen3.6-27b")
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--releases", choices=("gold", "all"), default="gold")
    ap.add_argument("--reuse", help="label of a run whose extractions to keep")
    ap.add_argument("--method", choices=("llm", "rules"), default="llm",
                    help="rules: the label-pattern matcher, no LLM (facts/rules.py ablation)")
    args = ap.parse_args()
    gold = load_gold(GOLD, args.tickers)
    filings = {
        (f.ticker, f"{f.fiscal_year}{f.fiscal_period}"): f.filing_date
        for f in Store(DB, AsOf.today()).filings()
        if f.form == "ER" and (not args.tickers or f.ticker in args.tickers)
    }
    releases = sorted(
        filings if args.releases == "all" else {(g["ticker"], g["period"]) for g in gold}
    )
    reused = {}
    if args.reuse:
        saved = json.loads((OUT / args.reuse / "extractions.json").read_text())
        reused = {(r["ticker"], r["period"]): r for r in saved.values()}
    model = get_model(args.model, thinking=False) if args.method == "llm" else None

    def run(release):
        ticker, period = release
        store = Store(DB, AsOf.after(filings[release]))  # sees the release, nothing later
        t = time.time()
        if args.method == "rules":
            from tenk_agent.facts.rules import extract_rules

            out = to_json(extract_rules(store, ticker, period))
        else:
            out = to_json(extract(model, store, ticker, period))
        out["seconds"] = round(time.time() - t, 1)
        return release, out

    with ThreadPoolExecutor(4) as pool:
        results = dict(pool.map(run, [r for r in releases if r not in reused]))
    results |= {r: reused[r] for r in releases if r in reused}
    results = dict(sorted(results.items()))
    folder = OUT / args.label
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "extractions.json").write_text(
        json.dumps({f"{t}-{p}": r for (t, p), r in results.items()}, indent=1)
    )
    result = score(gold, results)
    (folder / "score.json").write_text(json.dumps(result, indent=1))
    print(
        f"exact {result['exact']:.1%} critical {result['critical']:.1%} "
        f"pass {result['pass']} ({result['labels']} labels)"
    )
    for m in result["misses"]:
        print("  miss", m["id"], "expected", m["expected"], "got", m["got"])
    for key, r in results.items():
        print(
            key,
            f"{r['calls']} calls {r['input_tokens']} in {r['output_tokens']} out "
            f"{r['seconds']} s, exceptions {len(r['exceptions'])}",
        )


if __name__ == "__main__":
    main()
