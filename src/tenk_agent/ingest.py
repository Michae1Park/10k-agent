"""Build the chunk database from downloaded filings."""

import json
import logging
from pathlib import Path

from tenk_agent.chunking import chunk_sections
from tenk_agent.corpus import company_by_ticker
from tenk_agent.edgar import Filing
from tenk_agent.parse import parse_filing
from tenk_agent.store import Store

log = logging.getLogger(__name__)

# V0 exit criterion: every filing has these Items, and Item 8 holds the financial tables.
REQUIRED_ITEMS = ("1A", "7", "8")


def db_path(data_dir: Path) -> Path:
    return data_dir / "tenk.db"


def ingest(data_dir: Path, tickers: list[str] | None = None) -> Store:
    manifest = json.loads((data_dir / "raw" / "manifest.json").read_text())
    store = Store(db_path(data_dir))
    for ticker, filings in manifest.items():
        if tickers and ticker not in tickers:
            continue
        for record in filings:
            filing = Filing(**record)
            html = _document_path(data_dir, filing).read_text(encoding="utf-8", errors="ignore")
            sections = parse_filing(html)
            chunks = chunk_sections(f"{ticker}-FY{filing.fiscal_year}", sections)
            store.add_filing(filing, company_by_ticker(ticker).name, sections, chunks)
            log.info(
                "Ingested %s FY%s: %d sections, %d chunks",
                ticker,
                filing.fiscal_year,
                len(sections),
                len(chunks),
            )
    store.rebuild_search_index()
    return store


def _document_path(data_dir: Path, filing: Filing) -> Path:
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
