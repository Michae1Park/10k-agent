#!/usr/bin/env python3
"""Build the V1 corpora: every filing through V0's preprocessing and its three chunkers, then
one database per method, embedded. Spec: experiments/V1_RETRIEVAL.md §5.

  chunk  HTML → document → chunks-<method>.json per filing (V0 run.py; needs vLLM; filings
         run in parallel, windows within a filing in order). Cached: labels.json and each
         method's chunks are reused when their settings match
  load   chunks → <out>/<method>.db in the production schema; parent-child adds children
  embed  vectors for every chunk (and child) with the V1 embedder. Stop vLLM first
  pages  each chunk's pages, written into an already built <method>.db in place (no reload,
         no re-embedding); load writes them too

  taskset -c 16-31 python experiments/v1/v1_src/build_corpus.py --stage chunk --jobs 8
  taskset -c 16-31 python experiments/v1/v1_src/build_corpus.py --stage load --stage embed
  taskset -c 16-31 python experiments/v1/v1_src/build_corpus.py --stage chunk --filing AAPL/FY2025
  taskset -c 16-31 python experiments/v1/v1_src/build_corpus.py --stage pages

Filings: data/raw/filings/<TICKER>/FY<year>/. Writes <out>/<TICKER>-FY<year>/ (V0's
blocks, labels, document, chunks-<method>.json, chunk.log) and <out>/<method>.db.
"""

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
V0 = ROOT / "experiments" / "v0" / "v0_src"
sys.path.insert(0, str(V0))

from chunkers import METHODS  # noqa: E402

from tenk_agent.corpus import COMPANIES  # noqa: E402
from tenk_agent.embeddings import get_embedder  # noqa: E402
from tenk_agent.retrieval import embed_corpus  # noqa: E402
from tenk_agent.store import CHILD_SCHEMA, Store  # noqa: E402

FILINGS = ROOT / "data" / "raw" / "filings"
OUT = ROOT / "data" / "v0-chunking"
V0_CONFIG = V0 / "config.yaml"
V1_CONFIG = Path(__file__).resolve().parent / "config.yaml"
STAGES = ["chunk", "load", "embed", "pages"]
NAMES = {c.ticker: c for c in COMPANIES}


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--stage", action="append", choices=STAGES, help="default: chunk, load, embed")
    ap.add_argument("--filing", action="append", help="TICKER/FYyear (default: all)")
    ap.add_argument("--method", action="append", choices=METHODS, help="default: all")
    ap.add_argument("--jobs", type=int, default=8, help="chunk: filings at once")
    ap.add_argument("--out", type=Path, default=OUT, help="default: %(default)s")
    ap.add_argument("--one", action="store_true", help=argparse.SUPPRESS)  # worker: one filing
    args = ap.parse_args()
    filings = [FILINGS / f for f in args.filing] if args.filing else sorted(FILINGS.glob("*/FY*"))
    methods = args.method or list(METHODS)
    stages = args.stage or STAGES[:3]

    if args.one:
        chunk_one(filings[0], args.out / label(filings[0]), methods)
        return
    if "chunk" in stages:
        chunk_all(filings, methods, args.jobs, args.out)
    if "load" in stages:
        for method in methods:
            load(method, filings, args.out)
    if "embed" in stages:
        settings = yaml.safe_load(V1_CONFIG.read_text())["retrieval"]
        embedder = get_embedder(settings["embedder"], settings["device"])
        for method in methods:
            store = Store(args.out / f"{method}.db")
            t = time.time()
            n = embed_corpus(store, embedder)
            print(f"{method}: embedded {n} in {time.time() - t:.0f} s")
    if "pages" in stages:
        for method in methods:
            add_pages(method, filings, args.out)


def label(filing: Path) -> str:
    return f"{filing.parent.name}-{filing.name}"


# --- chunk ---------------------------------------------------------------------------------


def chunk_all(filings: list[Path], methods: list[str], jobs: int, out: Path) -> None:
    """One worker process per filing (its own log), `jobs` at a time; vLLM batches them."""

    def work(filing: Path) -> str:
        target = out / label(filing)
        target.mkdir(parents=True, exist_ok=True)
        t = time.time()
        with open(target / "chunk.log", "a") as log:
            cmd = [sys.executable, __file__, "--one", "--out", str(out)]
            cmd += ["--filing", f"{filing.parent.name}/{filing.name}"]
            cmd += [a for m in methods for a in ("--method", m)]
            code = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT).returncode
        status = "ok" if code == 0 else f"FAILED ({code}), see {target / 'chunk.log'}"
        line = f"{label(filing)}: {status} in {(time.time() - t) / 60:.1f} min"
        print(line, flush=True)
        return line

    with ThreadPoolExecutor(jobs) as pool:
        results = list(pool.map(work, filings))
    if failed := [r for r in results if "FAILED" in r]:
        sys.exit(f"{len(failed)} filings failed")


def chunk_one(filing: Path, out: Path, methods: list[str]) -> None:
    """V0's preprocess and chunk stages on one full filing (it is its own cover page)."""
    import run as v0  # experiments/v0/v0_src/run.py

    config = yaml.safe_load(V0_CONFIG.read_text())
    html = next(p for p in filing.glob("*.htm"))
    out.mkdir(parents=True, exist_ok=True)
    if not (out / "document.json").exists():
        v0.preprocess(html, html, out, config["llm"], config["labeling"], relabel=False)
    log_path = out / "chunks-log.json"
    done = json.loads(log_path.read_text()) if log_path.exists() else {}
    todo = [
        m
        for m in methods
        if done.get(m) != config["chunking"] or not (out / f"chunks-{m}.json").exists()
    ]
    if todo:
        v0.chunk(out, config["llm"], config["chunking"], todo)


