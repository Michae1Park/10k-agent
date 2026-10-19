#!/usr/bin/env python3
"""V0 experiment: preprocess a 10-K excerpt once, chunk it three ways, score each against the
ground truth. Spec and results: experiments/V0_PARSING_CHUNKING.md.

  preprocess  HTML → blocks (blocks.py) → LLM labels (structure.py, cached) → document
  chunk       document → chunks, per method (chunkers.py)
  score       chunks vs the ground truth (score.py)

  taskset -c 16-31 python experiments/v0/run.py                  # all stages (LLM steps cached)
  taskset -c 16-31 python experiments/v0/run.py --stage preprocess
  taskset -c 16-31 python experiments/v0/run.py --stage chunk --stage score

Needs the vLLM server (scripts/serve_vllm.sh qwen3.6-27b-fp8) for preprocessing and the agentic
chunker. Writes output/chunking/<label>/: blocks.json, labels.json, labels-log.json,
document.json, chunks-<method>.json, scores.json.
"""

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blocks import cover_metadata, read_blocks  # noqa: E402
from chunkers import METHODS, run_all  # noqa: E402
from structure import Labeler, assemble  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TESTDATA = ROOT / "experiments" / "V0_testdata"
MODEL = "Qwen/Qwen3.6-27B-FP8"
BASE_URL = "http://localhost:8001/v1"
STAGES = ["preprocess", "chunk", "score"]


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--html", type=Path, default=TESTDATA / "aapl-fy25-excerpt.html")
    ap.add_argument(
        "--cover",
        type=Path,
        default=ROOT / "data/raw/filings/AAPL/FY2025/aapl-20250927.htm",
        help="full filing for the dei: cover tags (the excerpt has no cover page)",
    )
    ap.add_argument("--gt", type=Path, default=TESTDATA / "aapl-fy25-excerpt.gt.json")
    ap.add_argument("--label", default="aapl-fy25-excerpt")
    ap.add_argument("--stage", action="append", choices=STAGES, help="default: all")
    ap.add_argument(
        "--relabel", action="store_true", help="call the LLM again even if labels.json exists"
    )
    ap.add_argument(
        "--method", action="append", choices=METHODS, help="chunk stage: only these (default: all)"
    )
    args = ap.parse_args()
    out = ROOT / "output" / "chunking" / args.label
    out.mkdir(parents=True, exist_ok=True)
    stages = args.stage or STAGES

    if "preprocess" in stages:
        preprocess(args.html, args.cover, out, args.relabel)
    if "chunk" in stages:
        chunk(out, args.method or METHODS)
    if "score" in stages:
        score(args.html, args.gt, out)


def preprocess(html: Path, cover: Path, out: Path, relabel: bool) -> None:
    """HTML → blocks → labels (cached in labels.json) → document.json."""
    blocks = read_blocks(html.read_text(encoding="utf-8"))
    filing = cover_metadata(cover)
    _write(out / "blocks.json", [asdict(b) for b in blocks])
    labels_path = out / "labels.json"
    if labels_path.exists() and not relabel:
        labels = json.loads(labels_path.read_text())
    else:
        t = time.time()
        labeler = Labeler(MODEL, BASE_URL, filing)
        labels = labeler.label(blocks)
        _write(labels_path, labels)
        log = {"seconds": round(time.time() - t), "usage": labeler.usage, "windows": labeler.log}
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


def chunk(out: Path, methods) -> None:
    """document.json → chunks-<method>.json."""
    document = json.loads((out / "document.json").read_text())
    for method, chunks in run_all(document, MODEL, BASE_URL, methods).items():
        _write(out / f"chunks-{method}.json", chunks)
        print(f"{method}: {len(chunks)} chunks")


def score(html: Path, gt_path: Path, out: Path) -> None:
    """Every method's chunks vs the ground truth → scores.json."""
    from score import score_all  # loads the ground-truth builder

    gt = json.loads(gt_path.read_text())
    document = json.loads((out / "document.json").read_text())
    chunks = {m: json.loads((out / f"chunks-{m}.json").read_text()) for m in METHODS}
    scores = score_all(html, gt, document, chunks)
    _write(out / "scores.json", scores, ensure_ascii=True)
    print(json.dumps(scores, indent=1))


def _write(path: Path, obj, ensure_ascii: bool = False) -> None:
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=ensure_ascii))


if __name__ == "__main__":
    main()
