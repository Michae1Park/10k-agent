"""SQLite storage for filings, sections, chunks (and parent-child children) and embeddings.

Keyword search uses the built-in FTS5; vector search uses the sqlite-vec extension.
All database access goes through Store, so moving to Postgres + pgvector changes this module.

A Store is bound to an AsOf (asof.py, V4 design doc §3): every read of filings, chunks and
children sees only rows filed strictly before it. V3 opens stores at AsOf.today().
"""

import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sqlite_vec

from tenk_agent.asof import AsOf

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
    form TEXT NOT NULL DEFAULT '10-K',
    fiscal_period TEXT NOT NULL DEFAULT 'FY',  -- FY, Q1–Q3; a release: the quarter it reports
    accepted_at TEXT,                         -- EDGAR acceptance time, UTC
    retrieved_at TEXT,
    UNIQUE (ticker, form, fiscal_year, fiscal_period)
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
    printed_pages TEXT,
    form TEXT NOT NULL DEFAULT '10-K',
    fiscal_period TEXT NOT NULL DEFAULT 'FY',
    filed TEXT  -- the filing's filing_date: the as-of filter
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
    text TEXT NOT NULL,
    form TEXT NOT NULL DEFAULT '10-K',
    fiscal_period TEXT NOT NULL DEFAULT 'FY',
    filed TEXT
);
CREATE INDEX IF NOT EXISTS children_filter ON children (ticker, fiscal_year, item);
CREATE INDEX IF NOT EXISTS children_parent ON children (parent_id);
CREATE VIRTUAL TABLE IF NOT EXISTS children_fts
    USING fts5(text, content='children', content_rowid='rowid');
"""

# V4 structured data (design doc §4.3), added by enable_facts(). Every row carries the date it
# became public (`filed`); reads go through the store's AsOf. Quarters are derived from facts on
# read (facts/quarters.py), so they always match the store's AsOf.
FACTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,          -- xbrl:… or ixbrl:… (research/refs.py)
    ticker TEXT NOT NULL,
    taxonomy TEXT NOT NULL,
    concept TEXT NOT NULL,
    dims TEXT NOT NULL,           -- JSON {axis: member}; '{}' = consolidated
    start TEXT,                   -- NULL for an instant
    end TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    decimals INTEGER,
    accession TEXT NOT NULL,
    form TEXT NOT NULL,
    fy INTEGER,
    fp TEXT,
    filed TEXT NOT NULL,
    source TEXT NOT NULL          -- companyfacts | ixbrl
);
CREATE INDEX IF NOT EXISTS facts_lookup ON facts (ticker, concept, filed);
CREATE TABLE IF NOT EXISTS market (  -- prices, macro, ERP (acquisition/market.py)
    series TEXT NOT NULL,         -- px:<T>:close|adj_close|volume · macro:<SERIES> · erp:damodaran
    date TEXT NOT NULL,           -- the observation's date
    value REAL NOT NULL,
    published TEXT NOT NULL,      -- when this value became public (a vintage, for revised series)
    PRIMARY KEY (series, date, published)
);
CREATE TABLE IF NOT EXISTS kpis (    -- release KPIs (facts/extract.py), public from the release
    ticker TEXT NOT NULL,
    metric TEXT NOT NULL,
    segment TEXT NOT NULL,        -- 'total' = consolidated
    period TEXT NOT NULL,         -- 2026Q2
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    definition_version INTEGER NOT NULL,
    chunk_id TEXT NOT NULL,
    quote TEXT NOT NULL,
    method TEXT NOT NULL,         -- llm | rules
    filed TEXT NOT NULL,
    PRIMARY KEY (ticker, metric, segment, period, filed)
);
CREATE TABLE IF NOT EXISTS guidance (
    ticker TEXT NOT NULL,
    metric TEXT NOT NULL,
    segment TEXT NOT NULL,
    target_period TEXT NOT NULL,  -- 2026Q3, FY2026 (target_key of the printed period)
    low REAL,
    high REAL,
    unit TEXT NOT NULL,
    basis TEXT NOT NULL,          -- level | growth_yoy | margin
    chunk_id TEXT NOT NULL,
    quote TEXT NOT NULL,
    issued_in TEXT NOT NULL,      -- the release's period, 2026Q2
    filed TEXT NOT NULL,
    PRIMARY KEY (ticker, metric, segment, target_period, issued_in)
);
"""

