#!/usr/bin/env python3
"""V0 experiment: preprocess a 10-K excerpt once, chunk it three ways, score each against the
ground truth. Spec and results: experiments/V0_PARSING_CHUNKING.md.

  taskset -c 16-31 python experiments/v0/run.py                  # all stages (LLM steps cached)
  taskset -c 16-31 python experiments/v0/run.py --stage preprocess
  taskset -c 16-31 python experiments/v0/run.py --stage chunk --stage score

Needs the vLLM server (scripts/serve_vllm.sh qwen3.6-27b-fp8) for preprocessing and the agentic
chunker. Writes output/chunking/<label>/: blocks.json, labels.json, document.json,
chunks-<method>.json, scores.json.
"""

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blocks import cover_metadata, read_blocks  # noqa: E402
from structure import Labeler, assemble  # noqa: E402

METHODS = ("rule", "parent_child", "agentic")

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
        "--method",
        action="append",
        choices=list(METHODS),
        help="chunk stage: only these (default: all)",
    )
    args = ap.parse_args()
    out = ROOT / "output" / "chunking" / args.label
    out.mkdir(parents=True, exist_ok=True)
    stages = args.stage or STAGES

    if "preprocess" in stages:
        preprocess(args, out)
    if "chunk" in stages:
        from chunkers import run_all  # LlamaIndex and Chonkie load only for this stage

        document = json.loads((out / "document.json").read_text())
        for method, chunks in run_all(document, MODEL, BASE_URL, args.method or METHODS).items():
            (out / f"chunks-{method}.json").write_text(
                json.dumps(chunks, indent=1, ensure_ascii=False)
            )
            print(f"{method}: {len(chunks)} chunks")
    if "score" in stages:
        from score import score_all

        gt = json.loads(args.gt.read_text())
        document = json.loads((out / "document.json").read_text())
        chunks = {
            m: json.loads((out / f"chunks-{m}.json").read_text())
            for m in ("rule", "parent_child", "agentic")
        }
        scores = score_all(args.html, gt, document, chunks)
        (out / "scores.json").write_text(json.dumps(scores, indent=1))
        print(json.dumps(scores, indent=1))


def preprocess(args, out: Path) -> None:
    blocks = read_blocks(args.html.read_text(encoding="utf-8"))
    filing = cover_metadata(args.cover)
    (out / "blocks.json").write_text(
        json.dumps([asdict(b) for b in blocks], indent=1, ensure_ascii=False)
    )
    labels_path = out / "labels.json"
    if labels_path.exists() and not args.relabel:
        labels = json.loads(labels_path.read_text())
    else:
        t = time.time()
        labeler = Labeler(MODEL, BASE_URL, filing)
        labels = labeler.label(blocks)
        labels_path.write_text(json.dumps(labels, indent=1, ensure_ascii=False))
        log = {"seconds": round(time.time() - t), "usage": labeler.usage, "windows": labeler.log}
        (out / "labels-log.json").write_text(json.dumps(log, indent=1, ensure_ascii=False))
        print(f"labeled {len(blocks)} blocks in {log['seconds']} s, {labeler.usage}")
    document = assemble(blocks, labels, filing)
    (out / "document.json").write_text(json.dumps(document, indent=1, ensure_ascii=False))
    print(
        "sections:",
        [(s["item"], len(s["blocks"])) for s in document["sections"]],
        "dropped:",
        document["dropped"],
        "review:",
        len(document["review"]),
    )


if __name__ == "__main__":
    main()
