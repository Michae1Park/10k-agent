import pytest

from tenk_agent.corpus import Company
from tenk_agent.edgar import (
    EdgarClient,
    EdgarError,
    annual_reports,
    fiscal_years_by_accession,
    resolve_filings,
)

COMPANY = Company("TEST", 12345, "Test Corp")


def page(*rows):
    """Build a submissions page from (form, accession, filing_date, report_date) rows."""
    return {
        "form": [r[0] for r in rows],
        "accessionNumber": [r[1] for r in rows],
        "filingDate": [r[2] for r in rows],
        "reportDate": [r[3] for r in rows],
        "primaryDocument": [f"doc-{r[1]}.htm" for r in rows],
    }


def submissions(*rows):
    return [page(*rows)]


def company_facts(fy_by_accession):
    facts = [{"accn": accn, "fy": fy, "form": "10-K"} for accn, fy in fy_by_accession.items()]
    return {"facts": {"dei": {"EntityPublicFloat": {"units": {"USD": facts}}}}}


def test_annual_reports_keeps_only_original_10ks_newest_first():
    subs = submissions(
        ("10-Q", "a-1", "2025-05-01", "2025-03-31"),
        ("10-K", "a-2", "2024-02-01", "2023-12-31"),
        ("10-K/A", "a-3", "2025-04-01", "2024-12-31"),
        ("10-K", "a-4", "2025-02-01", "2024-12-31"),
    )
    assert [r["accessionNumber"] for r in annual_reports(subs)] == ["a-4", "a-2"]


def test_annual_reports_merges_older_pages_without_duplicates():
    recent = page(("10-K", "a-2", "2025-02-01", "2024-12-31"))
    older = page(
        ("10-K", "a-1", "2024-02-01", "2023-12-31"),
        ("10-K", "a-2", "2025-02-01", "2024-12-31"),
    )
    assert [r["accessionNumber"] for r in annual_reports([recent, older])] == ["a-2", "a-1"]


def test_fiscal_years_come_from_10k_public_float_facts():
    facts = company_facts({"a-1": 2025})
    facts["facts"]["dei"]["EntityPublicFloat"]["units"]["USD"].append(
        {"accn": "q-1", "fy": 2025, "form": "10-Q"}
    )
    assert fiscal_years_by_accession(facts) == {"a-1": 2025}


def test_resolve_prefers_xbrl_fiscal_year_over_period_end():
    # A January year-end labeled as the prior year, like some retailers.
    subs = submissions(("10-K", "a-1", "2025-03-20", "2025-02-01"))
    [filing] = resolve_filings(COMPANY, subs, company_facts({"a-1": 2024}), (2024,))
    assert filing.fiscal_year == 2024
    assert filing.source_url == "https://www.sec.gov/Archives/edgar/data/12345/a1/doc-a-1.htm"


def test_resolve_falls_back_to_period_end_year():
    subs = submissions(("10-K", "a-1", "2025-01-28", "2024-12-31"))
    [filing] = resolve_filings(COMPANY, subs, company_facts({}), (2024,))
    assert filing.fiscal_year == 2024


def test_resolve_raises_when_a_year_is_missing():
    subs = submissions(("10-K", "a-1", "2025-01-28", "2024-12-31"))
    with pytest.raises(EdgarError, match="FY2023"):
        resolve_filings(COMPANY, subs, company_facts({}), (2023, 2024))


def test_resolve_raises_on_duplicate_filings_for_a_year():
    subs = submissions(
        ("10-K", "a-1", "2025-01-28", "2024-12-31"),
        ("10-K", "a-2", "2025-02-15", "2024-12-31"),
    )
    with pytest.raises(EdgarError, match="expected one 10-K"):
        resolve_filings(COMPANY, subs, company_facts({}), (2024,))


def test_client_requires_contact_in_user_agent():
    with pytest.raises(EdgarError, match="User-Agent"):
        EdgarClient("anonymous")
