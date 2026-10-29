"""V4 acquisition (acquisition/filings.py): periods, releases, exhibits."""

from tenk_agent.acquisition.filings import (
    FilingV4,
    exhibits,
    periods_by_accession,
    quarter_before,
    resolve,
)
from tenk_agent.corpus import Company

CO = Company("TEST", 1, "Test Corp")


def facts(*rows):
    """Company facts from (accession, form, fy, fp) rows."""
    units = [{"accn": a, "form": f, "fy": fy, "fp": fp, "val": 1} for a, f, fy, fp in rows]
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": units}}}}}


def row(form, accession, filed, report="", items=""):
    return {
        "form": form,
        "accessionNumber": accession,
        "filingDate": filed,
        "reportDate": report,
        "items": items,
        "acceptanceDateTime": f"{filed}T20:00:00.000Z",
    }


def test_quarter_before_is_strict():
    assert quarter_before("2026-08-05")[1:] == (2026, 2)
    assert quarter_before("2026-02-04")[1:] == (2025, 4)
    assert quarter_before("2026-07-01")[1:] == (2026, 2)
    assert quarter_before("2026-06-30")[1:] == (2026, 1)  # filed on the quarter end itself


def test_periods_by_accession_takes_the_most_common_tag():
    f = facts(("a", "10-Q", 2026, "Q2"), ("a", "10-Q", 2026, "Q2"), ("a", "10-Q", 2025, "Q2"))
    assert periods_by_accession(f) == {"a": (2026, "Q2")}


def test_resolve_names_periods_and_checks_releases_against_reports():
    rows = [
        row("10-Q", "q2", "2026-08-05", "2026-06-30"),
        row("8-K", "er2", "2026-08-05", items="2.02,9.01"),
        row("8-K", "er-orphan", "2026-05-06", items="2.02"),  # no Q1 10-Q in this window
        row("8-K", "other", "2026-06-01", items="5.07"),
        row("10-Q/A", "amend", "2026-09-01", "2026-06-30"),
    ]
    filings, problems = resolve(CO, rows, facts(("q2", "10-Q", 2026, "Q2")))
    assert [(f.form, f.period, f.folder) for f in filings] == [
        ("10-Q", "2026Q2", "2026Q2-10Q"),
        ("ER", "2026Q1", "2026Q1-ER"),
        ("ER", "2026Q2", "2026Q2-ER"),
    ]
    assert len(problems) == 1 and "er-orphan" in problems[0]


def test_resolve_flags_two_filings_for_one_period():
    rows = [
        row("8-K", "a", "2026-08-05", items="2.02"),
        row("8-K", "b", "2026-08-20", items="2.02"),
    ]
    filings, problems = resolve(CO, rows, facts())
    assert [f.accession_no for f in filings] == ["a"]  # the first stays
    assert any("2 filings" in p for p in problems)


def test_ten_k_period_key():
    f = FilingV4("T", 1, "10-K", "a", "2026-02-13", "", "2025-12-31", 2025, "FY")
    assert (f.period, f.folder) == ("FY2025", "FY2025-10K")


def test_exhibits_reads_ex99_rows_from_the_index():
    html = """<table class="tableFile">
      <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th></tr>
      <tr><td>1</td><td>8-K</td><td><a href="/Archives/x/k.htm">k.htm</a></td><td>8-K</td></tr>
      <tr><td>2</td><td>R</td><td><a href="/Archives/x/r.htm">r.htm</a></td><td>EX-99.1</td></tr>
      <tr><td>3</td><td>L</td><td><a href="/Archives/x/l.htm">l.htm</a></td><td>EX-99.2</td></tr>
    </table>"""
    docs = exhibits(html, "https://base/")
    assert [(d.name, d.type, d.url) for d in docs] == [
        ("r.htm", "EX-99.1", "https://base/r.htm"),
        ("l.htm", "EX-99.2", "https://base/l.htm"),
    ]


def test_resolve_keys_the_annual_proxy_to_the_prior_fiscal_year():
    rows = [
        row("DEF 14A", "px", "2026-04-20"),
        row("DEF 14A", "special", "2026-10-01"),  # a special meeting: skipped, not a problem
        row("DEFA14A", "extra", "2026-04-25"),
        row("DEFC14A", "contest", "2025-04-24"),
    ]
    filings, problems = resolve(CO, rows, facts())
    assert [(f.accession_no, f.period, f.folder, f.period_end_date) for f in filings] == [
        ("contest", "FY2024", "FY2024-DEF14A", "2024-12-31"),
        ("px", "FY2025", "FY2025-DEF14A", "2025-12-31")
    ]
    assert problems == []
