"""Build the corpus: every filing through V0 (preprocess → chunk), then into V1's database.

  chunk  HTML → document → chunks-<method>.json per filing (needs the LLM served). Filings run
         in parallel processes, one log each (vLLM batches their calls); windows within a
         filing in order. Cached: labels are reused, and each method's chunks while its
         settings match
  load   chunks → <out>/<method>.db, rebuilt from scratch; parent-child adds children
  embed  vectors for every chunk (and child). Stop vLLM first, or set retrieval.device: cpu
  pages  each chunk's pages, written into an already built <method>.db in place

Filings are read from <data_dir>/raw/filings/<TICKER>/FY<year>/ (tenk fetch); each one's work
files (blocks, labels, document, chunks-<method>.json, chunk.log) go to <out>/<TICKER>-FY<year>/.
Used by `tenk build` and experiments/v1/build_corpus.py. Guide: docs/V0_PARSING_CHUNKING.md.
"""

import json
import multiprocessing
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tenk_agent.chunking import chunk, is_current
from tenk_agent.preprocess import preprocess
from tenk_agent.retrieval.embeddings import get_embedder
from tenk_agent.retrieval.index import add_pages, embed_corpus, load
from tenk_agent.store import Store

STAGES = ("chunk", "load", "embed", "pages")


def filing_dirs(data_dir: Path, names: list[str] | None = None) -> list[Path]:
    """The filings' folders: `names` as TICKER/FYyear, or every one downloaded."""
    root = data_dir / "raw" / "filings"
    return [root / n for n in names] if names else sorted(root.glob("*/FY*"))


def filing_name(filing: Path) -> str:
    """AAPL/FY2025 → AAPL-FY2025, the filing's work folder under <out>."""
    return f"{filing.parent.name}-{filing.name}"


# --- chunk ---------------------------------------------------------------------------------


def chunk_all(filings: list[Path], out: Path, settings: dict, methods, jobs: int) -> list[str]:
    """chunk_one per filing, `jobs` at a time, each logging to <out>/<filing>/chunk.log.
    `settings`: the config's llm, labeling and chunking sections. Returns the failed filings."""
    failed = []
    context = multiprocessing.get_context("spawn")  # a fresh process: chunking sizes are global
    with ProcessPoolExecutor(jobs, mp_context=context) as pool:
        work = {f: pool.submit(_chunk_logged, f, out, settings, list(methods)) for f in filings}
        for filing, future in work.items():
            ok, minutes = future.result()
            status = "ok" if ok else f"FAILED, see {out / filing_name(filing) / 'chunk.log'}"
            print(f"{filing_name(filing)}: {status} in {minutes:.1f} min", flush=True)
            if not ok:
                failed.append(filing_name(filing))
    return failed


def _chunk_logged(filing: Path, out: Path, settings: dict, methods: list[str]):
    target = out / filing_name(filing)
    target.mkdir(parents=True, exist_ok=True)
    t = time.time()
    with open(target / "chunk.log", "a") as log, redirect_stdout(log), redirect_stderr(log):
        try:
            chunk_one(filing, target, settings, methods)
            ok = True
        except Exception:
            traceback.print_exc()
            ok = False
    return ok, (time.time() - t) / 60


def chunk_one(filing: Path, target: Path, settings: dict, methods) -> None:
    """V0's preprocessing and chunking on one full filing (it is its own cover page), into
    `target`. A document already there is reused; so are chunks made with these settings."""
    html = next(filing.glob("*.htm"))
    if not (target / "document.json").exists():
        preprocess(html, html, target, settings["llm"], settings["labeling"])
    todo = [m for m in methods if not is_current(target, m, settings["chunking"])]
    if todo:
        chunk(target, settings["llm"], settings["chunking"], todo)


# --- load, embed, pages --------------------------------------------------------------------


def load_db(method: str, filings: list[Path], out: Path) -> Path:
    """<out>/<method>.db from each filing's document and chunks-<method>.json."""
    path = out / f"{method}.db"
    rows = []
    for filing in filings:
        source = out / filing_name(filing)
        rows.append(
            (
                json.loads((filing / "metadata.json").read_text()),
                json.loads((source / "document.json").read_text()),
                json.loads((source / f"chunks-{method}.json").read_text()),
            )
        )
    counts = load(path, rows)
    print(f"{method}: {len(filings)} filings, {counts} → {path}")
    return path


def embed_db(method: str, out: Path, retrieval: dict) -> None:
    """Vectors for <out>/<method>.db with the config's `retrieval:` embedder and device."""
    store = Store(out / f"{method}.db")
    t = time.time()
    n = embed_corpus(store, get_embedder(retrieval["embedder"], retrieval["device"]))
    print(f"{method}: embedded {n} in {time.time() - t:.0f} s")


def pages_db(method: str, filings: list[Path], out: Path) -> None:
    """Pages and printed pages from chunks-<method>.json into the built <method>.db."""
    store = Store(out / f"{method}.db")  # adds the page columns to an older db
    rows = [
        (
            json.loads((filing / "metadata.json").read_text()),
            json.loads((out / filing_name(filing) / f"chunks-{method}.json").read_text()),
        )
        for filing in filings
    ]
    print(f"{method}: pages for {add_pages(store, rows)} → {out / f'{method}.db'}")


def build(filings: list[Path], out: Path, settings: dict, methods, stages, jobs: int = 8) -> None:
    """Run `stages` in order. `settings`: llm, labeling and chunking (chunk), retrieval (embed)."""
    if "chunk" in stages and (failed := chunk_all(filings, out, settings, methods, jobs)):
        sys.exit(f"{len(failed)} filings failed: {', '.join(failed)}")
    if "load" in stages:
        for method in methods:
            load_db(method, filings, out)
    if "embed" in stages:
        for method in methods:
            embed_db(method, out, settings["retrieval"])
    if "pages" in stages:
        for method in methods:
            pages_db(method, filings, out)
