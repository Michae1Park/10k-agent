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


def test_form4_parse():
    from tenk_agent.acquisition.insiders import parse

    xml = b"""<ownershipDocument><reportingOwner><reportingOwnerId><rptOwnerName>Doe Jane
    </rptOwnerName></reportingOwnerId><reportingOwnerRelationship><isOfficer>1</isOfficer>
    <officerTitle>CFO</officerTitle></reportingOwnerRelationship></reportingOwner>
    <nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>2026-05-01</value>
    </transactionDate><transactionCoding><transactionCode>S</transactionCode></transactionCoding>
    <transactionAmounts><transactionShares><value>1000</value></transactionShares>
    <transactionPricePerShare><value>70.5</value></transactionPricePerShare>
    <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
    </transactionAmounts></nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""
    rows = parse(xml, "UBER", "0000000000-26-000001", "2026-05-03")
    assert rows == [("UBER", "0000000000-26-000001", "2026-05-03", "Doe Jane", 0, 1, "CFO",
                     "2026-05-01", "S", 1000.0, 70.5, "D")]


def test_a_table_cell_changed_to_another_facts_value_is_caught(facts):
    table = "| PT | Multiple |\n|---|---|\n| $95.00 | 17.3x |"
    note = f"# UBER\n\n## 1. EXECUTIVE SUMMARY\n\n{table}\n"
    assert lint(note, facts, [table]) == []
    swapped = note.replace("| $95.00 | 17.3x |", "| $95.00 | 3.2x |")  # 3.2: another fact
    assert [u.text for u in lint(swapped, facts, [table])] == ["block changed"]


def test_negative_money_prints_its_sign_first():
    f = Facts()
    assert f.add("x", -7.0, "USD millions", "s").text() == "-$7M"
    assert f.add("y", -0.5, "USD", "s").text() == "-$0.50"
    assert figures("-$7M") == [("-$7M", -7.0, "money_millions")]


def test_prose_traces_to_its_own_slot_only(facts):
    slot = {"key": "valuation", "text": "The PT is $95.00.", "facts": ["F-3"]}
    note = "# UBER\n\n## 1. EXECUTIVE SUMMARY\n\nThe PT is $95.00.\n"
    assert lint(note, facts, [], [slot]) == []
    other = {"key": "valuation", "text": "The multiple is 17.3x.", "facts": ["F-3"]}  # F-4's
    note2 = note.replace("The PT is $95.00.", "The multiple is 17.3x.")
    assert [u.text for u in lint(note2, facts, [], [other])] == ["17.3x"]


def test_writer_drops_sentences_still_untraced_after_repair(facts):
    from types import SimpleNamespace

    from tenk_agent.research.note.sections import Section, Slot
    from tenk_agent.research.note.write import fill

    class Writer:  # keeps the bad figure on the repair turn too
        def complete(self, messages, system, max_tokens):
            return SimpleNamespace(
                text='{"text": "EBITDA is $9.7B. Bookings grew 20% constant currency. PT $95.00."}')

    slot = Slot("s", "write", [f.id for f in facts.items])
    section = Section(1, "Summary", [slot])
    exceptions = fill([section], facts, Writer())
    assert slot.text == "EBITDA is $9.7B. PT $95.00."
    assert len(exceptions) == 1 and "'20%'" in exceptions[0]["item"]
