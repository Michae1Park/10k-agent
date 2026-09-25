"""Fetch 10-K filings from SEC EDGAR.

SEC fair-access rules: a User-Agent with contact details, and at most 10 requests/s.
"""

import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from tenk_agent.corpus import Company

log = logging.getLogger(__name__)

DATA_URL = "https://data.sec.gov"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"


class EdgarError(RuntimeError):
    pass


@dataclass(frozen=True)
class Filing:
    ticker: str
    cik: int
    accession_no: str
    filing_date: str
    period_end_date: str
    fiscal_year: int
    primary_document: str
    source_url: str


class EdgarClient:
    def __init__(self, user_agent: str, min_interval: float = 0.12, retries: int = 4):
        if "@" not in user_agent:
            raise EdgarError("User-Agent needs contact details: 'Name you@example.com'")
        self._http = httpx.Client(
            headers={"User-Agent": user_agent}, timeout=60, follow_redirects=True
        )
        self._min_interval = min_interval
        self._retries = retries
        self._last = 0.0

    def get(self, url: str) -> bytes:
        for attempt in range(self._retries + 1):
            time.sleep(max(0.0, self._min_interval - (time.monotonic() - self._last)))
            self._last = time.monotonic()
            response = self._http.get(url)
            if response.status_code == 200:
                return response.content
            if response.status_code not in (429, 500, 502, 503, 504) or attempt == self._retries:
                break
            time.sleep(2**attempt)
        raise EdgarError(f"HTTP {response.status_code} for {url}")

    def cached_json(self, url: str, path: Path, refresh: bool = False) -> dict:
        if refresh or not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.get(url))
        return json.loads(path.read_text())


def annual_reports(pages: list[dict]) -> list[dict]:
    """Original 10-Ks (no amendments) across column-oriented submissions pages, deduplicated."""
    reports = {}
    for page in pages:
        for i, form in enumerate(page["form"]):
            if form == "10-K":
                reports[page["accessionNumber"][i]] = {key: page[key][i] for key in page}
    return sorted(reports.values(), key=lambda r: r["filingDate"], reverse=True)


def fiscal_years_by_accession(company_facts: dict) -> dict[str, int]:
    """The fiscal year each 10-K was tagged with in XBRL.

    Public float is a required 10-K cover-page fact, so every 10-K reports it, and
    each fact carries its filing's fiscal year (`fy`).
    """
    public_float = company_facts.get("facts", {}).get("dei", {}).get("EntityPublicFloat", {})
    facts = public_float.get("units", {}).get("USD", [])
    return {fact["accn"]: fact["fy"] for fact in facts if fact.get("form") == "10-K"}


def resolve_filings(
    company: Company, pages: list[dict], company_facts: dict, fiscal_years: tuple[int, ...]
) -> list[Filing]:
    """Exactly one 10-K per fiscal year, using the XBRL fiscal year (else the period-end year)."""
    xbrl_years = fiscal_years_by_accession(company_facts)
    by_year: dict[int, list[Filing]] = {}
    for report in annual_reports(pages):
        accession = report["accessionNumber"]
        document = report["primaryDocument"]
        year = xbrl_years.get(accession, int(report["reportDate"][:4]))
        filing = Filing(
            ticker=company.ticker,
            cik=company.cik,
            accession_no=accession,
            filing_date=report["filingDate"],
            period_end_date=report["reportDate"],
            fiscal_year=year,
            primary_document=document,
            source_url=ARCHIVE_URL.format(
                cik=company.cik, accession=accession.replace("-", ""), document=document
            ),
        )
        by_year.setdefault(year, []).append(filing)

    for year in fiscal_years:
        if len(by_year.get(year, [])) != 1:
            found = [f.accession_no for f in by_year.get(year, [])]
            raise EdgarError(f"{company.ticker} FY{year}: expected one 10-K, found {found}")
    return [by_year[year][0] for year in fiscal_years]


def fetch_company(
    client: EdgarClient,
    company: Company,
    fiscal_years: tuple[int, ...],
    data_dir: Path,
    refresh: bool = False,
) -> list[Filing]:
    """Download a company's 10-Ks for the given fiscal years, each with a metadata.json."""
    cache = data_dir / "raw" / "edgar" / company.ticker
    cik = f"CIK{company.cik:010d}"
    submissions = client.cached_json(
        f"{DATA_URL}/submissions/{cik}.json", cache / "submissions.json", refresh
    )
    facts = client.cached_json(
        f"{DATA_URL}/api/xbrl/companyfacts/{cik}.json", cache / "companyfacts.json", refresh
    )

    # The main document lists only the ~1,000 most recent filings; frequent filers
    # push older 10-Ks into linked pages.
    pages = [submissions["filings"]["recent"]] + [
        client.cached_json(f"{DATA_URL}/submissions/{page['name']}", cache / page["name"], refresh)
        for page in submissions["filings"].get("files", [])
        if page["filingTo"] >= f"{min(fiscal_years)}-01-01"
    ]

    filings = resolve_filings(company, pages, facts, fiscal_years)
    for filing in filings:
        target = data_dir / "raw" / "filings" / company.ticker / f"FY{filing.fiscal_year}"
        document = target / filing.primary_document
        if refresh or not document.exists():
            target.mkdir(parents=True, exist_ok=True)
            document.write_bytes(client.get(filing.source_url))
            log.info("Downloaded %s FY%s", filing.ticker, filing.fiscal_year)
        (target / "metadata.json").write_text(json.dumps(asdict(filing), indent=2) + "\n")
    return filings
