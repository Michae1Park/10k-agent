"""SQLite storage for filings, sections, chunks, embeddings and traces.

Keyword search uses the built-in FTS5; vector search uses the sqlite-vec extension.
All database access goes through Store, so moving to Postgres + pgvector changes this module.
"""

import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sqlite_vec

from tenk_agent.chunking import Chunk
from tenk_agent.edgar import Filing
from tenk_agent.parse import Section

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    ticker TEXT PRIMARY KEY,
    cik INTEGER NOT NULL,
    name TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]'
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
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS traces (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    question TEXT NOT NULL,
    model TEXT NOT NULL,
    data TEXT NOT NULL
);
"""

# Filings from older databases lack the aliases column; add it in place.
MIGRATIONS = ("ALTER TABLE companies ADD COLUMN aliases TEXT NOT NULL DEFAULT '[]'",)


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


@dataclass
class FilingRecord:
    accession_no: str
    ticker: str
    company_name: str
    fiscal_year: int
    period_end_date: str
    filing_date: str
    source_url: str


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # The API shares one Store across worker threads; a lock serializes access.
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        self._lock = threading.RLock()
        self.db.executescript(SCHEMA)
        for statement in MIGRATIONS:
            try:
                self.db.execute(statement)
            except sqlite3.OperationalError:
                pass  # already applied

    def _query(self, sql: str, params=()) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(sql, params).fetchall()

    # --- Ingestion -----------------------------------------------------------------------

    def add_filing(
        self,
        filing: Filing,
        name: str,
        sections: list[Section],
        chunks: list[Chunk],
        aliases: tuple[str, ...] = (),
    ) -> None:
        """Insert or replace one filing with its sections and chunks."""
        prefix = f"{filing.ticker}-FY{filing.fiscal_year}"
        with self._lock, self.db:
            db = self.db
            if self._has_vectors():
                db.execute(
                    "DELETE FROM chunks_vec WHERE rowid IN "
                    "(SELECT rowid FROM chunks WHERE section_id LIKE ?)",
                    (f"{prefix}-%",),
                )
            db.execute("DELETE FROM chunks WHERE section_id LIKE ?", (f"{prefix}-%",))
            db.execute("DELETE FROM sections WHERE accession_no = ?", (filing.accession_no,))
            db.execute(
                "INSERT OR REPLACE INTO companies VALUES (?, ?, ?, ?)",
                (filing.ticker, filing.cik, name, json.dumps(list(aliases))),
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
        with self._lock, self.db:
            self.db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")

    # --- Embeddings ----------------------------------------------------------------------

    def _has_vectors(self) -> bool:
        return bool(
            self.db.execute("SELECT 1 FROM sqlite_master WHERE name = 'chunks_vec'").fetchone()
        )

    def embedding_model(self) -> str | None:
        rows = self._query("SELECT value FROM meta WHERE key = 'embedding_model'")
        return rows[0]["value"] if rows else None

    def reset_embeddings(self, model: str, dimensions: int) -> None:
        """Drop all vectors and prepare for a (possibly different) embedding model."""
        with self._lock, self.db:
            self.db.execute("DROP TABLE IF EXISTS chunks_vec")
            self.db.execute(
                f"CREATE VIRTUAL TABLE chunks_vec USING vec0("
                f"embedding float[{dimensions}] distance_metric=cosine)"
            )
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('embedding_model', ?)", (model,))

    def chunks_without_embeddings(self) -> list[ChunkRecord]:
        rows = self._query(
            "SELECT * FROM chunks WHERE rowid NOT IN (SELECT rowid FROM chunks_vec) ORDER BY id"
        )
        return [_record(r) for r in rows]

    def add_embeddings(self, chunk_ids: list[str], vectors: np.ndarray) -> None:
        with self._lock, self.db:
            rowids = {
                r["id"]: r["rowid"]
                for r in self.db.execute(
                    f"SELECT rowid, id FROM chunks WHERE id IN ({', '.join('?' * len(chunk_ids))})",
                    chunk_ids,
                )
            }
            self.db.executemany(
                "INSERT INTO chunks_vec(rowid, embedding) VALUES (?, ?)",
                [
                    (rowids[cid], vec.astype(np.float32).tobytes())
                    for cid, vec in zip(chunk_ids, vectors, strict=True)
                ],
            )

    def embedding_count(self) -> int:
        if not self._has_vectors():
            return 0
        return self._query("SELECT count(*) AS n FROM chunks_vec")[0]["n"]

    # --- Lookup --------------------------------------------------------------------------

    def filing_url(self, ticker: str, fiscal_year: int) -> str | None:
        filing = self.filing(ticker, fiscal_year)
        return filing.source_url if filing else None

    def filing(self, ticker: str, fiscal_year: int) -> FilingRecord | None:
        rows = self._query(
            "SELECT f.*, c.name FROM filings f JOIN companies c USING (ticker) "
            "WHERE f.ticker = ? AND f.fiscal_year = ?",
            (ticker.upper(), fiscal_year),
        )
        return _filing(rows[0]) if rows else None

    def filings(self) -> list[FilingRecord]:
        rows = self._query(
            "SELECT f.*, c.name FROM filings f JOIN companies c USING (ticker) "
            "ORDER BY f.ticker, f.fiscal_year"
        )
        return [_filing(r) for r in rows]

    def section_titles(self, ticker: str, fiscal_year: int) -> dict[str, str]:
        rows = self._query(
            "SELECT s.item, s.title FROM sections s JOIN filings f USING (accession_no) "
            "WHERE f.ticker = ? AND f.fiscal_year = ?",
            (ticker.upper(), fiscal_year),
        )
        return {r["item"]: r["title"] for r in rows}

    def get_chunk(self, chunk_id: str) -> ChunkRecord | None:
        rows = self._query("SELECT * FROM chunks WHERE id = ?", (chunk_id,))
        return _record(rows[0]) if rows else None

    def get_section(self, ticker: str, fiscal_year: int, item: str) -> list[ChunkRecord]:
        rows = self._query(
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
        rows = self._query(f"SELECT * FROM chunks WHERE {where} ORDER BY id", params)
        return [_record(r) for r in rows]

    # --- Search --------------------------------------------------------------------------

    def search(
        self,
        query: str,
        tickers: list[str] | None = None,
        fiscal_years: list[int] | None = None,
        items: list[str] | None = None,
        k: int = 10,
    ) -> list[ChunkRecord]:
        """Keyword search (BM25) over chunk text, optionally filtered by company, year and item."""
        terms = [t for t in re.findall(r"\w+", query.lower()) if t not in STOPWORDS]
        if not terms:
            return []
        match = " OR ".join(f'"{term}"' for term in terms)
        where, params = _filters(tickers, fiscal_years, items, table="c")
        rows = self._query(
            f"SELECT c.* FROM chunks_fts JOIN chunks c ON c.rowid = chunks_fts.rowid "
            f"WHERE chunks_fts MATCH ? AND {where} ORDER BY bm25(chunks_fts) LIMIT ?",
            [match, *params, k],
        )
        return [_record(r) for r in rows]

    def vector_search(
        self,
        vector: np.ndarray,
        tickers: list[str] | None = None,
        fiscal_years: list[int] | None = None,
        items: list[str] | None = None,
        k: int = 10,
    ) -> list[ChunkRecord]:
        """Nearest chunks by cosine distance (brute force; milliseconds at this corpus size)."""
        where, params = _filters(tickers, fiscal_years, items, table="c")
        rows = self._query(
            f"SELECT c.* FROM chunks c JOIN chunks_vec v ON v.rowid = c.rowid WHERE {where} "
            f"ORDER BY vec_distance_cosine(v.embedding, ?) LIMIT ?",
            [*params, vector.astype(np.float32).tobytes(), k],
        )
        return [_record(r) for r in rows]

    def coverage(self) -> dict[tuple[str, int], dict[str, tuple[int, int]]]:
        """(ticker, fiscal year) -> {item: (chunk count, table chunk count)}."""
        rows = self._query(
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

    # --- Traces --------------------------------------------------------------------------

    def save_trace(self, trace: dict) -> None:
        with self._lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO traces VALUES (?, ?, ?, ?, ?, ?)",
                (
                    trace["id"],
                    trace["created_at"],
                    trace["mode"],
                    trace["question"],
                    trace["model"],
                    json.dumps(trace),
                ),
            )

    def get_trace(self, trace_id: str) -> dict | None:
        rows = self._query("SELECT data FROM traces WHERE id = ?", (trace_id,))
        return json.loads(rows[0]["data"]) if rows else None

    def recent_traces(self, limit: int = 50) -> list[dict]:
        rows = self._query(
            "SELECT id, created_at, mode, question, model FROM traces "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]


# Common question words that only add noise to BM25 OR-queries.
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "did", "do", "does", "for", "from", "how",
    "in", "is", "it", "its", "of", "on", "or", "the", "to", "was", "were", "what", "which",
    "who", "why", "with",
}  # fmt: skip


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


def _filing(row: sqlite3.Row) -> FilingRecord:
    return FilingRecord(
        accession_no=row["accession_no"],
        ticker=row["ticker"],
        company_name=row["name"],
        fiscal_year=row["fiscal_year"],
        period_end_date=row["period_end_date"],
        filing_date=row["filing_date"],
        source_url=row["source_url"],
    )
