"""Build the chunk database from downloaded filings."""

import json
import logging
from pathlib import Path

from tenk_agent.chunking import DEFAULT_SIZES, ChunkSizes, chunk_sections
from tenk_agent.config import db_file
from tenk_agent.corpus import company_by_ticker
from tenk_agent.edgar import Filing
from tenk_agent.hierarchy import DEFAULT_HIERARCHY, HierarchySizes, build
from tenk_agent.parse import parse_filing
from tenk_agent.store import Store

log = logging.getLogger(__name__)

# V0 exit criterion: every filing has these Items, and Item 8 holds the financial tables.
REQUIRED_ITEMS = ("1A", "7", "8")


def db_path(data_dir: Path, layout: str = "flat") -> Path:
    return db_file(data_dir, layout)


def downloaded_filings(data_dir: Path) -> list[Filing]:
    """Every filing in data/raw/manifest.json (written by `tenk fetch`)."""
    manifest = json.loads((data_dir / "raw" / "manifest.json").read_text())
    return [Filing(**record) for records in manifest.values() for record in records]


def ingest(
    data_dir: Path,
    tickers: list[str] | None = None,
    sizes: ChunkSizes = DEFAULT_SIZES,
    layout: str = "flat",
    hierarchy_sizes: HierarchySizes = DEFAULT_HIERARCHY,
) -> Store:
    """`layout="parent_child"` (V7) writes parents and children to their own database."""
    store = Store(db_path(data_dir, layout))
    for filing in downloaded_filings(data_dir):
        ticker = filing.ticker
        if tickers and ticker not in tickers:
            continue
        html = document_path(data_dir, filing).read_text(encoding="utf-8", errors="ignore")
        sections = parse_filing(html)
        prefix = f"{ticker}-FY{filing.fiscal_year}"
        children = None
        if layout == "parent_child":
            chunks, children = build(prefix, sections, hierarchy_sizes)
        else:
            chunks = chunk_sections(prefix, sections, sizes)
        company = company_by_ticker(ticker)
        store.add_filing(filing, company.name, sections, chunks, company.aliases, children)
        log.info(
            "Ingested %s FY%s: %d sections, %d chunks%s",
            ticker,
            filing.fiscal_year,
            len(sections),
            len(chunks),
            f", {len(children)} children" if children is not None else "",
        )
    store.rebuild_search_index()
    return store


def document_path(data_dir: Path, filing: Filing) -> Path:
    filing_dir = data_dir / "raw" / "filings" / filing.ticker / f"FY{filing.fiscal_year}"
    return filing_dir / filing.primary_document


def coverage_problems(store: Store, expected_filings: int) -> list[str]:
    """Problems that fail the V0 ingestion criteria; empty when all filings are complete."""
    coverage = store.coverage()
    problems = []
    if len(coverage) != expected_filings:
        problems.append(f"expected {expected_filings} filings, found {len(coverage)}")
    for (ticker, year), items in sorted(coverage.items()):
        missing = [item for item in REQUIRED_ITEMS if item not in items]
        if missing:
            problems.append(f"{ticker} FY{year}: missing Item(s) {', '.join(missing)}")
        elif items["8"][1] == 0:
            problems.append(f"{ticker} FY{year}: Item 8 has no financial tables")
    return problems
