"""SQLite storage for filings, sections, chunks (and parent-child children) and embeddings.

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
    years_covered TEXT NOT NULL,
    pages TEXT,
    printed_pages TEXT
);
CREATE INDEX IF NOT EXISTS chunks_filter ON chunks (ticker, fiscal_year, item);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
    USING fts5(text, content='chunks', content_rowid='rowid');
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Parent-child databases only: small children are searched, their parent chunk is returned.
CHILD_SCHEMA = """
CREATE TABLE IF NOT EXISTS children (
    id TEXT PRIMARY KEY,
    parent_id TEXT NOT NULL REFERENCES chunks(id),
    ticker TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    item TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS children_filter ON children (ticker, fiscal_year, item);
CREATE INDEX IF NOT EXISTS children_parent ON children (parent_id);
CREATE VIRTUAL TABLE IF NOT EXISTS children_fts
    USING fts5(text, content='children', content_rowid='rowid');
"""

# Older databases lack these columns; add them in place.
MIGRATIONS = (
    "ALTER TABLE companies ADD COLUMN aliases TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE chunks ADD COLUMN pages TEXT",
    "ALTER TABLE chunks ADD COLUMN printed_pages TEXT",
)


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
    pages: list[int] | None = None  # V0 chunks: first and last page in the document
    printed_pages: list[str] | None = None  # the page numbers printed on those pages


@dataclass
class ChildRecord:
    id: str
    parent_id: str
    ticker: str
    fiscal_year: int
    item: str
    kind: str  # "prose", "row" or "table_title"
    text: str


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
        # Parallel workers (V2 run.py) share one Store; a lock serializes access.
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

    # --- Loading -------------------------------------------------------------------------

    def enable_children(self) -> None:
        """Add the children tables: this becomes a parent-child database."""
        with self._lock:
            self.db.executescript(CHILD_SCHEMA)

    def add_filing(
        self,
        filing: FilingRecord,
        cik: int,
        aliases: list[str],
        sections: dict[str, str],
        chunks: list[tuple[ChunkRecord, int]],
        children: list[ChildRecord] = (),
    ) -> None:
        """Insert one filing: its company, sections ({item: title}), chunks (each with its seq
        within its Item) and children. For a new database: nothing is replaced."""
        prefix = f"{filing.ticker}-FY{filing.fiscal_year}"
        with self._lock, self.db:
            db = self.db
            db.execute(
                "INSERT OR REPLACE INTO companies VALUES (?, ?, ?, ?)",
                (filing.ticker, cik, filing.company_name, json.dumps(list(aliases))),
            )
            db.execute(
                "INSERT INTO filings VALUES (?, ?, ?, ?, ?, ?)",
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
                [
                    (f"{prefix}-{item}", filing.accession_no, item, t)
                    for item, t in sections.items()
                ],
            )
            for c, seq in chunks:
                db.execute(
                    "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        c.id,
                        f"{prefix}-{c.item}",
                        c.ticker,
                        c.fiscal_year,
                        c.item,
                        seq,
                        c.kind,
                        c.text,
                        c.units,
                        json.dumps(c.years_covered),
                        json.dumps(c.pages) if c.pages else None,
                        json.dumps(c.printed_pages) if c.printed_pages else None,
                    ),
                )
            if children:
                db.executemany(
                    "INSERT INTO children (id, parent_id, ticker, fiscal_year, item, kind, text) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (k.id, k.parent_id, k.ticker, k.fiscal_year, k.item, k.kind, k.text)
                        for k in children
                    ],
                )

    def set_pages(
        self, chunk_id: str, text: str, pages: list[int] | None, printed: list[str] | None
    ) -> bool:
        """Write a chunk's pages and printed pages; False if no chunk has this id and text."""
        with self._lock, self.db:
            updated = self.db.execute(
                "UPDATE chunks SET pages = ?, printed_pages = ? WHERE id = ? AND text = ?",
                (
                    json.dumps(pages) if pages else None,
                    json.dumps(printed) if printed else None,
                    chunk_id,
                    text,
                ),
            ).rowcount
        return bool(updated)

    def rebuild_search_index(self) -> None:
        with self._lock, self.db:
            self.db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
            if self.has_children():
                self.db.execute("INSERT INTO children_fts(children_fts) VALUES ('rebuild')")

    # --- Embeddings ----------------------------------------------------------------------

    def _has_table(self, name: str) -> bool:
        return bool(
            self.db.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone()
        )

    def has_children(self) -> bool:
        """True for a parent-child database."""
        return self._has_table("children")

    def embedding_model(self) -> str | None:
        rows = self._query("SELECT value FROM meta WHERE key = 'embedding_model'")
        return rows[0]["value"] if rows else None

    def reset_embeddings(self, model: str, dimensions: int) -> None:
        """Drop all vectors and prepare for a (possibly different) embedding model."""
        with self._lock, self.db:
            for table in ("chunks_vec", "children_vec") if self.has_children() else ("chunks_vec",):
                self.db.execute(f"DROP TABLE IF EXISTS {table}")
                self.db.execute(
                    f"CREATE VIRTUAL TABLE {table} USING vec0("
                    f"embedding float[{dimensions}] distance_metric=cosine)"
                )
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('embedding_model', ?)", (model,))

    def chunks_without_embeddings(self) -> list[ChunkRecord]:
        return [_chunk(r) for r in self._without_embeddings("chunks")]

    def children_without_embeddings(self) -> list[ChildRecord]:
        return [_child(r) for r in self._without_embeddings("children")]

    def add_embeddings(self, chunk_ids: list[str], vectors: np.ndarray) -> None:
        self._add_embeddings("chunks", chunk_ids, vectors)

    def add_child_embeddings(self, child_ids: list[str], vectors: np.ndarray) -> None:
        self._add_embeddings("children", child_ids, vectors)

    def _without_embeddings(self, table: str) -> list[sqlite3.Row]:
        return self._query(
            f"SELECT * FROM {table} WHERE rowid NOT IN (SELECT rowid FROM {table}_vec) ORDER BY id"
        )

    def _add_embeddings(self, table: str, ids: list[str], vectors: np.ndarray) -> None:
        with self._lock, self.db:
            rowids = {
                r["id"]: r["rowid"]
                for r in self.db.execute(
                    f"SELECT rowid, id FROM {table} WHERE id IN ({', '.join('?' * len(ids))})", ids
                )
            }
            self.db.executemany(
                f"INSERT INTO {table}_vec(rowid, embedding) VALUES (?, ?)",
                [
                    (rowids[i], vec.astype(np.float32).tobytes())
                    for i, vec in zip(ids, vectors, strict=True)
                ],
            )

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

    def get_chunk(self, chunk_id: str) -> ChunkRecord | None:
        rows = self._query("SELECT * FROM chunks WHERE id = ?", (chunk_id,))
        return _chunk(rows[0]) if rows else None

    def chunks(
        self, ticker: str | None = None, fiscal_year: int | None = None
    ) -> list[ChunkRecord]:
        where, params = _filters(
            [ticker] if ticker else None, [fiscal_year] if fiscal_year else None, None
        )
        rows = self._query(f"SELECT * FROM chunks WHERE {where} ORDER BY id", params)
        return [_chunk(r) for r in rows]

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
        return [_chunk(r) for r in self._keyword("chunks", query, tickers, fiscal_years, items, k)]

    def vector_search(
        self,
        vector: np.ndarray,
        tickers: list[str] | None = None,
        fiscal_years: list[int] | None = None,
        items: list[str] | None = None,
        k: int = 10,
    ) -> list[ChunkRecord]:
        """Nearest chunks by cosine distance (brute force; milliseconds at this corpus size)."""
        return [_chunk(r) for r in self._nearest("chunks", vector, tickers, fiscal_years, items, k)]

    def child_search(
        self,
        query: str,
        tickers: list[str] | None = None,
        fiscal_years: list[int] | None = None,
        items: list[str] | None = None,
        k: int = 10,
    ) -> list[ChildRecord]:
        """Keyword search (BM25) over children, with the same filters as `search`."""
        rows = self._keyword("children", query, tickers, fiscal_years, items, k)
        return [_child(r) for r in rows]

    def child_vector_search(
        self,
        vector: np.ndarray,
        tickers: list[str] | None = None,
        fiscal_years: list[int] | None = None,
        items: list[str] | None = None,
        k: int = 10,
    ) -> list[ChildRecord]:
        """Nearest children by cosine distance, with the same filters as `search`."""
        rows = self._nearest("children", vector, tickers, fiscal_years, items, k)
        return [_child(r) for r in rows]

    def _keyword(self, table, query, tickers, fiscal_years, items, k) -> list[sqlite3.Row]:
        terms = [t for t in re.findall(r"\w+", query.lower()) if t not in STOPWORDS]
        if not terms:
            return []
        match = " OR ".join(f'"{term}"' for term in terms)
        where, params = _filters(tickers, fiscal_years, items, table="c")
        return self._query(
            f"SELECT c.* FROM {table}_fts JOIN {table} c ON c.rowid = {table}_fts.rowid "
            f"WHERE {table}_fts MATCH ? AND {where} ORDER BY bm25({table}_fts) LIMIT ?",
            [match, *params, k],
        )

    def _nearest(self, table, vector, tickers, fiscal_years, items, k) -> list[sqlite3.Row]:
        where, params = _filters(tickers, fiscal_years, items, table="c")
        return self._query(
            f"SELECT c.* FROM {table} c JOIN {table}_vec v ON v.rowid = c.rowid WHERE {where} "
            f"ORDER BY vec_distance_cosine(v.embedding, ?) LIMIT ?",
            [*params, vector.astype(np.float32).tobytes(), k],
        )


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


def _chunk(row: sqlite3.Row) -> ChunkRecord:
    return ChunkRecord(
        id=row["id"],
        ticker=row["ticker"],
        fiscal_year=row["fiscal_year"],
        item=row["item"],
        kind=row["kind"],
        text=row["text"],
        units=row["units"],
        years_covered=json.loads(row["years_covered"]),
        pages=json.loads(row["pages"]) if row["pages"] else None,
        printed_pages=json.loads(row["printed_pages"]) if row["printed_pages"] else None,
    )


def _child(row: sqlite3.Row) -> ChildRecord:
    return ChildRecord(
        id=row["id"],
        parent_id=row["parent_id"],
        ticker=row["ticker"],
        fiscal_year=row["fiscal_year"],
        item=row["item"],
        kind=row["kind"],
        text=row["text"],
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
