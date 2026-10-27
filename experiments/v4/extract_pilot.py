"""V4.1: extract every release in the gold set and score it against the labels.

  taskset -c 16-31 uv run python experiments/v4/extract_pilot.py [--model anthropic:<id>]
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
    args = ap.parse_args()
    gold = load_gold(GOLD, args.tickers)
    releases = sorted({(g["ticker"], g["period"]) for g in gold})
    filings = {
        (f.ticker, f"{f.fiscal_year}{f.fiscal_period}"): f.filing_date
        for f in Store(DB, AsOf.today()).filings()
        if f.form == "ER"
    }
    model = get_model(args.model, thinking=False)  # as V3: thinking spends the whole budget

    def run(release):
        ticker, period = release
        store = Store(DB, AsOf.after(filings[release]))  # sees the release, nothing later
        t = time.time()
        out = to_json(extract(model, store, ticker, period))
        out["seconds"] = round(time.time() - t, 1)
        return release, out

    with ThreadPoolExecutor(4) as pool:
        results = dict(pool.map(run, releases))
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
