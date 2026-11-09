"""The reference grammar (V4 design doc §10.1)."""

import pytest

from tenk_agent.research.refs import Resolver, parse, xbrl_id
from test_agent_tools import ACCESSION, FACT, tools  # noqa: F401  (fixture)


@pytest.mark.parametrize(
    "ref_id, kind",
    [
        ("UBER-2026Q2-ER-EX99-005", "chunk"),
        ("AAPL-FY2025-8-107", "chunk"),
        ("0001543151-26-000032", "accession"),
        ("E-12", "evidence"),
        ("X-03", "evidence"),
        ("xbrl:UBER:us-gaap:Revenues:0001543151-26-000032:2026-04-01/2026-06-30", "xbrl"),
        ("xbrl:UBER:us-gaap:Revenues:0001543151-26-000032:2026-06-30", "xbrl"),
        ("ixbrl:0001543151-26-000032:f-123", "ixbrl"),
        ("q:UBER:revenue:2025Q4", "quarter"),
        ("kpi:UBER:gross_bookings.mobility:2026Q2", "kpi"),
        ("guide:UBER:adj_ebitda:2026Q3", "guide"),
        ("px:UBER:2026-08-05", "price"),
        ("macro:DGS10:2026-08-05", "macro"),
        ("model:UBER:2026-08-06:v3:adj_ebitda.FY2027", "model"),
        ("xbrl:UBER:Revenues", "malformed"),
    ],
)
def test_parse_kinds(ref_id, kind):
    assert parse(ref_id).kind == kind


def test_xbrl_id_names_a_durations_start():
    assert xbrl_id("U", "us-gaap", "Revenues", "a", "2026-06-30", "2026-04-01").endswith(
        ":a:2026-04-01/2026-06-30"
    )
    assert xbrl_id("U", "us-gaap", "Cash", "a", "2026-06-30").endswith(":a:2026-06-30")


def test_resolver_reads_v3_and_v4_xbrl_ids(tools):  # noqa: F811
    resolver = Resolver(tools.store, tools.sec)
    v3 = resolver.resolve(FACT)
    assert v3 and v3.fact["value"] == 34550000000
    start = "2024-09-29"  # the fixture's FY2025 R&D duration
    assert resolver.resolve(FACT.replace(":2025-09-27", f":{start}/2025-09-27")).fact == v3.fact
    assert resolver.resolve(FACT.replace(":2025-09-27", ":2000-01-01/2025-09-27")) is None
    assert resolver.resolve(ACCESSION).filing["form"] == "10-K"
    assert resolver.resolve("kpi:AAPL:x:2025Q1") is None  # not built yet
