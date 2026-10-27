"""XBRL facts and derived quarters (facts/xbrl.py, facts/quarters.py), on a synthetic company."""

import pytest

from tenk_agent.asof import AsOf
from tenk_agent.facts.quarters import checks, derive
from tenk_agent.facts.xbrl import company_facts, inline_facts
from tenk_agent.store import Store

FLOW = {"kind": "flow", "concepts": ["NetCashProvidedByUsedInOperatingActivities"]}
TWO = {
    "kind": "flow",
    "concepts": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
}


def row(start, end, val, accn, filed, form="10-Q"):
    """A company-facts row; `val` in USD millions."""
    return {"start": start, "end": end, "val": val * 1e6, "accn": accn, "filed": filed, "form": form}


def cf(concept, *rows):
    return {"facts": {"us-gaap": {concept: {"units": {"USD": list(rows)}}}}}


YEAR = [  # operating cash flow, year to date, as 10-Qs and a 10-K report it
    row("2025-01-01", "2025-03-31", 100, "q1", "2025-05-01"),
    row("2025-01-01", "2025-06-30", 250, "q2", "2025-08-01"),
    row("2025-01-01", "2025-09-30", 450, "q3", "2025-11-01"),
    row("2025-01-01", "2025-12-31", 700, "k", "2026-02-10", "10-K"),
]


def store_with(tmp_path, *payloads, asof="2026-10-27"):
    s = Store(tmp_path / "f.db", AsOf.today())
    s.enable_facts()
    for p in payloads:
        s.add_facts(company_facts("TEST", p, "2023-01-01"))
    return Store(tmp_path / "f.db", AsOf.of(asof))


def values(quarters):
    """Period → (value in USD millions, derivation)."""
    return {q.period: (q.value / 1e6, q.derivation) for q in quarters}


def test_quarters_from_year_to_date(tmp_path):
    s = store_with(tmp_path, cf("NetCashProvidedByUsedInOperatingActivities", *YEAR))
    qs = derive(s, "TEST", "ocf", FLOW)
    assert values(qs) == {
        "Q1": (100, "direct"),
        "Q2": (150, "ytd_diff"),
        "Q3": (200, "ytd_diff"),
        "Q4": (250, "fy_minus_9m"),
        "FY": (700, "direct"),
    }
    assert [c["status"] for c in checks(s, "TEST", "ocf", FLOW, qs)] == ["pass"]  # q4_ties
    q4 = next(q for q in qs if q.period == "Q4")
    assert q4.id == "q:TEST:ocf:2025Q4" and q4.filed == "2026-02-10" and len(q4.inputs) == 2


def test_leak_no_q4_before_the_10k(tmp_path):
    s = store_with(
        tmp_path, cf("NetCashProvidedByUsedInOperatingActivities", *YEAR), asof="2026-02-10"
    )
    assert "Q4" not in values(derive(s, "TEST", "ocf", FLOW))


def test_restatement_replaces_the_original_once_filed(tmp_path):
    restated = row("2025-01-01", "2025-03-31", 110, "q1-2026", "2026-05-01")
    payload = cf("NetCashProvidedByUsedInOperatingActivities", *YEAR, restated)
    before = store_with(tmp_path, payload, asof="2026-05-01")
    assert values(derive(before, "TEST", "ocf", FLOW))["Q1"][0] == 100
    after = Store(tmp_path / "f.db", AsOf.of("2026-05-02"))
    assert values(derive(after, "TEST", "ocf", FLOW))["Q1"][0] == 110


def test_one_concept_per_year(tmp_path):
    cash_flow = cf("ShareBasedCompensation", *YEAR)
    income = cf(
        "AllocatedShareBasedCompensationExpense",
        row("2025-04-01", "2025-06-30", 149, "q2", "2025-08-01"),
    )
    s = store_with(tmp_path, cash_flow, income)
    assert values(derive(s, "TEST", "sbc", TWO))["Q2"] == (150, "ytd_diff")  # not 149 mixed in


def test_inline_facts_keep_dimensions_and_company_concepts():
    html = """<html><body>
      <xbrli:context id="c1"><xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate>
        <xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period>
        <xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">uber:MobilityMember</xbrldi:explicitMember></xbrli:segment></xbrli:context>
      <xbrli:context id="c2"><xbrli:period><xbrli:instant>2026-06-30</xbrli:instant></xbrli:period></xbrli:context>
      <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
      <ix:nonFraction id="f1" name="us-gaap:Revenues" contextRef="c1" unitRef="usd" scale="6" decimals="-6">7,363</ix:nonFraction>
      <ix:nonFraction id="f2" name="bkng:DeferredMerchantBookings" contextRef="c2" unitRef="usd" scale="6">10,121</ix:nonFraction>
      <ix:nonFraction id="f3" name="us-gaap:Assets" contextRef="c2" unitRef="usd" scale="6">1</ix:nonFraction>
      <ix:nonFraction id="f4" name="us-gaap:OtherIncome" contextRef="c1" unitRef="usd" sign="-" format="ixt:fixed-zero">—</ix:nonFraction>
    </body></html>"""
    facts = {f.id.split(":")[-1]: f for f in inline_facts("T", html, "acc", "10-Q", "2026-08-05")}
    assert set(facts) == {"f1", "f2", "f4"}  # f3: standard and consolidated, company facts has it
    assert facts["f1"].value == pytest.approx(7363e6) and facts["f1"].dims == {
        "us-gaap:StatementBusinessSegmentsAxis": "uber:MobilityMember"
    }
    assert facts["f2"].start is None and facts["f2"].unit == "USD"
    assert facts["f4"].value == 0


def test_a_recast_year_reads_restated_not_fail(tmp_path):
    recast = [  # a later 10-Q re-reports H1 and Q2 on a new basis; Q1 and 9M are not yet
        row("2025-01-01", "2025-06-30", 220, "q2-2026", "2026-08-01"),
        row("2025-04-01", "2025-06-30", 130, "q2-2026", "2026-08-01"),
    ]
    s = store_with(tmp_path, cf("NetCashProvidedByUsedInOperatingActivities", *YEAR, *recast))
    qs = derive(s, "TEST", "ocf", FLOW)
    assert {c["status"] for c in checks(s, "TEST", "ocf", FLOW, qs)} == {"restated"}
    assert next(q for q in qs if q.period == "Q3").restated  # 9M − H1, H1 re-reported
    assert not next(q for q in qs if q.period == "Q1").restated
