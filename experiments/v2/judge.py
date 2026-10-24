#!/usr/bin/env python3
"""V2 judge: score the text answers of a finished run against their required points, and
rewrite its scores. Spec: experiments/V2_GENERATION.md §4.

  taskset -c 16-31 python experiments/v2/judge.py         # run.label from config.yaml
  taskset -c 16-31 python experiments/v2/judge.py --label qwen3.5-9b --rejudge

The model under test isn't called again, so only the judge needs serving: stop the 27B, then
scripts/serve_vllm.sh gpt-oss-20b (port 8002). Numbers are never judged; they're scored by
code in run.py. A point is covered if the answer states it; paraphrase is fine.
"""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from run import CONFIG, ROOT, _write, print_scores

from tenk_agent.evaluation import gold
from tenk_agent.evaluation.judge import Judge
from tenk_agent.evaluation.scoring import correct, summarize
from tenk_agent.models import get_model


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", type=Path, default=CONFIG, help="default: %(default)s")
    ap.add_argument("--label", help="override run.label")
    ap.add_argument("--rejudge", action="store_true", help="judge answers already judged too")
    args = ap.parse_args()
    config = yaml.safe_load(args.config.read_text())
    out = ROOT / "output" / "generation" / (args.label or config["run"]["label"])
    rows = json.loads((out / "results.json").read_text())
    score = config["score"]
    judge = Judge(get_model(score["judge_model"], base_url=score["judge_base_url"]))
    if args.rejudge:  # judge against the gold's current points, not those copied into the run
        paths = config["run"]["gold"]
        points = {
            q["id"]: q["required_points"]
            for path in ([paths] if isinstance(paths, str) else paths)
            for q in gold.load(ROOT / path)
            if q.get("required_points")
        }
        for r in rows:
            r["required_points"] = points.get(r["id"], r["required_points"])

    # Declined answers are wrong whatever they say, so they aren't judged.
    todo = [
        r
        for r in rows
        if r["required_points"] and not r["abstained"] and (r["points"] is None or args.rejudge)
    ]

    def judged(row: dict) -> None:
        row["points"] = judge.points_covered(row["question"], row["required_points"], row["answer"])
        row["correct"] = correct(row, row["abstained"], row)
        print(f"{row['id']:<40} {row['context']:<9} {sum(row['points'])}/{len(row['points'])}")

    with ThreadPoolExecutor(max_workers=config["run"]["workers"]) as pool:
        list(pool.map(judged, todo))

    contexts = list(dict.fromkeys(r["context"] for r in rows))
    scores = {name: summarize([r for r in rows if r["context"] == name]) for name in contexts}
    scores["judge_model"] = score["judge_model"]
    _write(out / "results.json", rows)
    _write(out / "scores.json", scores)
    print_scores({name: scores[name] for name in contexts})


if __name__ == "__main__":
    main()
