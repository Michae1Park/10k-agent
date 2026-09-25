"""The fixed evaluation corpus (PRD §6.1): 8 companies × fiscal years 2023–2025."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Company:
    ticker: str
    cik: int
    name: str


COMPANIES: tuple[Company, ...] = (
    Company("AAPL", 320193, "Apple Inc."),
    Company("MSFT", 789019, "Microsoft Corporation"),
    Company("AMZN", 1018724, "Amazon.com, Inc."),
    Company("GOOGL", 1652044, "Alphabet Inc."),
    Company("META", 1326801, "Meta Platforms, Inc."),
    Company("NVDA", 1045810, "NVIDIA Corporation"),
    Company("TSLA", 1318605, "Tesla, Inc."),
    Company("NFLX", 1065280, "Netflix, Inc."),
)

FISCAL_YEARS: tuple[int, ...] = (2023, 2024, 2025)


def company_by_ticker(ticker: str) -> Company:
    for company in COMPANIES:
        if company.ticker == ticker.upper():
            return company
    raise KeyError(f"{ticker} is not in the corpus")
