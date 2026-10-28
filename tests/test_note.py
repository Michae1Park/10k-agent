"""The note's facts, number trace and lint (research/note/), with seeded errors (layer 5)."""

import pytest

from tenk_agent.research.note.facts import Facts
from tenk_agent.research.note.trace import check, figures
from tenk_agent.research.pipeline import lint


@pytest.fixture
def facts():
    f = Facts()
    f.add("2027E adj. EBITDA", 9712.3, "USD millions",
          "model:UBER:2026-08-06:base:adj_ebitda.FY2027")
    f.add("2027E adj. EBITDA change", 3.21, "percent", "computed")
    f.add("12-month price target", 95.0, "USD", "model:UBER:2026-08-06:val-base:price_target")
    f.add("UBER 2027E EV/EBITDA", 17.33, "x", "model:…")
    f.add("2026Q2 Trips", 3412.0, "count millions", "kpi:UBER:trips:2026Q2")
    f.add("2026Q2 Take rate y/y change", -0.4, "points", "computed")
    f.add("base scenario weight", 0.5, "weight", "research.yaml")
    return f


def test_figures_skip_dates_periods_and_ids():
    text = "As of 2026-08-06, 2026Q2 and FY2027 (2027E) per F-12 and X-03, Q3 guidance."
    assert figures(text) == []


@pytest.mark.parametrize("text", [
    "2027E adj. EBITDA of $9.7B, up +3.2%, PT $95.00 at 17.3x.",
    "Trips 3.41B; take rate -0.4 pts (-40 bps); base weight 50%.",
])
def test_true_figures_trace(facts, text):
    assert check(text, facts, "t") == []


@pytest.mark.parametrize("seeded", [
    "2027E adj. EBITDA of $9.8B",  # a corrupted fact
    "PT $96.00",
    "17.9x 2027E EV/EBITDA",
    "trips 3.51B",
    "a new number: 12.5%",  # a number computed by the writer
])
def test_seeded_errors_are_caught(facts, seeded):
    assert check(seeded, facts, "t")


def test_lint_reads_tables_and_prose(facts):
    note = ("# UBER\n\nAs of 2026-08-06\n\n## 1. EXECUTIVE SUMMARY\n\n"
            "| PT | Multiple |\n|---|---|\n| $95.00 | 17.3x |\n\nThe PT is $95.00.\n")
    assert lint(note, facts) == []
    corrupted = note.replace("| $95.00 | 17.3x |", "| $95.00 | 18.3x |")  # a table cell
    assert [u.text for u in lint(corrupted, facts)] == ["18.3x"]


def test_fact_text(facts):
    assert [f.text() for f in facts.items] == ["$9.7B", "+3.2%", "$95.00", "17.3x", "3.41B",
                                               "-0.4 pts", "50%"]