# Older databases lack these columns; add them in place. V3 databases (all 10-Ks) gain the V4
# form, period and filing-date columns; BACKFILL then dates their chunks from their filing.
MIGRATIONS = (
    "ALTER TABLE companies ADD COLUMN aliases TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE chunks ADD COLUMN pages TEXT",
    "ALTER TABLE chunks ADD COLUMN printed_pages TEXT",
    "ALTER TABLE filings ADD COLUMN form TEXT NOT NULL DEFAULT '10-K'",
    "ALTER TABLE filings ADD COLUMN fiscal_period TEXT NOT NULL DEFAULT 'FY'",
    "ALTER TABLE filings ADD COLUMN accepted_at TEXT",
    "ALTER TABLE filings ADD COLUMN retrieved_at TEXT",
    "ALTER TABLE chunks ADD COLUMN form TEXT NOT NULL DEFAULT '10-K'",
    "ALTER TABLE chunks ADD COLUMN fiscal_period TEXT NOT NULL DEFAULT 'FY'",
    "ALTER TABLE chunks ADD COLUMN filed TEXT",
)
CHILD_MIGRATIONS = (
    "ALTER TABLE children ADD COLUMN form TEXT NOT NULL DEFAULT '10-K'",
    "ALTER TABLE children ADD COLUMN fiscal_period TEXT NOT NULL DEFAULT 'FY'",
    "ALTER TABLE children ADD COLUMN filed TEXT",
)
BACKFILL = (
    "UPDATE chunks SET filed = (SELECT f.filing_date FROM sections s JOIN filings f "
    "USING (accession_no) WHERE s.id = chunks.section_id) WHERE filed IS NULL",
    "CREATE INDEX IF NOT EXISTS chunks_asof ON chunks (ticker, filed, form, fiscal_year, item)",
)
CHILD_BACKFILL = (
    "UPDATE children SET filed = (SELECT c.filed FROM chunks c WHERE c.id = children.parent_id) "
    "WHERE filed IS NULL",
    "CREATE INDEX IF NOT EXISTS children_asof ON children (ticker, filed, form, fiscal_year, item)",
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
    form: str = "10-K"
    fiscal_period: str = "FY"
    filed: str | None = None  # the filing's filing_date (set on load)


@dataclass
class ChildRecord:
    id: str
    parent_id: str
    ticker: str
    fiscal_year: int
    item: str
    kind: str  # "prose", "row" or "table_title"
    text: str
    form: str = "10-K"
    fiscal_period: str = "FY"
    filed: str | None = None


@dataclass
class FilingRecord:
    accession_no: str
    ticker: str
    company_name: str
    fiscal_year: int
    period_end_date: str
    filing_date: str
    source_url: str
    form: str = "10-K"
    fiscal_period: str = "FY"
    accepted_at: str | None = None
    retrieved_at: str | None = None


def id_prefix(ticker: str, fiscal_year: int, fiscal_period: str = "FY", form: str = "10-K") -> str:
    """A filing's section- and chunk-id prefix (V4 design doc §4.3): <TICKER>-<period>[-<doc>].
    UBER-FY2025 (10-K, as in V0–V3) · UBER-2026Q2 (10-Q) · UBER-2026Q2-ER (release)."""
    key = f"FY{fiscal_year}" if fiscal_period == "FY" else f"{fiscal_year}{fiscal_period}"
    return f"{ticker}-{key}" + ("-ER" if form == "ER" else "")


def target_key(printed: str) -> str:
    """A guidance target as a period key: 'Q3 2026' → 2026Q3; 'Full-year 2026', 'FY 2026',
    'Fiscal Year 2026', '2026' → FY2026. Anything else is kept as printed."""
    year = re.search(r"\b(20\d\d)\b", printed)
    quarter = re.search(r"\bQ([1-4])\b", printed, re.IGNORECASE)
    if not year:
        return printed
    return f"{year[1]}Q{quarter[1]}" if quarter else f"FY{year[1]}"


class Store:
    def __init__(self, path: Path, asof: AsOf | None = None):
        """`asof`: what reads see. None (V3 and loading only) means AsOf.today(); V4 code
        always passes one (tests/test_asof.py checks)."""
        self.asof = asof or AsOf.today()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Parallel workers (V2 run.py) share one Store; a lock serializes access.
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        self._lock = threading.RLock()
        self.db.executescript(SCHEMA)
        self._migrate(MIGRATIONS, BACKFILL)
        if self.has_children():
            self._migrate(CHILD_MIGRATIONS, CHILD_BACKFILL)

    def _migrate(self, statements: tuple[str, ...], backfill: tuple[str, ...]) -> None:
        with self.db:
            for statement in statements:
                try:
                    self.db.execute(statement)
                except sqlite3.OperationalError:
                    pass  # already applied
            for statement in backfill:
                self.db.execute(statement)

    def _query(self, sql: str, params=()) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(sql, params).fetchall()

    # --- Loading -------------------------------------------------------------------------

    def enable_children(self) -> None:
        """Add the children tables: this becomes a parent-child database."""
        with self._lock:
            self.db.executescript(CHILD_SCHEMA)
            self._migrate(CHILD_MIGRATIONS, CHILD_BACKFILL)

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
        prefix = id_prefix(filing.ticker, filing.fiscal_year, filing.fiscal_period, filing.form)
        with self._lock, self.db:
            db = self.db
            db.execute(
                "INSERT OR REPLACE INTO companies VALUES (?, ?, ?, ?)",
                (filing.ticker, cik, filing.company_name, json.dumps(list(aliases))),
            )
            db.execute(
                "INSERT INTO filings (accession_no, ticker, fiscal_year, period_end_date, "
                "filing_date, source_url, form, fiscal_period, accepted_at, retrieved_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    filing.accession_no,
                    filing.ticker,
                    filing.fiscal_year,
                    filing.period_end_date,
                    filing.filing_date,
                    filing.source_url,
                    filing.form,
                    filing.fiscal_period,
                    filing.accepted_at,
                    filing.retrieved_at,
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
                    "INSERT INTO chunks (id, section_id, ticker, fiscal_year, item, seq, kind, "
                    "text, units, years_covered, pages, printed_pages, form, fiscal_period, "
                    "filed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                        filing.form,
                        filing.fiscal_period,
                        filing.filing_date,
                    ),
                )
            if children:
                db.executemany(
                    "INSERT INTO children (id, parent_id, ticker, fiscal_year, item, kind, text, "
                    "form, fiscal_period, filed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (k.id, k.parent_id, k.ticker, k.fiscal_year, k.item, k.kind, k.text,
                         filing.form, filing.fiscal_period, filing.filing_date)
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

    def enable_facts(self) -> None:
        """Add the V4 tables: facts, market, kpis and guidance."""
        with self._lock:
            self.db.executescript(FACTS_SCHEMA)

    def add_facts(self, facts: list) -> int:
        """Insert facts (facts/xbrl.py Fact); an id already stored is kept. Returns the count
        inserted."""
        with self._lock, self.db:
            before = self.db.total_changes
            self.db.executemany(
                f"INSERT OR IGNORE INTO facts VALUES ({', '.join('?' * 16)})",
                [
                    (f.id, f.ticker, f.taxonomy, f.concept, json.dumps(f.dims, sort_keys=True),
                     f.start, f.end, f.value, f.unit, f.decimals, f.accession, f.form, f.fy, f.fp,
                     f.filed, f.source)
                    for f in facts
                ],
            )
            return self.db.total_changes - before

    def facts(
        self, ticker: str, concepts: list[str], dims: dict | None = None, any_dims: bool = False
    ) -> list[dict]:
        """Visible facts of these concepts (any taxonomy); `dims` None = consolidated only, else
        exactly these dimensions; `any_dims`: whatever their dimensions (a split ratio is tagged
        per class of stock). Every restated copy is returned (AsOf.latest picks)."""
        visible, params = self.asof.sql("f")
        dimmed = "" if any_dims else "AND f.dims = ?"
        rows = self._query(
            f"SELECT * FROM facts f WHERE f.ticker = ? AND f.concept IN "
            f"({', '.join('?' * len(concepts))}) {dimmed} AND {visible}",
            [ticker.upper(), *concepts,
             *([] if any_dims else [json.dumps(dims or {}, sort_keys=True)]), *params],
        )
        return [dict(r) | {"dims": json.loads(r["dims"])} for r in rows]

    def fact(self, fact_id: str) -> dict | None:
        visible, params = self.asof.sql("f")
        sql = f"SELECT * FROM facts f WHERE f.id = ? AND {visible}"
        rows = self._query(sql, [fact_id, *params])
        return dict(rows[0]) | {"dims": json.loads(rows[0]["dims"])} if rows else None

    def add_market(self, rows: list[tuple[str, str, float, str]]) -> None:
        """(series, date, value, published) rows; replaces the table's contents for their series."""
        with self._lock, self.db:
            for series in {r[0] for r in rows}:
                self.db.execute("DELETE FROM market WHERE series = ?", (series,))
            self.db.executemany("INSERT OR REPLACE INTO market VALUES (?, ?, ?, ?)", rows)

    def add_extractions(self, ticker: str, period: str, filed: str, kpis: list, guidance: list,
                        method: str = "llm") -> int:
        """One release's extract.py rows; replaces what that release loaded before. Returns the
        rows inserted."""
        with self._lock, self.db:
            self.db.execute("DELETE FROM kpis WHERE ticker = ? AND period = ? AND filed = ?",
                            (ticker, period, filed))
            self.db.execute("DELETE FROM guidance WHERE ticker = ? AND issued_in = ?",
                            (ticker, period))
            self.db.executemany(
                "INSERT OR REPLACE INTO kpis VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(ticker, k["metric"], k.get("segment") or "total", period, k["value"], k["unit"],
                  k.get("definition_version", 1), k["chunk_id"], k["quote"], method, filed)
                 for k in kpis],
            )
            self.db.executemany(
                "INSERT OR REPLACE INTO guidance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(ticker, g["metric"], g.get("segment") or "total", target_key(g["target_period"]),
                  g.get("low"), g.get("high"), g["unit"], g["basis"], g["chunk_id"], g["quote"],
                  period, filed)
                 for g in guidance],
            )
            return len(kpis) + len(guidance)

    def kpis(self, ticker: str, metric: str | None = None) -> list[dict]:
        """Visible KPI rows, oldest period first; a later release's copy of a period (a
        restatement) is returned beside the original."""
        visible, params = self.asof.sql("k")
        rows = self._query(
            f"SELECT * FROM kpis k WHERE k.ticker = ? AND (? IS NULL OR k.metric = ?) AND {visible}"
            " ORDER BY k.period, k.metric, k.segment, k.filed",
            [ticker.upper(), metric, metric, *params],
        )
        return [dict(r) for r in rows]

    def guidance(self, ticker: str, metric: str | None = None) -> list[dict]:
        """Visible guidance rows, in the order issued."""
        visible, params = self.asof.sql("g")
        rows = self._query(
            f"SELECT * FROM guidance g WHERE g.ticker = ? AND (? IS NULL OR g.metric = ?) AND "
            f"{visible} ORDER BY g.filed, g.metric, g.segment",
            [ticker.upper(), metric, metric, *params],
        )
        return [dict(r) for r in rows]

    def series(self, name: str, start: str | None = None) -> list[tuple[str, float]]:
        """(date, value) of a market series as known at the store's AsOf: observations dated and
        published before it, each at its latest visible vintage, oldest first."""
        dated, p1 = self.asof.sql("m", "date")
        published, p2 = self.asof.sql("m", "published")
        rows = self._query(
            f"SELECT m.date, m.value FROM market m WHERE m.series = ? AND {dated} AND {published} "
            "AND m.date >= ? AND m.published = (SELECT MAX(n.published) FROM market n WHERE "
            f"n.series = m.series AND n.date = m.date AND n.published < ?) ORDER BY m.date",
            [name, *p1, *p2, start or "0000", *p2],
        )
        return [(r["date"], r["value"]) for r in rows]

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

    def has_facts(self) -> bool:
        """A V4 database: enable_facts added the facts, market, kpis and guidance tables."""
        return self._has_table("kpis")

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
        """The fiscal year's 10-K, if filed before the as-of."""
        visible, params = self.asof.sql("f", "filing_date")
        rows = self._query(
            "SELECT f.*, c.name FROM filings f JOIN companies c USING (ticker) "
            f"WHERE f.ticker = ? AND f.fiscal_year = ? AND f.form = '10-K' AND {visible}",
            (ticker.upper(), fiscal_year, *params),
        )
        return _filing(rows[0]) if rows else None

    def filings(self) -> list[FilingRecord]:
        visible, params = self.asof.sql("f", "filing_date")
        rows = self._query(
            "SELECT f.*, c.name FROM filings f JOIN companies c USING (ticker) "
            f"WHERE {visible} ORDER BY f.ticker, f.fiscal_year",
            params,
        )
        return [_filing(r) for r in rows]

    def get_chunk(self, chunk_id: str) -> ChunkRecord | None:
        visible, params = self.asof.sql("chunks")
        rows = self._query(f"SELECT * FROM chunks WHERE id = ? AND {visible}", (chunk_id, *params))
        return _chunk(rows[0]) if rows else None

    def chunks(
        self, ticker: str | None = None, fiscal_year: int | None = None
    ) -> list[ChunkRecord]:
        where, params = _filters(
            [ticker] if ticker else None, [fiscal_year] if fiscal_year else None, None,
            asof=self.asof,
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
        periods: list[str] | None = None,
        forms: list[str] | None = None,
    ) -> list[ChunkRecord]:
        """Keyword search (BM25) over chunk text, optionally filtered by company, year and item."""
        rows = self._keyword("chunks", query, tickers, fiscal_years, items, k, periods, forms)
        return [_chunk(r) for r in rows]

    def vector_search(
        self,
        vector: np.ndarray,
        tickers: list[str] | None = None,
        fiscal_years: list[int] | None = None,
        items: list[str] | None = None,
        k: int = 10,
        periods: list[str] | None = None,
        forms: list[str] | None = None,
    ) -> list[ChunkRecord]:
        """Nearest chunks by cosine distance (brute force; milliseconds at this corpus size)."""
        rows = self._nearest("chunks", vector, tickers, fiscal_years, items, k, periods, forms)
        return [_chunk(r) for r in rows]

    def child_search(
        self,
        query: str,
        tickers: list[str] | None = None,
        fiscal_years: list[int] | None = None,
        items: list[str] | None = None,
        k: int = 10,
        periods: list[str] | None = None,
        forms: list[str] | None = None,
    ) -> list[ChildRecord]:
        """Keyword search (BM25) over children, with the same filters as `search`."""
        rows = self._keyword("children", query, tickers, fiscal_years, items, k, periods, forms)
        return [_child(r) for r in rows]

    def child_vector_search(
        self,
        vector: np.ndarray,
        tickers: list[str] | None = None,
        fiscal_years: list[int] | None = None,
        items: list[str] | None = None,
        k: int = 10,
        periods: list[str] | None = None,
        forms: list[str] | None = None,
    ) -> list[ChildRecord]:
        """Nearest children by cosine distance, with the same filters as `search`."""
        rows = self._nearest("children", vector, tickers, fiscal_years, items, k, periods, forms)
        return [_child(r) for r in rows]

    def _keyword(
        self, table, query, tickers, fiscal_years, items, k, periods=None, forms=None
    ) -> list[sqlite3.Row]:
        terms = [t for t in re.findall(r"\w+", query.lower()) if t not in STOPWORDS]
        if not terms:
            return []
        match = " OR ".join(f'"{term}"' for term in terms)
        where, params = _filters(
            tickers, fiscal_years, items, table="c", asof=self.asof, periods=periods, forms=forms
        )
        return self._query(
            f"SELECT c.* FROM {table}_fts JOIN {table} c ON c.rowid = {table}_fts.rowid "
            f"WHERE {table}_fts MATCH ? AND {where} ORDER BY bm25({table}_fts) LIMIT ?",
            [match, *params, k],
        )

    def _nearest(
        self, table, vector, tickers, fiscal_years, items, k, periods=None, forms=None
    ) -> list[sqlite3.Row]:
        where, params = _filters(
            tickers, fiscal_years, items, table="c", asof=self.asof, periods=periods, forms=forms
        )
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


