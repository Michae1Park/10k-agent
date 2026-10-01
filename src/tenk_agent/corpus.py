"""The fixed corpus (docs/DECISIONS.md D-001): 8 companies × fiscal years 2023–2025."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Company:
    ticker: str
    cik: int
    name: str
    aliases: tuple[str, ...] = ()


COMPANIES: tuple[Company, ...] = (
    Company("AAPL", 320193, "Apple Inc.", ("Apple",)),
    Company("MSFT", 789019, "Microsoft Corporation", ("Microsoft",)),
    Company("AMZN", 1018724, "Amazon.com, Inc.", ("Amazon", "AWS", "Amazon Web Services")),
    Company("GOOGL", 1652044, "Alphabet Inc.", ("Alphabet", "Google", "GOOG", "YouTube")),
    Company("META", 1326801, "Meta Platforms, Inc.", ("Meta", "Facebook", "FB", "Instagram")),
    Company("NVDA", 1045810, "NVIDIA Corporation", ("NVIDIA", "Nvidia")),
    Company("TSLA", 1318605, "Tesla, Inc.", ("Tesla",)),
    Company("NFLX", 1065280, "Netflix, Inc.", ("Netflix",)),
)

FISCAL_YEARS: tuple[int, ...] = (2023, 2024, 2025)


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
