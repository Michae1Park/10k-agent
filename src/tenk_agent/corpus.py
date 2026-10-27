"""The corpus: companies and fiscal years from a companies file (design doc §14.1).

$TENK_COMPANIES names the file, default companies-v3.yaml: the V0–V3 corpus (D-001),
8 companies × fiscal years 2023–2025. companies.yaml is the V4 coverage (D-038). A relative
path is read from the working directory, else the repo root.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_FILE = "companies-v3.yaml"


@dataclass(frozen=True)
class Company:
    ticker: str
    cik: int
    name: str
    aliases: tuple[str, ...] = ()


def companies_path(name: str | None = None) -> Path:
    path = Path(name or os.environ.get("TENK_COMPANIES") or DEFAULT_FILE)
    if path.is_absolute() or path.exists():
        return path
    return Path(__file__).resolve().parents[2] / path


def load(path: Path) -> tuple[tuple[Company, ...], tuple[int, ...] | None]:
    """The companies in `path` and its fiscal years (None: every period up to the as-of)."""
    data = yaml.safe_load(path.read_text())
    companies = tuple(
        Company(ticker, entry["cik"], entry["name"], tuple(entry.get("aliases", ())))
        for ticker, entry in data["companies"].items()
    )
    years = data.get("fiscal_years")
    return companies, tuple(years) if years else None


COMPANIES, FISCAL_YEARS = load(companies_path())
_FILE = yaml.safe_load(companies_path().read_text())
DATA_DIR: str | None = _FILE.get("data_dir")  # None: config.yaml's data_dir (V3)
WINDOW_START: str = str(_FILE.get("window", {}).get("since", "2023-01-01"))


def company_by_ticker(ticker: str) -> Company:
    for company in COMPANIES:
        if company.ticker == ticker.upper():
            return company
    raise KeyError(f"{ticker} is not in the corpus")


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower().removesuffix("'s"))


def resolve_company(name: str) -> Company | None:
    """A ticker, legal name or alias ("Google", "Facebook") -> corpus company; None if absent."""
    key = _key(name)
    for company in COMPANIES:
        names = (company.ticker, company.name, *company.aliases)
        if key in {_key(n) for n in names} or key == _key(company.name.split(",")[0]):
            return company
    return None
