"""Form 4 insider transactions (V4 design doc §4.2, V4.7): dilution context only.

  taskset -c 16-31 uv run python -m tenk_agent.acquisition.insiders [TICKERS] [--since 2023-01-01]

Each company's Form 4 filings from its cached EDGAR submissions: the raw ownership XML (the
primary document without its xsl stylesheet folder) is cached under raw/insiders/<T>/ (with
retrieved.json: fetch times) and its non-derivative transactions loaded into the store's
insider_tx table, dated by filing date (the as-of filter). Codes: S open-market sale, P purchase,
A award, M option exercise, F shares withheld for tax, G gift. The store's insider_summary gives a
quarter's net open-market and award shares, for the note's dilution context.
"""

import argparse
import json
import os
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from tenk_agent.acquisition.edgar import EdgarClient
from tenk_agent.asof import AsOf
from tenk_agent.config import load_dotenv
from tenk_agent.store import Store

ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"


def filings(raw: Path, ticker: str, since: str) -> list[dict]:
    d = json.loads((raw / "edgar" / ticker / "submissions.json").read_text())
    r = d["filings"]["recent"]
    return [{"accession": r["accessionNumber"][i], "filed": r["filingDate"][i],
             "document": r["primaryDocument"][i].split("/")[-1], "cik": d["cik"]}
            for i, form in enumerate(r["form"]) if form == "4" and r["filingDate"][i] >= since]


def fetch(raw: Path, ticker: str, since: str, client: EdgarClient) -> int:
    folder = raw / "insiders" / ticker
    folder.mkdir(parents=True, exist_ok=True)
    stamps_path = folder / "retrieved.json"  # accession -> when this copy was fetched
    stamps = json.loads(stamps_path.read_text()) if stamps_path.exists() else {}
    added = 0
    for f in filings(raw, ticker, since):
        out = folder / f"{f['accession']}.xml"
        if out.exists():
            continue
        url = ARCHIVE.format(cik=int(f["cik"]), accession=f["accession"].replace("-", ""),
                             document=f["document"])
        out.write_bytes(client.get(url))
        stamps[f["accession"]] = datetime.now(UTC).isoformat(timespec="seconds")
        added += 1
    stamps_path.write_text(json.dumps(stamps))
    return added


def parse(xml: bytes, ticker: str, accession: str, filed: str) -> list[tuple]:
    """insider_tx rows: (ticker, accession, filed, owner, director, officer, title, date, code,
    shares, price, acquired_disposed)."""
    root = ET.fromstring(xml)

    def text(node, path):
        found = node.find(path)
        return found.text.strip() if found is not None and found.text else None

    owner = root.find("reportingOwner")
    name = text(owner, "reportingOwnerId/rptOwnerName") if owner is not None else None
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None
    director = rel is not None and text(rel, "isDirector") in ("1", "true")
    officer = rel is not None and text(rel, "isOfficer") in ("1", "true")
    title = text(rel, "officerTitle") if rel is not None else None
    rows = []
    for tx in root.iter("nonDerivativeTransaction"):
        shares = text(tx, "transactionAmounts/transactionShares/value")
        if shares is None:
            continue
        price = text(tx, "transactionAmounts/transactionPricePerShare/value")
        rows.append((ticker, accession, filed, name, int(director), int(officer), title,
                     text(tx, "transactionDate/value"),
                     text(tx, "transactionCoding/transactionCode"),
                     float(shares), float(price) if price else None,
                     text(tx, "transactionAmounts/transactionAcquiredDisposedCode/value")))
    return rows


def load(raw: Path, store: Store, tickers: list[str], since: str) -> int:
    total = 0
    for ticker in tickers:
        rows = []
        for f in filings(raw, ticker, since):
            path = raw / "insiders" / ticker / f"{f['accession']}.xml"
            if path.exists():
                try:
                    rows += parse(path.read_bytes(), ticker, f["accession"], f["filed"])
                except ET.ParseError:
                    continue  # an HTML rendering cached in error: skipped, counted below
        total += store.add_insider_tx(ticker, rows)
    return total


def main() -> None:
    from tenk_agent.corpus import COMPANIES

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="*", default=[c.ticker for c in COMPANIES])
    ap.add_argument("--data", type=Path, default=Path("data/v4"))
    ap.add_argument("--since", default="2023-01-01")
    args = ap.parse_args()
    load_dotenv()
    client = EdgarClient(os.environ["SEC_USER_AGENT"])
    raw = args.data / "raw"
    for ticker in args.tickers:
        print(f"{ticker}: {fetch(raw, ticker, args.since, client)} Form 4s fetched")
    store = Store(args.data / "build" / "parent_child.db", AsOf.today())  # loading only
    store.enable_facts()
    print(f"insider_tx: {load(raw, store, args.tickers, args.since)} rows")


if __name__ == "__main__":
    main()
