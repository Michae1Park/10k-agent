"""V1 index: V0's chunks → a searchable database, then vectors.

  load         filings' chunks (and parent-child children) → a new database: SQLite with an
               FTS5 index for keyword search
  add_pages    pages and printed pages into an already loaded database, in place
  embed_corpus a vector per chunk and child (sqlite-vec) for dense search

What is embedded and reranked is the chunk's text under a header line naming the company,
fiscal year and Item (embedding_text), so a chunk that never names its company still matches.
Spec: experiments/V1_RETRIEVAL.md §2.
"""

import logging
from pathlib import Path

from tenk_agent.corpus import COMPANIES, company_by_ticker
from tenk_agent.preprocess.forms import TEN_K, form_spec
from tenk_agent.retrieval.embeddings import Embedder
from tenk_agent.store import ChildRecord, ChunkRecord, FilingRecord, Store

log = logging.getLogger(__name__)

ITEM_NAMES = TEN_K.item_names  # the 10-K's, for callers not yet form-aware
COMPANY_NAMES = {c.ticker: c.name for c in COMPANIES}


# --- What is searched ----------------------------------------------------------------------


def chunk_header(chunk: ChunkRecord | ChildRecord, company_name: str) -> str:
    """Context line prepended to chunk text for embedding and reranking (not for display)."""
    spec = form_spec(getattr(chunk, "form", "10-K"))  # V3 chunks carry no form: all 10-Ks
    item = spec.item_names.get(chunk.item, "")
    return (
        f"{company_name} ({chunk.ticker}) fiscal {chunk.fiscal_year} {spec.form}, "
        f"Item {chunk.item} {item}"
    )


def embedding_text(chunk: ChunkRecord | ChildRecord) -> str:
    """What is embedded and reranked: the header line, then the chunk's (or child's) text."""
    return chunk_header(chunk, COMPANY_NAMES.get(chunk.ticker, chunk.ticker)) + "\n" + chunk.text


def child_text(child: dict) -> str:
    """A child's stored text. V0 §3.2: most children's text has no heading, so the path is put
    in front of it (for the embedder, BM25 and the reranker alike) unless the text already
    starts with it."""
    path = child.get("heading_path") or []
    if not path or child["text"].startswith(path[-1]):
        return child["text"]
    return " › ".join(path) + "\n" + child["text"]


def chunk_ids(chunks: list[dict], prefix: str) -> list[tuple[str, int]]:
    """Each chunk's id and its position within its Item: <prefix>-<item>-<nnn>."""
    seq: dict[str, int] = {}
    ids = []
    for c in chunks:
        n = seq[c["item"]] = seq.get(c["item"], -1) + 1
        ids.append((f"{prefix}-{c['item']}-{n:03d}", n))
    return ids


# --- Load ----------------------------------------------------------------------------------


def load(path: Path, filings: list[tuple[dict, dict, list[dict]]]) -> dict[str, int]:
    """A new database at `path` (any old one is deleted) from (metadata.json, document, chunks)
    per filing. Ticker and fiscal year come from metadata.json (the corpus folder), not the
    cover tags, so ids match the gold sets. Parent-child chunks add their children."""
    path.unlink(missing_ok=True)
    store = Store(path)
    if any(c.get("children") for _, _, chunks in filings for c in chunks):
        store.enable_children()
    counts = {"chunks": 0, "children": 0}
    for meta, document, chunks in filings:
        ticker, year = meta["ticker"], meta["fiscal_year"]
        company = company_by_ticker(ticker)
        prefix = f"{ticker}-FY{year}"
        filing = FilingRecord(
            meta["accession_no"],
            ticker,
            company.name,
            year,
            meta["period_end_date"],
            meta["filing_date"],
            meta["source_url"],
        )
        sections: dict[str, str] = {}
        for s in document["sections"]:
            sections.setdefault(s["item"], s["title"])
        records, children = [], []
        for (chunk_id, n), c in zip(chunk_ids(chunks, prefix), chunks, strict=True):
            records.append((_chunk_record(chunk_id, ticker, year, c), n))
            children += [
                ChildRecord(
                    f"{chunk_id}-c{j:02d}",
                    chunk_id,
                    ticker,
                    year,
                    c["item"],
                    k["kind"],
                    child_text(k),
                )
                for j, k in enumerate(c.get("children") or [])
            ]
        store.add_filing(filing, company.cik, list(company.aliases), sections, records, children)
        counts["chunks"] += len(records)
        counts["children"] += len(children)
    store.rebuild_search_index()
    return counts


def _chunk_record(chunk_id: str, ticker: str, year: int, c: dict) -> ChunkRecord:
    return ChunkRecord(
        id=chunk_id,
        ticker=ticker,
        fiscal_year=year,
        item=c["item"],
        kind=c["kind"],
        text=c["text"],
        units=c.get("units"),
        years_covered=c.get("years_covered") or [],
        pages=c.get("pages"),
        printed_pages=c.get("printed_pages"),
    )


def add_pages(store: Store, filings: list[tuple[dict, list[dict]]]) -> dict[str, int]:
    """Each chunk's pages and printed pages (V0 D8, A5) from (metadata.json, chunks) per filing
    into an already loaded database, matched by id; a chunk whose text differs from the
    database's is skipped. Filings the database doesn't hold are ignored."""
    loaded = {(f.ticker, f.fiscal_year) for f in store.filings()}
    counts = {"updated": 0, "mismatched": 0}
    for meta, chunks in filings:
        if (meta["ticker"], meta["fiscal_year"]) not in loaded:
            continue
        prefix = f"{meta['ticker']}-FY{meta['fiscal_year']}"
        for (chunk_id, _), c in zip(chunk_ids(chunks, prefix), chunks, strict=True):
            updated = store.set_pages(chunk_id, c["text"], c.get("pages"), c.get("printed_pages"))
            counts["updated" if updated else "mismatched"] += 1
    return counts


# --- Embed ---------------------------------------------------------------------------------


def embed_corpus(store: Store, embedder: Embedder, batch: int = 256) -> int:
    """Embed every chunk (and child) that has no vector yet; re-embeds everything if the model
    changed."""
    if store.embedding_model() != embedder.name:
        dimensions = len(embedder.embed_query("dimension probe"))
        store.reset_embeddings(embedder.name, dimensions)
    pending = store.chunks_without_embeddings()
    for start in range(0, len(pending), batch):
        group = pending[start : start + batch]
        vectors = embedder.embed_documents([embedding_text(c) for c in group])
        store.add_embeddings([c.id for c in group], vectors)
        log.info("Embedded %d/%d chunks", min(start + batch, len(pending)), len(pending))
    children = store.children_without_embeddings() if store.has_children() else []
    for start in range(0, len(children), batch):
        group = children[start : start + batch]
        vectors = embedder.embed_documents([embedding_text(c) for c in group])
        store.add_child_embeddings([c.id for c in group], vectors)
        log.info("Embedded %d/%d children", min(start + batch, len(children)), len(children))
    return len(pending) + len(children)
