import pytest

from tenk_agent.calculator import CalculatorError, calculate
from tenk_agent.store import ChunkRecord
from tenk_agent.verify import find_text, find_value, numbers_in


def chunk(text: str, units: str | None = None) -> ChunkRecord:
    return ChunkRecord("T-FY2024-8-001", "AAPL", 2024, "8", "table", text, units, [2024])


def test_named_operations_show_their_formula():
    result = calculate("pct_change", inputs={"old": 29915, "new": 31370})
    assert result.result == pytest.approx(4.8638, abs=1e-4)
    assert result.formula == "(new - old) / old * 100"
    assert calculate("cagr", inputs={"start": 100, "end": 121, "years": 2}).result == pytest.approx(
        10.0
    )
    assert calculate("sum", inputs={"a": 1, "b": 2, "c": 3}).result == 6


def test_expressions_accept_formatted_numbers_and_parentheses_as_negatives():
    result = calculate(expression="a / b * 100", inputs={"a": "85,200", "b": "$383,285"})
    assert result.result == pytest.approx(22.229, abs=1e-3)
    assert calculate(expression="a + b", inputs={"a": "(3,105)", "b": 5}).result == -3100


def test_calculator_rejects_unsafe_or_invalid_input():
    with pytest.raises(CalculatorError):
        calculate(expression="__import__('os').system('ls')", inputs={})
    with pytest.raises(CalculatorError):
        calculate(expression="a / 0", inputs={"a": 1})
    with pytest.raises(CalculatorError):
        calculate("pct_change", inputs={"old": 1})
    with pytest.raises(CalculatorError):
        calculate(expression="a ** 0.5", inputs={"a": -4})


def test_numbers_in_reads_signs_scales_and_percentages():
    numbers = numbers_in("Net loss was (1,234) and revenue $1.2 billion, up 12.5%.")
    assert [(n.value, n.scale, n.percent) for n in numbers] == [
        (-1234.0, None, False),
        (1.2, 1e9, False),
        (12.5, None, True),
    ]


def test_find_value_normalizes_units():
    table = chunk("| Research and development | 31,370 | 29,915 |", units="USD millions")
    assert find_value(31370, "USD millions", table) == "31,370"
    assert find_value(31.37, "USD billions", table) == "31,370"
    assert find_value(31370000000, "USD", table) == "31,370"
    assert find_value(31.4, "USD billions", table) == "31,370"  # rounded claim, written precision
    assert find_value(31500, "USD millions", table) is None  # close, but a different figure
    assert find_value(29916, "USD millions", table) is None


def test_find_value_respects_rounding_of_prose_figures():
    prose = chunk("Revenue grew to $1.2 billion in 2024.")
    assert find_value(1234, "USD millions", prose) == "$1.2 billion"
    assert find_value(1400, "USD millions", prose) is None


def test_find_value_matches_percentages_as_displayed():
    prose = chunk("Services represented 26.2% of total net sales.")
    assert find_value(26.2, "percent", prose) == "26.2%"
    assert find_value(22.2, "percent", prose) is None


def test_find_text_ignores_whitespace_case_and_table_pipes():
    table = chunk("| Total net sales | 391,035 | 383,285 |")
    assert find_text("total net sales 391,035", table)
    assert not find_text("total net sales 416,161", table)
