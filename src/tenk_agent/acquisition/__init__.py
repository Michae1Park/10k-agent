"""Data acquisition: the corpus 10-Ks (corpus.py, D-001) downloaded from SEC EDGAR.

  edgar.py  EDGAR client (fair-access rate limit, retries); one 10-K resolved per fiscal year

Each filing lands in <data_dir>/raw/filings/<TICKER>/FY<year>/ (the document and its
metadata.json), EDGAR's JSON in raw/edgar/<TICKER>/, and raw/manifest.json lists them all.
Used by `tenk fetch`; the build (build.py) reads the filings from there.
"""

import json
import logging
from dataclasses import asdict
from pathlib import Path

from tenk_agent.acquisition.edgar import EdgarClient, EdgarError, fetch_company
from tenk_agent.corpus import Company


def fetch(
    companies: list[Company],
    fiscal_years: tuple[int, ...],
    data_dir: Path,
    user_agent: str,
    refresh: bool = False,
) -> bool:
    """Download each company's 10-Ks and record them in raw/manifest.json. A company that fails
    is logged and skipped; returns False if any did."""
    client = EdgarClient(user_agent)
    manifest_path = data_dir / "raw" / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    ok = True
    for company in companies:
        try:
            filings = fetch_company(client, company, fiscal_years, data_dir, refresh)
        except EdgarError as error:
            logging.error("%s", error)
            ok = False
            continue
        manifest[company.ticker] = [asdict(f) for f in filings]
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return ok