# --- load ----------------------------------------------------------------------------------


def load(method: str, filings: list[Path], out: Path) -> None:
    """<out>/<method>.db, rebuilt from scratch. Ticker and fiscal year come from the corpus
    folder (as the gold set names them), not the cover tags, so ids match the production db."""
    path = out / f"{method}.db"
    path.unlink(missing_ok=True)
    store = Store(path)
    db = store.db
    if method == "parent_child":
        db.executescript(CHILD_SCHEMA)
    counts = {"chunks": 0, "children": 0}
    with db:
        for filing in filings:
            meta = json.loads((filing / "metadata.json").read_text())
            ticker, year = meta["ticker"], meta["fiscal_year"]
            source = out / label(filing)
            document = json.loads((source / "document.json").read_text())
            chunks = json.loads((source / f"chunks-{method}.json").read_text())
            company = NAMES[ticker]
            prefix = f"{ticker}-FY{year}"
            db.execute(
                "INSERT OR REPLACE INTO companies VALUES (?, ?, ?, ?)",
                (ticker, company.cik, company.name, json.dumps(list(company.aliases))),
            )
            db.execute(
                "INSERT INTO filings VALUES (?, ?, ?, ?, ?, ?)",
                (
                    meta["accession_no"],
                    ticker,
                    year,
                    meta["period_end_date"],
                    meta["filing_date"],
                    meta["source_url"],
                ),
            )
            items = {}
            for s in document["sections"]:
                items.setdefault(s["item"], s["title"])
            db.executemany(
                "INSERT INTO sections VALUES (?, ?, ?, ?)",
                [(f"{prefix}-{i}", meta["accession_no"], i, t) for i, t in items.items()],
            )
            for (chunk_id, n), c in zip(chunk_ids(chunks, prefix), chunks):
                db.execute(
                    "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk_id,
                        f"{prefix}-{c['item']}",
                        ticker,
                        year,
                        c["item"],
                        n,
                        c["kind"],
                        c["text"],
                        c.get("units"),
                        json.dumps(c.get("years_covered") or []),
                        json.dumps(c["pages"]) if c.get("pages") else None,
                        json.dumps(c["printed_pages"]) if c.get("printed_pages") else None,
                    ),
                )
                counts["chunks"] += 1
                for j, child in enumerate(c.get("children") or []):
                    db.execute(
                        "INSERT INTO children VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            f"{chunk_id}-c{j:02d}",
                            chunk_id,
                            ticker,
                            year,
                            c["item"],
                            child["kind"],
                            child_text(child),
                            None,
                        ),
                    )
                    counts["children"] += 1
    store.rebuild_search_index()
    print(f"{method}: {len(filings)} filings, {counts} → {path}")


def add_pages(method: str, filings: list[Path], out: Path) -> None:
    """Each chunk's pages and printed pages (V0 D8, A5) from chunks-<method>.json into the
    built <method>.db, matched by id; a chunk whose text differs from the db's is skipped."""
    store = Store(out / f"{method}.db")  # adds the page columns to an older db
    db = store.db
    loaded = {(f.ticker, f.fiscal_year) for f in store.filings()}
    counts = {"updated": 0, "mismatched": 0}
    with db:
        for filing in filings:
            meta = json.loads((filing / "metadata.json").read_text())
            if (meta["ticker"], meta["fiscal_year"]) not in loaded:
                continue
            prefix = f"{meta['ticker']}-FY{meta['fiscal_year']}"
            chunks = json.loads((out / label(filing) / f"chunks-{method}.json").read_text())
            for (chunk_id, _), c in zip(chunk_ids(chunks, prefix), chunks):
                updated = db.execute(
                    "UPDATE chunks SET pages = ?, printed_pages = ? WHERE id = ? AND text = ?",
                    (
                        json.dumps(c["pages"]) if c.get("pages") else None,
                        json.dumps(c["printed_pages"]) if c.get("printed_pages") else None,
                        chunk_id,
                        c["text"],
                    ),
                ).rowcount
                counts["updated" if updated else "mismatched"] += 1
    print(f"{method}: pages for {counts} → {out / f'{method}.db'}")


def chunk_ids(chunks: list[dict], prefix: str) -> list[tuple[str, int]]:
    """Each chunk's id and its position within its Item: <prefix>-<item>-<nnn>."""
    seq: dict[str, int] = {}
    ids = []
    for c in chunks:
        n = seq[c["item"]] = seq.get(c["item"], -1) + 1
        ids.append((f"{prefix}-{c['item']}-{n:03d}", n))
    return ids


def child_text(child: dict) -> str:
    """V0 §3.2: most children's text has no heading, so the path is put in front of it (for
    the embedder, BM25 and the reranker alike) unless the text already starts with it."""
    path = child.get("heading_path") or []
    if not path or child["text"].startswith(path[-1]):
        return child["text"]
    return " › ".join(path) + "\n" + child["text"]


if __name__ == "__main__":
    main()
