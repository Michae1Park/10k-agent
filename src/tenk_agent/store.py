"""SQLite storage for filings, sections and chunks, with FTS5 keyword search.

All database access goes through Store. Vector search (sqlite-vec) is added in V1.
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from tenk_agent.chunking import Chunk
from tenk_agent.edgar import Filing
from tenk_agent.parse import Section

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    ticker TEXT PRIMARY KEY,
    cik INTEGER NOT NULL,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS filings (
    accession_no TEXT PRIMARY KEY,
    ticker TEXT NOT NULL REFERENCES companies(ticker),
    fiscal_year INTEGER NOT NULL,
    period_end_date TEXT NOT NULL,
    filing_date TEXT NOT NULL,
    source_url TEXT NOT NULL,
    UNIQUE (ticker, fiscal_year)
);
CREATE TABLE IF NOT EXISTS sections (
    id TEXT PRIMARY KEY,
    accession_no TEXT NOT NULL REFERENCES filings(accession_no),
    item TEXT NOT NULL,
    title TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    section_id TEXT NOT NULL REFERENCES sections(id),
    ticker TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    item TEXT NOT NULL,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    units TEXT,
    years_covered TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_filter ON chunks (ticker, fiscal_year, item);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
    USING fts5(text, content='chunks', content_rowid='rowid');
"""


@dataclass
class ChunkRecord:
    id: str
    ticker: str
    fiscal_year: int
    item: str
    kind: str
    text: str
    units: str | None
    years_covered: list[int]


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def add_filing(
        self, filing: Filing, name: str, sections: list[Section], chunks: list[Chunk]
    ) -> None:
        """Insert or replace one filing with its sections and chunks."""
        prefix = f"{filing.ticker}-FY{filing.fiscal_year}"
        with self.db:
            db = self.db
            db.execute("DELETE FROM chunks WHERE section_id LIKE ?", (f"{prefix}-%",))
            db.execute("DELETE FROM sections WHERE accession_no = ?", (filing.accession_no,))
            db.execute(
                "INSERT OR REPLACE INTO companies VALUES (?, ?, ?)",
                (filing.ticker, filing.cik, name),
            )
            db.execute(
                "INSERT OR REPLACE INTO filings VALUES (?, ?, ?, ?, ?, ?)",
                (
                    filing.accession_no,
                    filing.ticker,
                    filing.fiscal_year,
                    filing.period_end_date,
                    filing.filing_date,
                    filing.source_url,
                ),
            )
            db.executemany(
                "INSERT INTO sections VALUES (?, ?, ?, ?)",
                [(f"{prefix}-{s.item}", filing.accession_no, s.item, s.title) for s in sections],
            )
            db.executemany(
                "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        c.id,
                        f"{prefix}-{c.item}",
                        filing.ticker,
                        filing.fiscal_year,
                        c.item,
                        c.seq,
                        c.kind,
                        c.text,
                        c.units,
                        json.dumps(c.years_covered),
                    )
                    for c in chunks
                ],
            )

    def rebuild_search_index(self) -> None:
        with self.db:
            self.db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")

    def filing_url(self, ticker: str, fiscal_year: int) -> str | None:
        row = self.db.execute(
            "SELECT source_url FROM filings WHERE ticker = ? AND fiscal_year = ?",
            (ticker.upper(), fiscal_year),
        ).fetchone()
        return row["source_url"] if row else None

    def get_chunk(self, chunk_id: str) -> ChunkRecord | None:
        row = self.db.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        return _record(row) if row else None

    def get_section(self, ticker: str, fiscal_year: int, item: str) -> list[ChunkRecord]:
        rows = self.db.execute(
            "SELECT * FROM chunks WHERE ticker = ? AND fiscal_year = ? AND item = ? ORDER BY seq",
            (ticker.upper(), fiscal_year, item.upper()),
        )
        return [_record(r) for r in rows]

    def chunks(
        self, ticker: str | None = None, fiscal_year: int | None = None
    ) -> list[ChunkRecord]:
        where, params = _filters(
            [ticker] if ticker else None, [fiscal_year] if fiscal_year else None, None
        )
        rows = self.db.execute(f"SELECT * FROM chunks WHERE {where} ORDER BY id", params)
        return [_record(r) for r in rows]

    def search(
        self,
        query: str,
        tickers: list[str] | None = None,
        fiscal_years: list[int] | None = None,
        items: list[str] | None = None,
        k: int = 10,
    ) -> list[ChunkRecord]:
        """Keyword search (BM25) over chunk text, optionally filtered by company, year and item."""
        terms = re.findall(r"\w+", query)
        if not terms:
            return []
        match = " OR ".join(f'"{term}"' for term in terms)
        where, params = _filters(tickers, fiscal_years, items, table="c")
        rows = self.db.execute(
            f"SELECT c.* FROM chunks_fts JOIN chunks c ON c.rowid = chunks_fts.rowid "
            f"WHERE chunks_fts MATCH ? AND {where} ORDER BY bm25(chunks_fts) LIMIT ?",
            [match, *params, k],
        )
        return [_record(r) for r in rows]

    def coverage(self) -> dict[tuple[str, int], dict[str, tuple[int, int]]]:
        """(ticker, fiscal year) -> {item: (chunk count, table chunk count)}."""
        rows = self.db.execute(
            "SELECT ticker, fiscal_year, item, count(*) AS n, sum(kind = 'table') AS tables "
            "FROM chunks GROUP BY ticker, fiscal_year, item"
        )
        result: dict[tuple[str, int], dict[str, tuple[int, int]]] = {}
        for r in rows:
            result.setdefault((r["ticker"], r["fiscal_year"]), {})[r["item"]] = (
                r["n"],
                r["tables"],
            )
        return result


def _filters(tickers, fiscal_years, items, table: str = "chunks") -> tuple[str, list]:
    clauses, params = ["1 = 1"], []
    for column, values in (("ticker", tickers), ("fiscal_year", fiscal_years), ("item", items)):
        if values:
            clauses.append(f"{table}.{column} IN ({', '.join('?' * len(values))})")
            params.extend(v.upper() if isinstance(v, str) else v for v in values)
    return " AND ".join(clauses), params


def _record(row: sqlite3.Row) -> ChunkRecord:
    return ChunkRecord(
        id=row["id"],
        ticker=row["ticker"],
        fiscal_year=row["fiscal_year"],
        item=row["item"],
        kind=row["kind"],
        text=row["text"],
        units=row["units"],
        years_covered=json.loads(row["years_covered"]),
    )
