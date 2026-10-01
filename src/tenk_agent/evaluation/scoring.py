"""Deterministic scoring of answers and agent traces against gold questions."""

import json

from tenk_agent import gold
from tenk_agent.agent import BUDGET_EXHAUSTED
from tenk_agent.store import Store
from tenk_agent.verify import numbers_in, unit_scale, verify_claim


def _absolute(value: float, unit: str | None) -> tuple[float, bool]:
    """(value in base units, whether the unit was understood)."""
    scale = unit_scale(unit)
    return (value * scale, True) if scale is not None else (value, False)


def _matches(expected: dict, claim_value, claim_unit, tolerance: float) -> bool:
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
            _matches(value, c["value"], c.get("unit"), tolerance)
            and (year is None or c.get("fiscal_year") in (None, year))
            and (company is None or not c.get("company") or _same_company(c["company"], company))
            for c in claims
        )
        if not hit and not claims:
            # Unstructured answer: fall back to numbers in the text (single values only).
            hit = len(expected) == 1 and any(
                _matches(value, n.value, _text_unit(n), tolerance)
                for n in numbers_in(answer.get("answer", ""))
            )
        matched.append(hit)
    return {"expected": len(expected), "matched": sum(matched), "all": all(matched)}


def _same_company(claimed: str, ticker: str) -> bool:
    from tenk_agent.corpus import resolve_company

    company = resolve_company(claimed)
    return company is not None and company.ticker == ticker


def _text_unit(number) -> str | None:
    if number.percent:
        return "percent"
    return {1e3: "USD thousands", 1e6: "USD millions", 1e9: "USD billions"}.get(number.scale)


def citation_accuracy(answer: dict, store: Store) -> dict:
    """Numeric claims whose cited chunks contain the value (or reproduce the calculation)."""
    numeric = [c for c in answer.get("claims", []) if c.get("value") is not None]
    accurate = sum(verify_claim(c, store)["status"] in ("verified", "calculated") for c in numeric)
    return {"numeric_claims": len(numeric), "accurate": accurate}


def agent_metrics(q: dict, trace: dict, reference: dict | None = None) -> dict:
    spans = [s for s in trace.get("spans", []) if s["type"] == "tool"]
    # Calls refused for exceeding the tool budget had valid arguments; they count as
    # unnecessary, not as incorrect calls.
    over_budget = [s for s in spans if s["is_error"] and BUDGET_EXHAUSTED in s["output"]]
    tools = [s for s in spans if s not in over_budget]
    used = {s["name"] for s in tools}
    expected = set(q.get("expected_tools") or [])
    signatures = [(s["name"], json.dumps(s["input"], sort_keys=True)) for s in tools]
    duplicates = len(signatures) - len(set(signatures))
    beyond = max(0, len(tools) - len(reference["steps"])) if reference else None
    return {
        "tool_calls": len(tools),
        "tools_used": sorted(used),
        # Tool selection: every tool a correct answer needs was used (none needed -> 1).
        "tool_selection": expected <= used,
        "valid_calls": sum(not s["is_error"] for s in tools),
        "duplicate_calls": duplicates,
        "over_budget_calls": len(over_budget),
        "calls_beyond_reference": beyond,
    }
