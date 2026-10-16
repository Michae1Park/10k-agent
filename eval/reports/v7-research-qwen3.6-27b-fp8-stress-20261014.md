# Comparison: V4 vs V7

- **V4**: `v4-research-qwen3.6-27b-fp8-stress-20261014` (openai:Qwen/Qwen3.6-27B-FP8, 29 questions)
- **V7**: `v7-r5-capped-research-qwen3.6-27b-fp8-stress-20261014` (openai:Qwen/Qwen3.6-27B-FP8, 29 questions)

Baseline: V4. Intervals are 95% bootstrap over questions; p-values are paired sign-flip permutation tests on the questions both runs answered, and a difference reads as better or worse only at p < 0.05. A ±5-point interval on task completion needs ~220 questions.

**Provisional:** includes unverified gold questions.

## Metrics

| Metric | V4 | V7 |
| --- | --- | --- |
| Task completion | 82.8% [69.0%, 96.5%] | 86.2% [72.4%, 96.5%] |
| Answer correctness | 88.9% [75.9%, 100.0%] | 93.8% [84.8%, 100.0%] |
| Citation accuracy | 98.3% [95.2%, 100.0%] | 98.4% [94.7%, 100.0%] |
| Evidence recall | 77.1% [60.0%, 92.0%] | 100.0% [100.0%, 100.0%] |
| Tool selection | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| Tool-call correctness | 93.0% [88.2%, 97.2%] | 95.4% [92.6%, 98.0%] |
| Over-budget rate | 3.7% [0.0%, 11.5%] | 7.1% [0.0%, 17.9%] |
| Unnecessary calls / q | 0.41 [0.09, 0.77] | 1.11 [0.38, 2.12] |
| Mean latency (s) | 251.91 [182.48, 330.32] | 275.98 [216.06, 339.46] |

## Differences vs V4

| Metric | Run | Difference [95% CI] | p | Reading |
| --- | --- | --- | --- | --- |
| Task completion | V7 | +3.4 pt [-13.8 pt, +20.7 pt] | 1.000 | no clear difference |
| Answer correctness | V7 | +4.9 pt [-9.0 pt, +20.4 pt] | 0.565 | no clear difference |
| Citation accuracy | V7 | +0.1 pt [-4.8 pt, +4.5 pt] | 0.801 | no clear difference |
| Evidence recall | V7 | +22.9 pt [+8.0 pt, +40.4 pt] | 0.023 | better |
| Tool selection | V7 | +0.0 pt [+0.0 pt, +0.0 pt] | 1.000 | no clear difference |
| Tool-call correctness | V7 | +2.4 pt [-2.3 pt, +8.3 pt] | 0.358 | no clear difference |
| Over-budget rate | V7 | +3.4 pt [-8.3 pt, +15.2 pt] | 1.000 | no clear difference |
| Unnecessary calls / q | V7 | +0.70 [-0.02, +1.52] | 0.094 | no clear difference |
| Mean latency (s) | V7 | +24.07 [-41.88, +85.42] | 0.489 | no clear difference |

## Question-level changes (task completion)

| Run | Fixed | Regressed | Both pass | Both fail | McNemar p |
| --- | --- | --- | --- | --- | --- |
| V7 | 4 | 3 | 21 | 1 | 1.000 |

**V7**: `meta-tax-rate-obbba-fy2025` regressed (wrong value), `nflx-content-obligations-growth-fy2024` regressed (wrong value), `tsla-regulatory-credits-fy2024` regressed (wrong value), `msft-rpo-fy2025` fixed, `msft-segment-recast-fy2025` fixed, `nvda-rd-calendar-2023-shifted` fixed, `tsla-cybertruck-deliveries-2024` fixed

## Failure causes

| Cause | V4 | V7 |
| --- | --- | --- |
| error | 2 | 1 |
| unsupported | 1 | 0 |
| wrong value | 2 | 3 |
| citation | 1 | 1 |

## Task completion by category

| Category | V4 | V7 |
| --- | --- | --- |
| agent_workflow | 50.0% (n=2) | 50.0% (n=2) |
| failure_case | 77.8% (n=9) | 100.0% (n=9) |
| grounding | 66.7% (n=3) | 66.7% (n=3) |
| multi_document | 100.0% (n=1) | 100.0% (n=1) |
| retrieval | 91.7% (n=12) | 91.7% (n=12) |
| tool_use | 100.0% (n=2) | 50.0% (n=2) |
