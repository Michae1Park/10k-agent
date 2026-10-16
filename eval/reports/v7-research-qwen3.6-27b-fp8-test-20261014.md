# Comparison: V4 vs V7

- **V4**: `v5-qwen3.6-27b-fp8-test-20261013` (openai:Qwen/Qwen3.6-27B-FP8, 31 questions)
- **V7**: `v7-r5-capped-research-qwen3.6-27b-fp8-test-20261014` (openai:Qwen/Qwen3.6-27B-FP8, 31 questions)

Baseline: V4. Intervals are 95% bootstrap over questions; p-values are paired sign-flip permutation tests on the questions both runs answered, and a difference reads as better or worse only at p < 0.05. A ±5-point interval on task completion needs ~295 questions.

**Provisional:** includes unverified gold questions.

## Metrics

| Metric | V4 | V7 |
| --- | --- | --- |
| Task completion | 74.2% [58.1%, 90.3%] | 74.2% [58.1%, 90.3%] |
| Answer correctness | 90.0% [82.7%, 96.0%] | 90.5% [82.7%, 96.8%] |
| Citation accuracy | 95.1% [89.2%, 100.0%] | 96.2% [88.5%, 100.0%] |
| Evidence recall | 75.0% [58.9%, 89.2%] | 90.5% [79.3%, 99.1%] |
| Tool selection | 93.5% [83.9%, 100.0%] | 90.3% [80.7%, 100.0%] |
| Tool-call correctness | 96.6% [93.1%, 99.1%] | 94.9% [91.1%, 97.9%] |
| Over-budget rate | 12.9% [3.2%, 25.8%] | 16.1% [3.2%, 29.0%] |
| Unnecessary calls / q | 1.23 [0.35, 2.42] | 1.42 [0.42, 2.68] |
| Mean latency (s) | 243.50 [166.77, 328.36] | 227.65 [165.91, 296.10] |

## Differences vs V4

| Metric | Run | Difference [95% CI] | p | Reading |
| --- | --- | --- | --- | --- |
| Task completion | V7 | +0.0 pt [-12.9 pt, +12.9 pt] | 1.000 | no clear difference |
| Answer correctness | V7 | +0.6 pt [-8.1 pt, +8.1 pt] | 0.939 | no clear difference |
| Citation accuracy | V7 | +1.1 pt [-1.5 pt, +4.0 pt] | 0.539 | no clear difference |
| Evidence recall | V7 | +15.5 pt [+0.0 pt, +31.2 pt] | 0.125 | no clear difference |
| Tool selection | V7 | -3.2 pt [-9.7 pt, +0.0 pt] | 1.000 | no clear difference |
| Tool-call correctness | V7 | -1.6 pt [-5.1 pt, +1.4 pt] | 0.426 | no clear difference |
| Over-budget rate | V7 | +3.2 pt [+0.0 pt, +9.7 pt] | 1.000 | no clear difference |
| Unnecessary calls / q | V7 | +0.19 [-0.39, +0.81] | 0.608 | no clear difference |
| Mean latency (s) | V7 | -15.85 [-75.74, +23.62] | 0.654 | no clear difference |

## Question-level changes (task completion)

| Run | Fixed | Regressed | Both pass | Both fail | McNemar p |
| --- | --- | --- | --- | --- | --- |
| V7 | 2 | 2 | 21 | 6 | 1.000 |

**V7**: `amzn-net-sales-fy2021-unsupported` regressed (unsupported), `msft-ai-risks-fy2025` regressed (wrong value), `nflx-revenue-drivers-fy2025` fixed, `tsla-revenue-decline-2023-premise` fixed

## Failure causes

| Cause | V4 | V7 |
| --- | --- | --- |
| unsupported | 0 | 1 |
| retrieval miss | 4 | 1 |
| over budget | 1 | 5 |
| wrong value | 3 | 1 |

## Task completion by category

| Category | V4 | V7 |
| --- | --- | --- |
| agent_workflow | 0.0% (n=3) | 0.0% (n=3) |
| failure_case | 80.0% (n=5) | 80.0% (n=5) |
| grounding | 33.3% (n=6) | 33.3% (n=6) |
| multi_document | 100.0% (n=4) | 100.0% (n=4) |
| retrieval | 100.0% (n=9) | 100.0% (n=9) |
| tool_use | 100.0% (n=4) | 100.0% (n=4) |
