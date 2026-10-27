"""The as-of rule (V4 design doc §3): AsOf itself, the Store's filter, and the leak test."""

import re
import sqlite3
from datetime import date
from pathlib import Path

from tenk_agent.asof import AsOf
from tenk_agent.retrieval.index import load
from tenk_agent.store import Store
from test_store import APPLE, filing

FILED = "2025-11-01"  # test_store.filing's filing date


def test_strictly_before():
    asof = AsOf.of("2026-08-06")
    assert asof.sees("2026-08-05") and asof.sees("2026-08-05T23:59:00Z")
    assert not asof.sees("2026-08-06") and not asof.sees(None)
    assert AsOf.after("2026-08-05") == asof
    assert asof.sql("c") == ("c.filed < ?", ["2026-08-06"])


def test_latest_takes_the_newest_visible_restatement():
    rows = [("rev", 100, "2025-02-01"), ("rev", 105, "2026-02-01"), ("eps", 1, "2025-02-01")]

    def pick(asof):
        return {k: v[1] for k, v in asof.latest(rows, lambda r: r[0], lambda r: r[2]).items()}

    assert pick(AsOf.of("2026-01-01")) == {"rev": 100, "eps": 1}  # before the restatement
    assert pick(AsOf.of("2026-03-01")) == {"rev": 105, "eps": 1}
    assert pick(AsOf.of("2025-01-01")) == {}


def test_leak_store_sees_nothing_from_a_filing_until_the_day_after(tmp_path):
    load(tmp_path / "t.db", [filing("AAPL", 2025, "2025-09-27", APPLE)])
    chunk_id = Store(tmp_path / "t.db").chunks()[0].id
    for asof, visible in ((AsOf.of(FILED), False), (AsOf.after(FILED), True)):
        store = Store(tmp_path / "t.db", asof)
        found = [
            store.chunks(), store.search("tariffs"), store.child_search("research"),
            store.filings(), [store.filing("AAPL", 2025)] if store.filing("AAPL", 2025) else [],
            [store.get_chunk(chunk_id)] if store.get_chunk(chunk_id) else [],
        ]
        assert all(bool(rows) == visible for rows in found), (asof, found)


def test_chunks_carry_form_period_and_filing_date(tmp_path):
    load(tmp_path / "t.db", [filing("AAPL", 2025, "2025-09-27", APPLE)])
    store = Store(tmp_path / "t.db")
    chunk = store.chunks()[0]
    assert (chunk.form, chunk.fiscal_period, chunk.filed) == ("10-K", "FY", FILED)
    assert {c.filed for c in store.child_search("research")} == {FILED}
    assert store.filings()[0].form == "10-K"


def test_a_v3_database_is_migrated_and_backfilled(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE companies (ticker TEXT PRIMARY KEY, cik INTEGER, name TEXT);
        CREATE TABLE filings (accession_no TEXT PRIMARY KEY, ticker TEXT, fiscal_year INTEGER,
            period_end_date TEXT, filing_date TEXT, source_url TEXT, UNIQUE (ticker, fiscal_year));
        CREATE TABLE sections (id TEXT PRIMARY KEY, accession_no TEXT, item TEXT, title TEXT);
        CREATE TABLE chunks (id TEXT PRIMARY KEY, section_id TEXT, ticker TEXT,
            fiscal_year INTEGER, item TEXT, seq INTEGER, kind TEXT, text TEXT, units TEXT,
            years_covered TEXT);
        INSERT INTO companies VALUES ('X', 1, 'X Corp');
        INSERT INTO filings VALUES ('a1', 'X', 2024, '2024-12-31', '2025-02-01', 'u');
        INSERT INTO sections VALUES ('X-FY2024-7', 'a1', '7', 'MD&A');
        INSERT INTO chunks VALUES ('X-FY2024-7-001', 'X-FY2024-7', 'X', 2024, '7', 1, 'prose',
            'text', NULL, '[]');
    """)
    db.commit()
    db.close()
    assert Store(path, AsOf.of("2025-02-02")).get_chunk("X-FY2024-7-001").filed == "2025-02-01"
    assert Store(path, AsOf.of("2025-02-01")).get_chunk("X-FY2024-7-001") is None


def test_v4_modules_always_pass_an_asof():
    """V3 may open a Store at today's date; V4 code (facts/, research/) never relies on that."""
    src = Path(__file__).resolve().parents[1] / "src" / "tenk_agent"
    for folder in ("facts", "research"):
        for path in (src / folder).rglob("*.py"):
            for call in re.findall(r"\bStore\(([^)]*)\)", path.read_text()):
                assert "," in call, f"{path}: Store({call}) without an AsOf"


def test_today_default_is_today():
    assert AsOf.today().date == date.today()


def test_id_prefixes_by_form():
    from tenk_agent.store import id_prefix

    assert id_prefix("UBER", 2025) == "UBER-FY2025"
    assert id_prefix("UBER", 2026, "Q2", "10-Q") == "UBER-2026Q2"
    assert id_prefix("UBER", 2026, "Q2", "ER") == "UBER-2026Q2-ER"
