"""Uncertainty for eval results: bootstrap intervals and paired tests between runs.

With ~30 test questions, a single number like "95.2%" hides an interval roughly ±10 points
wide. Every metric here is recomputed by `runner.summarize` on resampled rows, so intervals
cover exactly what the report shows (ratios, means, errors counted as failures).

- `intervals`  percentile bootstrap over questions (95%)
- `paired`     candidate - base on the questions both runs answered: bootstrap interval of
               the difference and a sign-flip permutation p-value (each question's two
               results swapped at random), which suits small, paired, bounded samples
- `mcnemar`    exact McNemar test on a pass/fail metric: only questions that changed count

Each resample is summarized once and every metric read from it.
"""

import math
import random
from dataclasses import dataclass

from tenk_agent.evaluation.runner import summarize

RESAMPLES = 2000


@dataclass(frozen=True)
class Metric:
    label: str
    section: str
    key: str
    systems: frozenset[str]
    fmt: str = "pct"  # pct | num
    higher_is_better: bool = True


ANSWER = frozenset({"ask", "research"})
RESEARCH = frozenset({"research"})
METRICS = (
    Metric("Task completion", "answers", "task_completion", ANSWER),
    Metric("Answer correctness", "answers", "correctness", ANSWER),
    Metric("Citation accuracy", "answers", "citation_accuracy", ANSWER),
    Metric("Evidence recall", "retrieval", "evidence_recall", RESEARCH),
    Metric("Tool selection", "agent", "tool_selection", RESEARCH),
    Metric("Tool-call correctness", "agent", "tool_call_correctness", RESEARCH),
    Metric("Over-budget rate", "agent", "over_budget_rate", RESEARCH, higher_is_better=False),
    Metric("Unnecessary calls / q", "agent", "unnecessary_calls", RESEARCH, "num", False),
    Metric("Mean latency (s)", "cost", "mean_latency_s", ANSWER, "num", False),
)


def metrics_for(system: str) -> list[Metric]:
    return [m for m in METRICS if system in m.systems]


def values(rows: list[dict], system: str, metrics: list[Metric]) -> dict[str, float | None]:
    summary = summarize(rows, system)
    return {m.key: summary.get(m.section, {}).get(m.key) for m in metrics}


@dataclass
class Interval:
    value: float | None
    low: float | None
    high: float | None
    n: int


@dataclass
class Paired:
    base_value: float | None
    candidate_value: float | None
    diff: float | None
    low: float | None
    high: float | None
    p: float | None
    n: int  # questions in both runs


def intervals(
    rows: list[dict], system: str, metrics: list[Metric], seed: int = 0
) -> dict[str, Interval]:
    observed = values(rows, system, metrics)
    rng = random.Random(seed)
    draws = [values(rng.choices(rows, k=len(rows)), system, metrics) for _ in range(RESAMPLES)]
    out = {}
    for m in metrics:
        low, high = _percentiles([d[m.key] for d in draws if d[m.key] is not None])
        value = observed[m.key]
        out[m.key] = Interval(value, *(low, high) if value is not None else (None, None), len(rows))
    return out


def paired(
    base: list[dict], candidate: list[dict], system: str, metrics: list[Metric], seed: int = 0
) -> dict[str, Paired]:
    """Candidate minus base, over the question IDs both runs contain."""
    by_id = {r["id"]: r for r in base}
    pairs = [(by_id[r["id"]], r) for r in candidate if r["id"] in by_id]

    def diffs(sample) -> dict[str, float | None]:
        b = values([p[0] for p in sample], system, metrics)
        c = values([p[1] for p in sample], system, metrics)
        return {k: None if b[k] is None or c[k] is None else c[k] - b[k] for k in b}

    if not pairs:
        return {m.key: Paired(None, None, None, None, None, None, 0) for m in metrics}
    b0 = values([p[0] for p in pairs], system, metrics)
    c0 = values([p[1] for p in pairs], system, metrics)
    observed = diffs(pairs)
    rng = random.Random(seed)
    boot = [diffs(rng.choices(pairs, k=len(pairs))) for _ in range(RESAMPLES)]
    flips = [
        diffs([(c, b) if rng.random() < 0.5 else (b, c) for b, c in pairs])
        for _ in range(RESAMPLES)
    ]
    out = {}
    for m in metrics:
        d = observed[m.key]
        if d is None:
            out[m.key] = Paired(b0[m.key], c0[m.key], None, None, None, None, len(pairs))
            continue
        low, high = _percentiles([x[m.key] for x in boot if x[m.key] is not None])
        extreme = sum(x[m.key] is not None and abs(x[m.key]) >= abs(d) - 1e-12 for x in flips)
        p = (extreme + 1) / (RESAMPLES + 1)
        out[m.key] = Paired(b0[m.key], c0[m.key], d, low, high, p, len(pairs))
    return out


def mcnemar(base: list[dict], candidate: list[dict], key: str = "task_complete") -> dict:
    """Exact two-sided McNemar test on a pass/fail field (errors count as fails)."""
    by_id = {r["id"]: r for r in base}
    fixed = regressed = both = neither = 0
    for r in candidate:
        if r["id"] not in by_id:
            continue
        b, c = _passed(by_id[r["id"]], key), _passed(r, key)
        if b is None or c is None:
            continue
        fixed += c and not b
        regressed += b and not c
        both += b and c
        neither += not b and not c
    n = fixed + regressed
    tail = sum(math.comb(n, k) for k in range(min(fixed, regressed) + 1)) / 2**n if n else 1.0
    return {
        "fixed": fixed,
        "regressed": regressed,
        "both_pass": both,
        "both_fail": neither,
        "p": min(1.0, 2 * tail),
    }


def questions_for_margin(p: float, margin: float = 0.05) -> int:
    """Questions needed for a ±margin 95% interval on a rate near p (normal approximation)."""
    return math.ceil(1.96**2 * p * (1 - p) / margin**2)


def _passed(row: dict, key: str) -> bool | None:
    if row.get("error"):
        return False
    value = row.get(key)
    return None if value is None else bool(value)


def _percentiles(draws: list[float]) -> tuple[float | None, float | None]:
    if not draws:
        return None, None
    draws = sorted(draws)
    return draws[int(0.025 * len(draws))], draws[min(len(draws) - 1, int(0.975 * len(draws)))]
