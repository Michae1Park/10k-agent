from pathlib import Path

from tenk_agent.corpus import Company, companies_path, load

ROOT = Path(__file__).resolve().parents[1]


def test_v3_file_reproduces_the_old_hard_coded_corpus():
    companies, years = load(ROOT / "companies-v3.yaml")
    assert years == (2023, 2024, 2025)
    assert companies == (
        Company("AAPL", 320193, "Apple Inc.", ("Apple",)),
        Company("MSFT", 789019, "Microsoft Corporation", ("Microsoft",)),
        Company("AMZN", 1018724, "Amazon.com, Inc.", ("Amazon", "AWS", "Amazon Web Services")),
        Company("GOOGL", 1652044, "Alphabet Inc.", ("Alphabet", "Google", "GOOG", "YouTube")),
        Company("META", 1326801, "Meta Platforms, Inc.", ("Meta", "Facebook", "FB", "Instagram")),
        Company("NVDA", 1045810, "NVIDIA Corporation", ("NVIDIA", "Nvidia")),
        Company("TSLA", 1318605, "Tesla, Inc.", ("Tesla",)),
        Company("NFLX", 1065280, "Netflix, Inc.", ("Netflix",)),
    )


def test_v4_file_has_the_eight_and_no_fixed_years():
    companies, years = load(ROOT / "companies.yaml")
    assert years is None
    assert [c.ticker for c in companies] == [
        "BKNG", "EXPE", "ABNB", "TRIP", "UBER", "LYFT", "DASH", "CART"
    ]
    assert all(c.cik for c in companies)


def test_companies_path_prefers_env(monkeypatch):
    monkeypatch.setenv("TENK_COMPANIES", "companies.yaml")
    assert companies_path().name == "companies.yaml"
    monkeypatch.delenv("TENK_COMPANIES")
    assert companies_path().name == "companies-v3.yaml"
