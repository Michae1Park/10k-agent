"""Deterministic scoring of numeric answers against gold questions: expected values matched
within the question's tolerance, after unit normalization."""

from tenk_agent.corpus import resolve_company
from tenk_agent.evaluation import gold
from tenk_agent.generation.verify import numbers_in, unit_scale


def _absolute(value: float, unit: str | None) -> tuple[float, bool]:
    """(value in base units, whether the unit was understood)."""
    scale = unit_scale(unit)
    return (value * scale, True) if scale is not None else (value, False)


def matches(expected: dict, claim_value, claim_unit, tolerance: float) -> bool:
    try:
        value = float(str(claim_value).replace(",", "").replace("$", "").replace("%", ""))
    except ValueError:
        return False
    target, _ = _absolute(float(expected["value"]), expected["unit"])
    got, understood = _absolute(value, claim_unit)
    if not understood:  # unknown claim unit: compare as displayed, in the gold unit
        got = value * (unit_scale(expected["unit"]) or 1)
    if expected["unit"] == "percent":
        # Percentages: relative tolerance, or 0.05 points for small values.
        return abs(abs(got) - abs(target)) <= max(abs(target) * tolerance, 0.05)
    return abs(abs(got) - abs(target)) <= abs(target) * tolerance


def score_numbers(q: dict, answer: dict) -> dict | None:
    """Per expected value, whether a claim (right company and year, if stated) matches it."""
    expected = gold.expected_values(q)
    if not expected:
        return None
    tolerance = q.get("tolerance", 0.005)
    # A single-number question's company and year are its primary source's.
    source = q["sources"][0] if q.get("sources") else {}
    default_company = source.get("company")
    default_year = source.get("fiscal_year") if q.get("answer_type") == "number" else None
    claims = [c for c in answer.get("claims", []) if c.get("value") is not None]
    matched = []
    for value in expected:
        company = value.get("company", default_company)
        year = value.get("fiscal_year", default_year)
        hit = any(
            matches(value, c["value"], c.get("unit"), tolerance)
            and (year is None or c.get("fiscal_year") in (None, year))
            and (company is None or not c.get("company") or _same_company(c["company"], company))
            for c in claims
        )
        if not hit and not claims:
            # Unstructured answer: fall back to numbers in the text (single values only).
            hit = len(expected) == 1 and any(
                matches(value, n.value, text_unit(n), tolerance)
                for n in numbers_in(answer.get("answer", ""))
            )
        matched.append(hit)
    return {"expected": len(expected), "matched": sum(matched), "all": all(matched)}


def _same_company(claimed: str, ticker: str) -> bool:
    company = resolve_company(claimed)
    return company is not None and company.ticker == ticker


def text_unit(number) -> str | None:
    if number.percent:
        return "percent"
    return {1e3: "USD thousands", 1e6: "USD millions", 1e9: "USD billions"}.get(number.scale)
