from tenk_agent.evaluation import analysis, stats


def row(qid: str, complete: bool, **extra) -> dict:
    """A research-run result row, as the runner writes it."""
    return {
        "id": qid,
        "split": "test",
        "category": "retrieval",
        "failure_mode": None,
        "answer_type": "number",
        "should_abstain": False,
        "verified": True,
        "abstained": False,
        "format_error": False,
        "numbers": {"expected": 1, "matched": int(complete), "all": complete},
        "citations": {"numeric_claims": 1, "accurate": 1},
        "correctness": float(complete),
        "task_complete": complete,
        "usage": {"latency_s": 10.0, "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0},
    } | extra


def test_mcnemar_counts_only_changed_questions():
    base = [row(f"q{i}", i < 10) for i in range(20)]
    cand = [row(f"q{i}", i < 16) for i in range(20)]
    result = stats.mcnemar(base, cand)
    assert (result["fixed"], result["regressed"]) == (6, 0)
    assert (result["both_pass"], result["both_fail"]) == (10, 4)
    assert abs(result["p"] - 2 / 2**6) < 1e-12  # exact: 6 changes, all one way


def test_mcnemar_counts_errors_as_failures():
    result = stats.mcnemar([row("q", True)], [row("q", True, error="boom")])
    assert result["regressed"] == 1


def test_paired_difference_interval_and_permutation_p():
    metrics = [m for m in stats.METRICS if m.key == "task_completion"]
    base = [row(f"q{i}", i < 10) for i in range(30)]
    same = stats.paired(base, base, "research", metrics)["task_completion"]
    assert same.diff == 0 and same.p == 1.0
    better = [row(f"q{i}", i < 25) for i in range(30)]
    result = stats.paired(base, better, "research", metrics)["task_completion"]
    assert abs(result.diff - 0.5) < 1e-9
    assert result.low > 0 and result.p < 0.001
    # Only questions in both runs are compared.
    assert stats.paired(base, better[:5], "research", metrics)["task_completion"].n == 5


def test_interval_brackets_the_value():
    metrics = [m for m in stats.METRICS if m.key == "task_completion"]
    rows = [row(f"q{i}", i % 4 != 0) for i in range(40)]
    iv = stats.intervals(rows, "research", metrics)["task_completion"]
    assert iv.value == 0.75 and iv.low < 0.75 < iv.high


def test_questions_for_margin():
    assert stats.questions_for_margin(0.5) == 385
    assert stats.questions_for_margin(0.9) == 139


def test_failure_cause_blames_the_first_broken_stage():
    assert analysis.failure_cause(row("q", True)) is None
    assert analysis.failure_cause(row("q", False, error="x")) == "error"
    assert analysis.failure_cause(row("q", False, format_error=True)) == "format"
    assert analysis.failure_cause(row("q", False, abstained=True)) == "false abstention"
    assert analysis.failure_cause(row("q", False, should_abstain=True)) == "unsupported"
    # Never saw the evidence: retrieval, even if the budget also ran out.
    over = {"over_budget_calls": 2}
    missed = row("q", False, evidence_recall=0.0, agent=over)
    assert analysis.failure_cause(missed) == "retrieval miss"
    assert analysis.failure_cause(row("q", False, evidence_recall=1.0, agent=over)) == "over budget"
    assert analysis.failure_cause(row("q", False, evidence_recall=1.0)) == "wrong value"
    cited_wrong = row("q", True, citations={"numeric_claims": 2, "accurate": 1})
    assert analysis.failure_cause(cited_wrong) == "citation"


def test_transitions_list_regressions_first():
    base = [row("a", True), row("b", False), row("c", True)]
    cand = [row("a", False), row("b", True), row("c", True)]
    changes = [(t["id"], t["change"]) for t in analysis.transitions(base, cand)]
    assert changes == [("a", "regressed"), ("b", "fixed"), ("c", "both pass")]
