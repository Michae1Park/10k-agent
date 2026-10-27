"""`tenk companies verify`: check companies.yaml against SEC data (V4 spec §8, items 1–2).

Resolves each ticker's CIK from SEC's company_tickers.json and records what EDGAR's
submissions say: legal name, exchanges, fiscal year-end, and how many 10-Ks, 10-Qs and
earnings releases (8-K Item 2.02) were filed since the window start. Problems are returned
as messages, not raised, so one run reports every company.
"""

from pathlib import Path

import yaml

from tenk_agent.acquisition.edgar import DATA_URL, EdgarClient

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def filing_rows(pages: list[dict], since: str) -> list[dict]:
    """Every filing on or after `since` across column-oriented submissions pages, deduplicated."""
    rows = {}
    for page in pages:
        for i, filed in enumerate(page["filingDate"]):
            if filed >= since:
                rows[page["accessionNumber"][i]] = {key: page[key][i] for key in page}
    return sorted(rows.values(), key=lambda r: r["filingDate"])


def counts(rows: list[dict]) -> dict[str, int]:
    """Original 10-Ks and 10-Qs, and 8-Ks carrying Item 2.02 (results of operations)."""
    return {
        "10-K": sum(r["form"] == "10-K" for r in rows),
        "10-Q": sum(r["form"] == "10-Q" for r in rows),
        "8-K 2.02": sum(r["form"] == "8-K" and "2.02" in r.get("items", "") for r in rows),
    }


def verify_company(ticker: str, entry: dict, cik: int | None, submissions: dict,
                   pages: list[dict], since: str) -> tuple[dict, list[str]]:
    """The entry with `cik` and `verified` filled in, and any problems found."""
    problems = []
    if cik is None:
        return entry, [f"{ticker}: not in SEC company_tickers.json"]
    if entry.get("cik") not in (None, cik):
        problems.append(f"{ticker}: companies.yaml CIK {entry['cik']} but SEC says {cik}")
    fye = submissions.get("fiscalYearEnd")
    if fye != "1231":
        problems.append(f"{ticker}: fiscal year-end {fye}, expected 1231")
    if not submissions.get("exchanges"):
        problems.append(f"{ticker}: no exchange listed (delisted?)")
    found = counts(filing_rows(pages, since))
    if not found["10-K"] or not found["10-Q"]:
        problems.append(f"{ticker}: missing 10-K or 10-Q since {since}: {found}")
    verified = {
        "sec_name": submissions.get("name"),
        "exchanges": submissions.get("exchanges"),
        "fiscal_year_end": fye,
        "since": since,
        "filings": found,
    }
    return {**entry, "cik": cik, "verified": verified}, problems


def verify(path: Path, data_dir: Path, user_agent: str, since: str | None = None,
           refresh: bool = False) -> list[str]:
    """Verify every company in `path` and write the results back to it. `since` defaults to
    the file's window start."""
    client = EdgarClient(user_agent)
    cache = data_dir / "raw" / "edgar"
    tickers = client.cached_json(TICKERS_URL, cache / "company_tickers.json", refresh)
    ciks = {row["ticker"]: row["cik_str"] for row in tickers.values()}

    data = yaml.safe_load(path.read_text())
    since = since or str(data.get("window", {}).get("since", "2023-01-01"))
    companies = data["companies"]
    problems = []
    for ticker, entry in companies.items():
        cik = ciks.get(ticker)
        submissions, pages = {}, []
        if cik is not None:
            submissions = client.cached_json(
                f"{DATA_URL}/submissions/CIK{cik:010d}.json",
                cache / ticker / "submissions.json", refresh,
            )
            pages = [submissions["filings"]["recent"]] + [
                client.cached_json(f"{DATA_URL}/submissions/{page['name']}",
                                   cache / ticker / page["name"], refresh)
                for page in submissions["filings"].get("files", [])
                if page["filingTo"] >= since
            ]
        companies[ticker], found = verify_company(ticker, entry, cik, submissions, pages, since)
        problems += found

    header = "".join(line + "\n" for line in path.read_text().splitlines()
                     if line.startswith("#"))
    path.write_text(header + "\n" + yaml.safe_dump(data, sort_keys=False, width=100))
    return problems
