"""V2 metrics: is the answer right, is it grounded, and does it decline when it should.
Spec: experiments/V2_GENERATION.md §4.

Numbers are scored by code (tenk_agent.evaluation.scoring.score_numbers: each expected value
matched by a claim for the right company and fiscal year, within the tolerance). Text answers
are scored by the LLM judge (judge.py) against the question's required points; until then
they are unjudged. Citations are checked by code: a numeric claim is grounded when its value
is in a chunk it cites (or its calculation reruns from them).
"""

from tenk_agent.evaluation.scoring import score_numbers


def grade(q: dict, answer: dict, context: list[str], per_source: list[set[str]]) -> dict:
    """One answer's scores. `context`: the chunk ids the model saw; `per_source`: each gold
    source's chunks (restatements included)."""
    claims = answer["claims"]
    numeric = [c for c in claims if c["value"] is not None]
    cited = {cid for c in claims for cid in c["chunk_ids"]}
    shown = set(context)
    row = {
        "numbers": score_numbers(q, answer),  # None: not a number question
        "points": None,  # judge.py: [covered, …] per required point
        "claims": len(claims),
        "numeric_claims": len(numeric),
        "grounded": sum(c["status"] in ("verified", "calculated") for c in numeric),
        "cited_unseen": sorted(cited - shown),  # ids the model wasn't shown
        "gold_in_context": _share([bool(shown & ids) for ids in per_source]),
        "gold_cited": _share([bool(cited & ids) for ids in per_source]),
    }
    return row | {"correct": correct(q, answer["abstained"], row)}


def correct(q: dict, abstained: bool, row: dict) -> bool | None:
    """Decline questions: declined. Others: not declined, and every expected number matched
    (number questions) or every required point covered (text; None until judged)."""
    if q.get("should_abstain"):
        return abstained
    if abstained:
        return False
    if row["numbers"] is not None:
        return row["numbers"]["all"]
    return None if row["points"] is None else all(row["points"])


def summarize(rows: list[dict]) -> dict:
    """Scores of one context's answers."""
    answerable = [r for r in rows if not r["should_abstain"]]
    declines = [r for r in rows if r["should_abstain"]]
    scored = [r for r in rows if r["correct"] is not None]
    numbers = [r["numbers"] for r in rows if r["numbers"]]
    points = [p for r in rows if r["points"] for p in r["points"]]
    numeric = sum(r["numeric_claims"] for r in rows)
    full = [r for r in answerable if r["gold_in_context"] == 1 and r["correct"] is not None]
    partial = [r for r in answerable if r["gold_in_context"] != 1 and r["correct"] is not None]
    return {
        "questions": len(rows),
        "correct": _share([r["correct"] for r in scored]),
        "scored": len(scored),
        "unjudged": [r["id"] for r in rows if r["correct"] is None],
        "by_kind": {
            kind: {"questions": len(group), "correct": _share([r["correct"] for r in group])}
            for kind in dict.fromkeys(r["kind"] for r in rows)
            for group in [[r for r in scored if r["kind"] == kind]]
        },
        "declined_when_should": _share([r["abstained"] for r in declines]),
        "declined_when_answerable": _share([r["abstained"] for r in answerable]),
        "numbers_matched": (
            sum(n["matched"] for n in numbers) / sum(n["expected"] for n in numbers)
            if numbers
            else None
        ),
        "points_covered": _share(points),
        "numeric_claims": numeric,
        "grounded": sum(r["grounded"] for r in rows) / numeric if numeric else None,
        "cited_unseen": [r["id"] for r in rows if r["cited_unseen"]],
        "gold_cited": _share([r["gold_cited"] for r in answerable if not r["abstained"]]),
        "correct_with_all_gold": _share([r["correct"] for r in full]),
        "correct_with_gold_missing": _share([r["correct"] for r in partial]),
        "with_all_gold": len(full),
        "with_gold_missing": len(partial),
        "format_errors": [r["id"] for r in rows if r["format_error"]],
        "seconds_mean": sum(r["seconds"] for r in rows) / len(rows),
        "output_tokens_mean": sum(r["output_tokens"] for r in rows) / len(rows),
    }


def _share(values: list) -> float | None:
    """Mean of booleans or fractions, skipping None; None when there are none."""
    values = [float(v) for v in values if v is not None]
    return sum(values) / len(values) if values else None
