#!/usr/bin/env python3
"""Build the V1 corpora: every filing through V0's preprocessing and its three chunkers, then
one database per method, embedded. Spec: experiments/V1_RETRIEVAL.md §2.

  chunk  HTML → document → chunks-<method>.json per filing (needs vLLM; filings run in
         parallel, windows within a filing in order). Cached: labels.json and each method's
         chunks are reused when their settings match
  load   chunks → <out>/<method>.db; parent-child adds children
  embed  vectors for every chunk (and child) with the V1 embedder. Stop vLLM first
  pages  each chunk's pages, written into an already built <method>.db in place (no reload,
         no re-embedding); load writes them too

  taskset -c 16-31 python experiments/v1/build_corpus.py --stage chunk --jobs 8
  taskset -c 16-31 python experiments/v1/build_corpus.py --stage load --stage embed
  taskset -c 16-31 python experiments/v1/build_corpus.py --stage chunk --filing AAPL/FY2025
  taskset -c 16-31 python experiments/v1/build_corpus.py --stage pages

The same build as `tenk build` (tenk_agent.build), with V0's settings (experiments/v0/config.yaml:
llm, labeling, chunking) and V1's (config.yaml: retrieval). Filings: data/raw/filings/<TICKER>/
FY<year>/. Writes <out>/<TICKER>-FY<year>/ (V0's blocks, labels, document, chunks-<method>.json,
chunk.log) and <out>/<method>.db.
"""

import argparse
from pathlib import Path

import yaml

from tenk_agent.build import STAGES, build, filing_dirs
from tenk_agent.chunking import METHODS

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "v0-chunking"
V0_CONFIG = ROOT / "experiments" / "v0" / "config.yaml"
V1_CONFIG = Path(__file__).resolve().parent / "config.yaml"


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--stage", action="append", choices=STAGES, help="default: chunk, load, embed")
    ap.add_argument("--filing", action="append", help="TICKER/FYyear (default: all)")
    ap.add_argument("--method", action="append", choices=METHODS, help="default: all")
    ap.add_argument("--jobs", type=int, default=8, help="chunk: filings at once")
    ap.add_argument("--out", type=Path, default=OUT, help="default: %(default)s")
    args = ap.parse_args()
    v0 = yaml.safe_load(V0_CONFIG.read_text())
    settings = {k: v0[k] for k in ("llm", "labeling", "chunking")}
    settings["retrieval"] = yaml.safe_load(V1_CONFIG.read_text())["retrieval"]
    build(
        filing_dirs(ROOT / "data", args.filing),
        args.out,
        settings,
        args.method or list(METHODS),
        args.stage or list(STAGES[:3]),
        args.jobs,
    )


if __name__ == "__main__":
    main()
