#!/usr/bin/env python3
"""V0 experiment: preprocess a 10-K excerpt once, chunk it three ways, score each against the
ground truth. Spec and results: experiments/V0_PARSING_CHUNKING.md.

  preprocess  HTML → blocks → LLM labels (cached) → document      tenk_agent.preprocess
  chunk       document → chunks, per method                     tenk_agent.chunking
  score       chunks vs the ground truth                        score.py

  taskset -c 16-31 python experiments/v0/run.py                  # all stages (LLM steps cached)
  taskset -c 16-31 python experiments/v0/run.py --stage preprocess
  taskset -c 16-31 python experiments/v0/run.py --stage chunk --stage score
  taskset -c 16-31 python experiments/v0/run.py --config my-config.yaml

Settings (inputs, LLM, labeling window, chunk sizes) are in config.yaml. Needs the vLLM server
(scripts/serve_vllm.sh qwen3.6-27b-fp8) for preprocessing and the agentic chunker. Writes
output/chunking/<label>/: config.yaml (the settings used), blocks.json, labels.json,
labels-log.json, document.json, chunks-<method>.json, chunks-log.json, scores.json.
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

from tenk_agent.chunking import METHODS, chunk
from tenk_agent.preprocess import StaleLabels, preprocess

ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(__file__).resolve().parent / "config.yaml"
STAGES = ["preprocess", "chunk", "score"]


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", type=Path, default=CONFIG, help="default: %(default)s")
    ap.add_argument("--stage", action="append", choices=STAGES, help="default: all")
    ap.add_argument(
        "--relabel", action="store_true", help="call the LLM again even if labels.json exists"
    )
    ap.add_argument(
        "--method", action="append", choices=METHODS, help="chunk stage: only these (default: all)"
    )
    args = ap.parse_args()
    config = yaml.safe_load(args.config.read_text())
    run, llm = config["run"], config["llm"]
    html, cover, gt = (ROOT / run[k] for k in ("html", "cover", "gt"))
    out = ROOT / "output" / "chunking" / run["label"]
    out.mkdir(parents=True, exist_ok=True)
    stages = args.stage or STAGES

    if "preprocess" in stages:
        try:
            preprocess(html, cover, out, llm, config["labeling"], args.relabel)
        except StaleLabels as e:
            sys.exit(f"{e} (--relabel, or set another run.label)")
    if "chunk" in stages:
        chunk(out, llm, config["chunking"], args.method or METHODS)
    if "score" in stages:
        score(html, gt, out, config["chunking"])
    (out / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))


def score(html: Path, gt_path: Path, out: Path, chunking: dict) -> None:
    """Every method's chunks vs the ground truth → scores.json."""
    from score import score_all  # loads the ground-truth builder

    log_path = out / "chunks-log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else {}
    if stale := [m for m in METHODS if log.get(m) != chunking]:
        sys.exit(
            f"chunks for {stale} weren't made with this config's `chunking:`: run --stage chunk"
        )
    gt = json.loads(gt_path.read_text())
    document = json.loads((out / "document.json").read_text())
    chunks = {m: json.loads((out / f"chunks-{m}.json").read_text()) for m in METHODS}
    scores = score_all(html, gt, document, chunks, chunking)
    _write(out / "scores.json", scores, ensure_ascii=True)
    print(json.dumps(scores, indent=1))


def _write(path: Path, obj, ensure_ascii: bool = False) -> None:
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=ensure_ascii))


if __name__ == "__main__":
    main()
