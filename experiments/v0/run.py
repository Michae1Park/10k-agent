#!/usr/bin/env python3
"""V0 experiment: preprocess a 10-K excerpt once, chunk it three ways, score each against the
ground truth. Spec and results: experiments/V0_PARSING_CHUNKING.md.

  preprocess  HTML → blocks (blocks.py) → LLM labels (structure.py, cached) → document
  chunk       document → chunks, per method (chunkers.py)
  score       chunks vs the ground truth (score.py)

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
import time
from dataclasses import asdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blocks import cover_metadata, read_blocks  # noqa: E402
from chunkers import METHODS, run_all  # noqa: E402
from structure import Labeler, assemble  # noqa: E402

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
        preprocess(html, cover, out, llm, config["labeling"], args.relabel)
    if "chunk" in stages:
        chunk(out, llm, config["chunking"], args.method or METHODS)
    if "score" in stages:
        score(html, gt, out, config["chunking"])
    (out / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))


def preprocess(
    html: Path, cover: Path, out: Path, llm: dict, labeling: dict, relabel: bool
) -> None:
    """HTML → blocks → labels (cached in labels.json) → document.json."""
    blocks = read_blocks(html.read_text(encoding="utf-8"))
    filing = cover_metadata(cover)
    _write(out / "blocks.json", [asdict(b) for b in blocks])
    labels_path = out / "labels.json"
    if labels_path.exists() and not relabel:
        cached = json.loads((out / "labels-log.json").read_text()).get("labeling")
        if cached != labeling:
            sys.exit(
                f"{labels_path} was labeled with {cached}, not {labeling}: "
                "pass --relabel, or set another run.label to keep both"
            )
        labels = json.loads(labels_path.read_text())
    else:
        t = time.time()
        labeler = Labeler(llm["model"], llm["base_url"], filing, labeling)
        labels = labeler.label(blocks)
        _write(labels_path, labels)
        log = {
            "labeling": labeling,
            "seconds": round(time.time() - t),
            "usage": labeler.usage,
            "windows": labeler.log,
        }
        _write(out / "labels-log.json", log)
        print(f"labeled {len(blocks)} blocks in {log['seconds']} s, {labeler.usage}")
    document = assemble(blocks, labels, filing)
    _write(out / "document.json", document)
    print(
        "sections:",
        [(s["item"], len(s["blocks"])) for s in document["sections"]],
        "dropped:",
        document["dropped"],
        "review:",
        len(document["review"]),
    )


def chunk(out: Path, llm: dict, chunking: dict, methods) -> None:
    """document.json → chunks-<method>.json; chunks-log.json records each method's sizes."""
    document = json.loads((out / "document.json").read_text())
    log_path = out / "chunks-log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else {}
    for method, chunks in run_all(
        document, chunking, llm["model"], llm["base_url"], methods
    ).items():
        _write(out / f"chunks-{method}.json", chunks)
        log[method] = chunking
        print(f"{method}: {len(chunks)} chunks")
    _write(log_path, log)


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
