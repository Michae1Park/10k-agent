"""Structured facts from XBRL (V4 design doc §4.5), two sources into one `facts` table.

  company facts  SEC's companyfacts JSON: us-gaap, dei, srt facts, consolidated only, each
                 with the filing it came from and that filing's date
  inline XBRL    the filing's own HTML (ix:nonFraction): what company facts leaves out, which
                 is facts with dimensions (segments, geography) and company-specific concepts
                 (bkng:DeferredMerchantBookings)

Every fact keeps its filing's date (`filed`), so the store's AsOf decides what a run sees, and
a citable id (research/refs.py): xbrl:<T>:<taxonomy>:<concept>:<accession>:<start>/<end> or
ixbrl:<accession>:<fact id>.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup

from tenk_agent.research.refs import xbrl_id

STANDARD = ("us-gaap", "dei", "srt")  # taxonomies company facts carries
FORMS = ("10-K", "10-Q")


@dataclass
class Fact:
    id: str
    ticker: str
    taxonomy: str
    concept: str
    value: float
    unit: str
    start: str | None  # None for an instant (balance-sheet) fact
    end: str
    accession: str
    form: str
    filed: str
    fy: int | None = None
    fp: str | None = None
    dims: dict[str, str] = field(default_factory=dict)  # axis → member; {} = consolidated
    decimals: int | None = None
    source: str = "companyfacts"  # or "ixbrl"


def company_facts(ticker: str, data: dict, since: str) -> list[Fact]:
    """Facts from 10-Ks and 10-Qs filed on or after `since`, from a companyfacts JSON."""
    out = []
    for taxonomy, concepts in data.get("facts", {}).items():
        for concept, entry in concepts.items():
            for unit, rows in entry.get("units", {}).items():
                for r in rows:
                    if r.get("form") not in FORMS or r.get("filed", "") < since:
                        continue
                    out.append(
                        Fact(
                            id=xbrl_id(
                                ticker, taxonomy, concept, r["accn"], r["end"], r.get("start")
                            ),
                            ticker=ticker,
                            taxonomy=taxonomy,
                            concept=concept,
                            value=float(r["val"]),
                            unit=unit,
                            start=r.get("start"),
                            end=r["end"],
                            accession=r["accn"],
                            form=r["form"],
                            filed=r["filed"],
                            fy=r.get("fy"),
                            fp=r.get("fp"),
                        )
                    )
    return out


# --- inline XBRL ---------------------------------------------------------------------------

NUMBER = re.compile(r"[^\d.]")


def _number(element, text: str) -> float | None:
    """An ix:nonFraction's value: its text read per its format, times 10^scale, signed."""
    fmt = (element.get("format") or "").lower()
    if "zerodash" in fmt or "fixed-zero" in fmt or text.strip() in ("—", "-", "–"):
        value = 0.0
    else:
        if "numcommadecimal" in fmt.replace("-", ""):  # 1.234,5 style
            text = text.replace(".", "").replace(",", ".")
        digits = NUMBER.sub("", text.replace(",", ""))
        if not digits or digits == ".":
            return None
        value = float(digits)
    value *= 10 ** int(element.get("scale") or 0)
    return -value if element.get("sign") == "-" else value


def inline_facts(
    ticker: str, html: str, accession: str, form: str, filed: str, keep_standard: bool = False
) -> list[Fact]:
    """Facts from one filing's inline XBRL: those with dimensions, and those of company-specific
    concepts (keep_standard: every fact)."""
    soup = BeautifulSoup(html, "lxml")
    contexts = {}
    for cx in soup.find_all(re.compile(r"(^|:)context$")):
        period = cx.find(re.compile(r"(^|:)period$"))
        if period is None:
            continue

        def text(tag, period=period):
            found = period.find(re.compile(rf"(^|:){tag}$"))
            return found.get_text(strip=True) if found else None

        dims = {
            m.get("dimension"): m.get_text(strip=True)
            for m in cx.find_all(re.compile(r"(^|:)explicitmember$"))
        }
        start, end = text("startdate"), text("enddate") or text("instant")
        contexts[cx.get("id")] = (start, end, dims)
    units = {}
    for u in soup.find_all(re.compile(r"(^|:)unit$")):
        measures = [
            m.get_text(strip=True).split(":")[-1] for m in u.find_all(re.compile("measure$"))
        ]
        units[u.get("id")] = "/".join(measures) if measures else u.get("id")

    out = []
    for n, element in enumerate(soup.find_all(re.compile(r"(^|:)nonfraction$"))):
        name = element.get("name") or ""
        taxonomy, _, concept = name.partition(":")
        context = contexts.get(element.get("contextref"))
        if not concept or context is None:
            continue
        start, end, dims = context
        if not (keep_standard or dims or taxonomy not in STANDARD):
            continue
        value = _number(element, element.get_text(" ", strip=True))
        if value is None or end is None:
            continue
        decimals = element.get("decimals")
        out.append(
            Fact(
                id=f"ixbrl:{accession}:{element.get('id') or f'f{n}'}",
                ticker=ticker,
                taxonomy=taxonomy,
                concept=concept,
                value=value,
                unit=units.get(element.get("unitref"), element.get("unitref") or ""),
                start=start,
                end=end,
                accession=accession,
                form=form,
                filed=filed,
                dims=dims,
                decimals=int(decimals) if decimals and decimals.lstrip("-").isdigit() else None,
                source="ixbrl",
            )
        )
    return out


def filing_facts(folder: Path) -> list[Fact]:
    """Inline facts of one V4 filing folder (acquisition/filings.py), 10-K or 10-Q."""
    meta = json.loads((folder / "metadata.json").read_text())
    html = (folder / meta["documents"][0]["name"]).read_text(encoding="utf-8")
    return inline_facts(
        meta["ticker"], html, meta["accession_no"], meta["form"], meta["filing_date"]
    )
