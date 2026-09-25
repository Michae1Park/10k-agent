"""Gold dataset tooling: validation, evidence-to-chunk resolution and XBRL cross-checks.

Rules for the dataset itself are in docs/eval/gold-set-guide.md.
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path

from tenk_agent.corpus import COMPANIES, FISCAL_YEARS
from tenk_agent.store import Store

GOLD_PATH = Path("eval/gold/questions.jsonl")

CATEGORIES = {
    "retrieval",
    "grounding",
    "multi_document",
    "tool_use",
    "agent_workflow",
    "failure_case",
}
FAILURE_MODES = {
    "ambiguous_fiscal_year",
    "shifted_fiscal_year",
    "similar_numbers",
    "unit_normalization",
    "entity_naming",
    "unsupported",
    "false_premise",
}
ANSWER_TYPES = {"number", "number_set", "text", "abstain"}
UNITS = {
    "USD millions",
    "USD thousands",
    "USD",
    "percent",
    "shares millions",
    "count",
    "count thousands",
}
UNIT_SCALE = {"USD millions": 1e6, "USD thousands": 1e3, "USD": 1}
REQUIRED_FIELDS = ("id", "split", "category", "question", "answer_type", "sources", "origin")
TICKERS = {c.ticker for c in COMPANIES}


def load(path: Path = GOLD_PATH) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# Field order in the file, matching the record format in the authoring guide.
FIELD_ORDER = (
    "id",
    "split",
    "category",
    "failure_mode",
    "question",
    "answer_type",
    "expected_answer",
    "tolerance",
    "required_points",
    "should_abstain",
    "sources",
    "acceptable_sources",
    "expected_tools",
    "notes",
    "origin",
    "verified_by",
    "verified_on",
)


def save(questions: list[dict], path: Path = GOLD_PATH) -> None:
    ordered = [{field: q[field] for field in FIELD_ORDER if field in q} | q for q in questions]
    path.write_text("".join(json.dumps(q) + "\n" for q in ordered))


def mark_verified(questions: list[dict], ids: list[str], by: str, on: date) -> list[str]:
    """Record a reviewer's sign-off on the given questions; returns IDs that don't exist."""
    by_id = {q["id"]: q for q in questions}
    for qid in ids:
        if qid in by_id:
            by_id[qid]["verified_by"] = by
            by_id[qid]["verified_on"] = on.isoformat()
    return [qid for qid in ids if qid not in by_id]


def is_verified(question: dict) -> bool:
    return bool(question.get("verified_by")) and bool(question.get("verified_on"))


def validate(questions: list[dict]) -> list[str]:
    """Structural problems, one message each; empty when the dataset is well formed."""
    problems = []
    seen = set()
    for q in questions:
        qid = q.get("id", "<missing id>")
        problems += [f"{qid}: {p}" for p in _validate_one(q)]
        if qid in seen:
            problems.append(f"{qid}: duplicate id")
        seen.add(qid)
    return problems


def _validate_one(q: dict) -> list[str]:
    problems = [
        f"missing {field}"
        for field in REQUIRED_FIELDS
        if not q.get(field) and not (field == "sources" and q.get("should_abstain"))
    ]
    if q.get("split") not in {"dev", "test"}:
        problems.append(f"split must be dev or test, not {q.get('split')!r}")
    if q.get("category") not in CATEGORIES:
        problems.append(f"unknown category {q.get('category')!r}")
    if (q.get("category") == "failure_case") != (q.get("failure_mode") is not None):
        problems.append("failure_mode is required for failure cases and only for them")
    elif q.get("failure_mode") and q["failure_mode"] not in FAILURE_MODES:
        problems.append(f"unknown failure_mode {q['failure_mode']!r}")
    if q.get("answer_type") not in ANSWER_TYPES:
        problems.append(f"unknown answer_type {q.get('answer_type')!r}")
    if q.get("answer_type") == "text" and not q.get("required_points"):
        problems.append("text answers need required_points")
    if (q.get("answer_type") == "abstain") != bool(q.get("should_abstain")):
        problems.append("answer_type abstain and should_abstain must agree")
    for value in expected_values(q):
        if value.get("unit") not in UNITS:
            problems.append(f"unknown unit {value.get('unit')!r}")
    for source in q.get("sources", []) + q.get("acceptable_sources", []):
        if source.get("company") not in TICKERS or source.get("fiscal_year") not in FISCAL_YEARS:
            problems.append(
                f"source outside the corpus: {source.get('company')} {source.get('fiscal_year')}"
            )
        if not source.get("evidence"):
            problems.append("source without evidence")
    return problems


def expected_values(q: dict) -> list[dict]:
    """Numeric expected answers as a list (a number answer becomes a one-item list)."""
    answer = q.get("expected_answer")
    if q.get("answer_type") == "number" and answer:
        return [answer]
    if q.get("answer_type") == "number_set" and answer:
        return answer
    return []


def normalize(text: str) -> str:
    """Comparison form for evidence: case, whitespace, '$' and table pipes don't matter."""
    return re.sub(r"[\s|$]+", " ", text).strip().lower()