def _filters(
    tickers, fiscal_years, items, table: str = "chunks", *, asof: AsOf, periods=None, forms=None
) -> tuple[str, list]:
    """V4 adds `periods` (2026Q2, FY2025: a fiscal year and period) and `forms` (10-K, 10-Q,
    ER); both None is V3's filter."""
    visible, params = asof.sql(table)
    clauses = [visible]
    for column, values in (
        ("ticker", tickers), ("fiscal_year", fiscal_years), ("item", items), ("form", forms)
    ):
        if values:
            clauses.append(f"{table}.{column} IN ({', '.join('?' * len(values))})")
            params.extend(v.upper() if isinstance(v, str) else v for v in values)
    if periods:
        pairs = [period_parts(p) for p in periods]
        clauses.append("(" + " OR ".join(
            f"({table}.fiscal_year = ? AND {table}.fiscal_period = ?)" for _ in pairs
        ) + ")")
        params.extend(v for pair in pairs for v in pair)
    return " AND ".join(clauses), params


def period_parts(period: str) -> tuple[int, str]:
    """2026Q2 → (2026, 'Q2'); FY2025 → (2025, 'FY')."""
    key = period.strip().upper().replace(" ", "")
    match = re.fullmatch(r"FY(\d{4})|(\d{4})(Q[1-4])", key)
    if not match:
        raise ValueError(f"bad period {period!r}: use e.g. 2026Q2 or FY2025")
    return (int(match[1]), "FY") if match[1] else (int(match[2]), match[3])


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
        form=row["form"],
        fiscal_period=row["fiscal_period"],
        filed=row["filed"],
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
        form=row["form"],
        fiscal_period=row["fiscal_period"],
        filed=row["filed"],
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
        form=row["form"],
        fiscal_period=row["fiscal_period"],
        accepted_at=row["accepted_at"],
        retrieved_at=row["retrieved_at"],
    )
