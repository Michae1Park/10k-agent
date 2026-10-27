"""Build the corpus: every filing through V0 (preprocess → chunk), then into V1's database.

  chunk  HTML → document → chunks-<method>.json per filing (needs the LLM served). Filings run
         in parallel processes, one log each (vLLM batches their calls); windows within a
         filing in order. Cached: labels are reused, and each method's chunks while its
         settings match
  load   chunks → <out>/<method>.db, rebuilt from scratch; parent-child adds children
  embed  vectors for every chunk (and child). Stop vLLM first, or set retrieval.device: cpu
  pages  each chunk's pages, written into an already built <method>.db in place
  facts  V4: XBRL company facts and inline facts of every 10-K and 10-Q into <method>.db

Filings are read from <data_dir>/raw/filings/<TICKER>/FY<year>/ (tenk fetch); each one's work
files (blocks, labels, document, chunks-<method>.json, chunk.log) go to <out>/<TICKER>-FY<year>/.
Used by `tenk build` and experiments/v1/build_corpus.py. Spec: docs/stages/V0_PARSING_CHUNKING.md.
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

STAGES = ("chunk", "load", "embed", "pages", "facts")


def filing_dirs(
    data_dir: Path, names: list[str] | None = None, pattern: str = "FY*"
) -> list[Path]:
    """The filings' folders: `names` as TICKER/<folder>, or every one downloaded whose folder
    matches `pattern` (V3: FY<year>; V4: FY2025-10K, 2026Q2-10Q, 2026Q2-ER, so "*")."""
    root = data_dir / "raw" / "filings"
    return [root / n for n in names] if names else sorted(root.glob(f"*/{pattern}"))


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
    meta = json.loads((filing / "metadata.json").read_text())
    if not (target / "document.json").exists():
        if meta.get("form") in ("10-Q", "ER"):  # V4: rule-based structure (preprocess/rules.py)
            from tenk_agent.corpus import company_by_ticker
            from tenk_agent.preprocess.rules import preprocess_filing

            preprocess_filing(filing, target, company_by_ticker(meta["ticker"]).name)
        else:
            html = next(filing.glob("*.htm"))
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


def facts_db(method: str, filings: list[Path], out: Path, since: str) -> None:
    """V4 facts (facts/xbrl.py) into <out>/<method>.db: each company's companyfacts JSON (from
    the fetch cache beside the filings) and the inline XBRL of its 10-Ks and 10-Qs."""
    from tenk_agent.asof import AsOf
    from tenk_agent.facts.xbrl import company_facts, filing_facts

    store = Store(out / f"{method}.db", AsOf.today())  # loading only: no dated reads
    store.enable_facts()
    counts = {"companyfacts": 0, "inline": 0}
    for ticker in sorted({f.parent.name for f in filings}):
        raw = filings[0].parents[2] / "edgar" / ticker / "companyfacts.json"
        counts["companyfacts"] += store.add_facts(
            company_facts(ticker, json.loads(raw.read_text()), since)
        )
    for filing in filings:
        if filing.name.endswith(("-10K", "-10Q")):
            counts["inline"] += store.add_facts(filing_facts(filing))
    print(f"{method}: facts {counts} → {out / f'{method}.db'}")


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
    if "facts" in stages:
        from tenk_agent.corpus import WINDOW_START

        for method in methods:
            facts_db(method, filings, out, WINDOW_START)
