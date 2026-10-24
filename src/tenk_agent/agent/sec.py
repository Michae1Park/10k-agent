"""SEC data the V3 agent can call: XBRL company facts and live EDGAR filing metadata.

Every response is cached under data/raw/edgar/<TICKER>/ (the files `tenk fetch` already
writes for the corpus). Offline (no client), only cached responses are served, so an eval
run never touches the network and always sees the same data.

XBRL facts are a citable source of their own (D-036): `xbrl:<TICKER>:<taxonomy>:<concept>:
<accession>:<period end>`. EDGAR metadata answers which filings exist and when; it never
answers financial questions (D-036: metadata only).
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from tenk_agent.acquisition.edgar import DATA_URL, EdgarClient, EdgarError, annual_reports
from tenk_agent.corpus import resolve_company

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"


class SecError(RuntimeError):
    pass


@dataclass
class SecData:
    cache: Path  # data/raw/edgar
    client: EdgarClient | None = None  # None: offline, cache only

    # --- Companies -----------------------------------------------------------------------

    def resolve(self, name: str) -> tuple[str, int, str]:
        """(ticker, CIK, name) for a corpus company, or any SEC registrant by ticker or name."""
        company = resolve_company(name)
        if company:
            return company.ticker, company.cik, company.name
        tickers = self._json(TICKERS_URL, self.cache / "company_tickers.json")
        key = name.strip().upper()
        for row in tickers.values():
            if row["ticker"].upper() == key:
                return row["ticker"].upper(), int(row["cik_str"]), row["title"]
        for row in tickers.values():
            if _key(row["title"]).startswith(_key(name)):
                return row["ticker"].upper(), int(row["cik_str"]), row["title"]
        raise SecError(f"no SEC registrant matches {name!r}")

    def _json(self, url: str, path: Path) -> dict:
        if path.exists():
            return json.loads(path.read_text())
        if self.client is None:
            raise SecError(f"{path.name} is not cached and the agent is offline")
        try:
            return self.client.cached_json(url, path)
        except EdgarError as e:
            raise SecError(str(e)) from e

    def company_facts(self, ticker: str, cik: int) -> dict:
        return self._json(
            f"{DATA_URL}/api/xbrl/companyfacts/CIK{cik:010d}.json",
            self.cache / ticker / "companyfacts.json",
        )

    def submissions(self, ticker: str, cik: int) -> dict:
        return self._json(
            f"{DATA_URL}/submissions/CIK{cik:010d}.json", self.cache / ticker / "submissions.json"
        )

    # --- XBRL ----------------------------------------------------------------------------

    def xbrl_facts(self, company: str, concept: str, fiscal_year: int) -> dict:
        """The values a 10-K tagged for one concept: the filing's current year and the prior
        years it restates beside it. An unknown concept returns the closest concept names."""
        ticker, cik, name = self.resolve(company)
        facts = self.company_facts(ticker, cik).get("facts", {})
        found = _find_concept(facts, concept)
        if not found:
            return {
                "company": ticker,
                "error": f"no XBRL concept {concept!r}",
                "candidates": _candidates(facts, concept),
            }
        taxonomy, tag = found
        entry = facts[taxonomy][tag]
        accession = _accession_for(facts, fiscal_year)
        if not accession:
            return {"company": ticker, "error": f"no {ticker} 10-K for fiscal year {fiscal_year}"}
        values = []
        for unit, rows in entry["units"].items():
            for row in rows:
                if row.get("accn") != accession or row.get("form") != "10-K":
                    continue
                values.append(
                    {
                        "xbrl_id": f"xbrl:{ticker}:{taxonomy}:{tag}:{accession}:{row['end']}",
                        "value": row["val"],
                        "unit": unit,
                        "period_start": row.get("start"),
                        "period_end": row["end"],
                    }
                )
        values = sorted(
            {v["xbrl_id"] + v["unit"]: v for v in values}.values(),
            key=lambda v: (v["period_end"], v["period_start"] or ""),
            reverse=True,
        )
        return {
            "company": ticker,
            "company_name": name,
            "concept": f"{taxonomy}:{tag}",
            "label": entry.get("label"),
            "fiscal_year": fiscal_year,
            "accession_no": accession,
            "values": values,
        }

    def xbrl_fact(self, xbrl_id: str) -> dict | None:
        """The fact an xbrl_id cites, from the cache (claim check; never fetches)."""
        parts = xbrl_id.split(":")
        if len(parts) != 6 or parts[0] != "xbrl":
            return None
        _, ticker, taxonomy, tag, accession, end = parts
        path = self.cache / ticker / "companyfacts.json"
        if not path.exists():
            return None
        entry = json.loads(path.read_text()).get("facts", {}).get(taxonomy, {}).get(tag)
        for unit, rows in (entry or {}).get("units", {}).items():
            for row in rows:
                if row.get("accn") == accession and row["end"] == end:
                    return {"value": row["val"], "unit": unit, "ticker": ticker}
        return None

    # --- EDGAR metadata ------------------------------------------------------------------

    def filing(self, accession: str) -> dict | None:
        """The filing an accession number names, from the cached submissions (claim check)."""
        for path in sorted(self.cache.glob("*/submissions.json")):
            recent = json.loads(path.read_text())["filings"]["recent"]
            if accession in recent["accessionNumber"]:
                i = recent["accessionNumber"].index(accession)
                return {key: recent[key][i] for key in ("form", "filingDate", "reportDate")} | {
                    "ticker": path.parent.name
                }
        return None

    def filings(self, company: str, form: str = "10-K", fiscal_year: int | None = None) -> dict:
        """Which filings of a form a company made: dates, accession numbers, links."""
        ticker, cik, name = self.resolve(company)
        submissions = self.submissions(ticker, cik)
        recent = submissions["filings"]["recent"]
        if form == "10-K":
            rows = annual_reports([recent])
        else:
            rows = [
                {key: recent[key][i] for key in recent}
                for i, f in enumerate(recent["form"])
                if f == form
            ]
        out = []
        for row in rows:
            if fiscal_year and not row["reportDate"].startswith(str(fiscal_year)):
                continue
            out.append(
                {
                    "form": row["form"],
                    "accession_no": row["accessionNumber"],
                    "filing_date": row["filingDate"],
                    "period_end_date": row["reportDate"],
                    "url": ARCHIVE.format(
                        cik=cik,
                        accession=row["accessionNumber"].replace("-", ""),
                        document=row["primaryDocument"],
                    ),
                }
            )
        return {
            "company": ticker,
            "company_name": name,
            "fiscal_year_end": submissions.get("fiscalYearEnd"),
            "filings": out[:10],
        }


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _find_concept(facts: dict, concept: str) -> tuple[str, str] | None:
    taxonomy, _, tag = concept.rpartition(":")
    for tax in [taxonomy] if taxonomy else ["us-gaap", "dei", "ifrs-full"]:
        if tag in facts.get(tax, {}):
            return tax, tag
    return None


def _candidates(facts: dict, concept: str, n: int = 10) -> list[dict]:
    """Concepts whose name or label share the most words with the query."""
    words = set(re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", concept).lower()))
    scored = []
    for taxonomy, entries in facts.items():
        for tag, entry in entries.items():
            text = re.sub(r"([a-z])([A-Z])", r"\1 \2", tag) + " " + (entry.get("label") or "")
            hits = len(words & set(re.findall(r"[a-z]+", text.lower())))
            if hits:
                scored.append((hits, -len(tag), f"{taxonomy}:{tag}", entry.get("label")))
    scored.sort(reverse=True)
    return [{"concept": c, "label": label} for _, _, c, label in scored[:n]]


def _accession_for(facts: dict, fiscal_year: int) -> str | None:
    """The 10-K tagged with this fiscal year, by its cover-page facts (as acquisition does)."""
    for taxonomy in ("dei",):
        for entry in facts.get(taxonomy, {}).values():
            for rows in entry.get("units", {}).values():
                for row in rows:
                    if row.get("form") == "10-K" and row.get("fy") == fiscal_year:
                        return row["accn"]
    return None
