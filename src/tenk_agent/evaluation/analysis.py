"""Error analysis: why each question failed, where failures cluster, what changed between runs.

`failure_cause` gives every unsuccessful row one cause, checked in pipeline order, so the
first broken stage is blamed (a retrieval miss isn't also counted as a reading error):

    error             the run crashed on this question
    format            the final answer wasn't valid JSON
    false abstention  declined a question the corpus answers
    unsupported       answered a question it should have declined
    retrieval miss    the agent never saw a chunk holding the answer
    over budget       ran out of tool calls before finishing
    wrong value       saw the evidence but reported a wrong or missing number
    citation          right number, but cited a chunk that doesn't show it
"""

import json
from collections import Counter

from tenk_agent.evaluation.runner import RUNS_DIR

CAUSES = (
    "error",
    "format",
    "false abstention",
    "unsupported",
    "retrieval miss",
    "over budget",
    "wrong value",
    "citation",
)


def load_rows(label: str) -> list[dict]:
    path = RUNS_DIR / label / "results.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_config(label: str) -> dict:
    return json.loads((RUNS_DIR / label / "config.json").read_text())


def succeeded(row: dict) -> bool | None:
    """Task completion, with errors as failures; None when it can't be scored (no judge)."""
    if row.get("error"):
        return False
    complete = row.get("task_complete")
    return None if complete is None else bool(complete)


def failure_cause(row: dict) -> str | None:
    """The first stage that broke, or None for a success or an unscorable row."""
    if row.get("error"):
        return "error"
    ok = succeeded(row)
    if ok is None:
        return None
    citations = row.get("citations") or {}
    clean_citations = citations.get("accurate", 0) == citations.get("numeric_claims", 0)
    if ok:
        return None if clean_citations else "citation"
    if row.get("format_error"):
        return "format"
    if row["should_abstain"]:
        return "unsupported"
    if row.get("abstained"):
        return "false abstention"
    if row.get("evidence_recall") == 0 or row.get("recall@5") == 0:
        return "retrieval miss"
    if (row.get("agent") or {}).get("over_budget_calls"):
        return "over budget"
    return "wrong value"


def causes(rows: list[dict]) -> Counter:
    return Counter(c for c in map(failure_cause, rows) if c)


def slices(rows: list[dict], key: str) -> dict[str, list[dict]]:
    """Rows grouped by a field (category, failure_mode, answer_type, ...)."""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(str(row.get(key) or "none"), []).append(row)
    return dict(sorted(groups.items()))


def transitions(base: list[dict], candidate: list[dict]) -> list[dict]:
    """Per question in both runs: pass/fail in each and the candidate's failure cause."""
    by_id = {r["id"]: r for r in base}
    out = []
    for row in candidate:
        if row["id"] not in by_id:
            continue
        b, c = succeeded(by_id[row["id"]]), succeeded(row)
        if b is None or c is None:
            change = "unscored"
        elif b == c:
            change = "both pass" if c else "both fail"
        else:
            change = "fixed" if c else "regressed"
        out.append(
            {
                "id": row["id"],
                "category": row["category"],
                "failure_mode": row.get("failure_mode"),
                "change": change,
                "base_cause": failure_cause(by_id[row["id"]]),
                "candidate_cause": failure_cause(row),
            }
        )
    order = {"regressed": 0, "fixed": 1, "both fail": 2, "unscored": 3, "both pass": 4}
    return sorted(out, key=lambda t: (order[t["change"]], t["id"]))
