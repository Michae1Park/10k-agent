"""The model's formula language (research/model/dsl.py): parse, evaluate, Excel."""

import pytest

from tenk_agent.research.model.dsl import Cells, DslError, Readers, evaluate, parse, to_excel

LINES = {("gb", 0): 100.0, ("tr", 0): 0.25, ("trips", 4): 50.0, ("g", 0): 0.1, ("x", 0): None}
ROWS = {"gb": 10, "tr": 11, "trips": 12, "g": 13, "revenue": 14}


def read(fy=None):
    return Readers(line=lambda n, k: LINES.get((n, k)), fy=lambda n: fy,
                   assumption=lambda n: {"fade": 0.9}.get(n))


# Column F is this quarter; a lag of k quarters is k columns left; fy spans C:F.
CELLS = Cells(cell=lambda n, k: f"{'FEDCB'[k]}{ROWS[n]}" if k < 5 else None,
              fy=lambda n: f"C{ROWS[n]}:F{ROWS[n]}", assumption=lambda n: f"Assumptions!$C${4}")


@pytest.mark.parametrize("text, value, formula", [
    ("gb * tr", 25.0, "=F10*F11"),
    ("trips[-4] * (1 + g)", 55.0, "=B12*(1+F13)"),
    ("gb * @fade", 90.0, "=F10*Assumptions!$C$4"),
    ("max(tr, 0.3) - min(1, 2)", -0.7, "=MAX(F11,0.3)-MIN(1,2)"),
    ("if_(gb > 50, gb, 0)", 100.0, "=IF(F10>50,F10,0)"),
    ("-gb / 4", -25.0, "=-F10/4"),
])
def test_one_expression_two_evaluators(text, value, formula):
    expr = parse(text)
    assert evaluate(expr, read()) == pytest.approx(value)
    assert to_excel(expr, CELLS) == formula


def test_fy_sums_the_years_quarters():
    expr = parse("fy(revenue)")
    assert evaluate(expr, read(fy=400.0)) == 400.0
    assert to_excel(expr, CELLS) == "=SUM(C14:F14)"
    assert expr.lines == {"revenue"} and expr.same_period == set()


def test_missing_inputs_give_none_not_zero():
    assert evaluate(parse("gb * x"), read()) is None
    assert evaluate(parse("gb / (tr - tr)"), read()) is None
    assert to_excel(parse("gb[-9]"), CELLS) is None  # before the first column


def test_dependencies():
    expr = parse("gb * tr + trips[-4] + @fade")
    assert expr.lines == {"gb", "tr", "trips"}
    assert expr.same_period == {"gb", "tr"}
    assert expr.assumptions == {"fade"}
    assert parse("x * @take_rate.mobility").assumptions == {"take_rate.mobility"}


@pytest.mark.parametrize("text", [
    "__import__('os')", "gb.real", "gb[4]", "gb[-1.5]", "'a'", "fy(gb, tr)", "sum(gb)",
    "lambda: 1", "gb if tr else 0", "gb +",
])
def test_rejects_anything_else(text):
    with pytest.raises(DslError):
        parse(text)