def resolve_evidence(store: Store, q: dict) -> list[list[str]]:
    """For each source (primary, then acceptable), the IDs of chunks containing its evidence."""
    resolved = []
    for source in q.get("sources", []) + q.get("acceptable_sources", []):
        evidence = normalize(source["evidence"])
        chunks = store.chunks(source["company"], source["fiscal_year"])
        resolved.append([c.id for c in chunks if evidence in normalize(c.text)])
    return resolved


@dataclass
class XbrlCheck:
    value: float
    unit: str
    matched_concept: str | None  # None when no XBRL fact has this value for the period


def xbrl_checks(q: dict, data_dir: Path) -> list[XbrlCheck]:
    """Look for an XBRL fact equal to each USD expected value, for that company's fiscal year.

    A match is strong evidence the gold value is transcribed correctly; a miss means the
    value needs a closer look (it may be a non-standard line item, which is fine).
    """
    checks = []
    for value in expected_values(q):
        scale = UNIT_SCALE.get(value.get("unit"))
        source = _source_for(q, value)
        if scale is None or source is None:
            continue
        facts = _annual_usd_facts(data_dir, source["company"])
        period_end = _period_end(data_dir, source["company"], source["fiscal_year"])
        target = value["value"] * scale
        tolerance = max(abs(target) * q.get("tolerance", 0.005), scale / 2)
        concept = next(
            (
                name
                for name, end, amount in facts
                if end == period_end and abs(abs(amount) - abs(target)) <= tolerance
            ),
            None,
        )
        checks.append(XbrlCheck(value["value"], value["unit"], concept))
    return checks


def _source_for(q: dict, value: dict) -> dict | None:
    """The first source matching the company and fiscal year a value names (if it names them)."""
    company, year = value.get("company"), value.get("fiscal_year")
    for source in q.get("sources", []):
        if company in (None, source["company"]) and year in (None, source["fiscal_year"]):
            return source
    return None


@cache
def _annual_usd_facts(data_dir: Path, ticker: str) -> list[tuple[str, str, float]]:
    """(concept, period end, value) for every annual or point-in-time USD fact in a 10-K."""
    facts = json.loads((data_dir / "raw" / "edgar" / ticker / "companyfacts.json").read_text())
    return [
        (f"{taxonomy}:{name}", fact["end"], fact["val"])
        for taxonomy, concepts in facts["facts"].items()
        for name, concept in concepts.items()
        for fact in concept.get("units", {}).get("USD", [])
        if fact.get("form") == "10-K" and _is_annual_or_instant(fact)
    ]


def _is_annual_or_instant(fact: dict) -> bool:
    if "start" not in fact:
        return True
    days = (date.fromisoformat(fact["end"]) - date.fromisoformat(fact["start"])).days
    return 350 <= days <= 380


@cache
def _period_end(data_dir: Path, ticker: str, fiscal_year: int) -> str:
    manifest = json.loads((data_dir / "raw" / "manifest.json").read_text())
    return next(f["period_end_date"] for f in manifest[ticker] if f["fiscal_year"] == fiscal_year)
