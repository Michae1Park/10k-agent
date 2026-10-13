# Comparison: qwen3.6-27b-fp8 vs gemma-4-31b-fp8

- **qwen3.6-27b-fp8**: `v5-qwen3.6-27b-fp8-test-20261013` (openai:Qwen/Qwen3.6-27B-FP8, 31 questions)
- **gemma-4-31b-fp8**: `v5-gemma-4-31b-fp8-test-20261013` (openai:RedHatAI/gemma-4-31B-it-FP8-block, 31 questions)

Baseline: qwen3.6-27b-fp8. Intervals are 95% bootstrap over questions; p-values are paired sign-flip permutation tests on the questions both runs answered, and a difference reads as better or worse only at p < 0.05. A ±5-point interval on task completion needs ~73 questions.

**Provisional:** includes unverified gold questions.

## Metrics

| Metric | qwen3.6-27b-fp8 | gemma-4-31b-fp8 |
| --- | --- | --- |
| Task completion | 100.0% [100.0%, 100.0%] | 90.5% [76.5%, 100.0%] |
| Answer correctness | 100.0% [100.0%, 100.0%] | 90.5% [76.5%, 100.0%] |
| Citation accuracy | 95.1% [89.2%, 100.0%] | 100.0% [100.0%, 100.0%] |
| Evidence recall | 75.0% [58.9%, 89.2%] | 76.8% [62.2%, 90.0%] |
| Tool selection | 93.5% [83.9%, 100.0%] | 93.5% [83.9%, 100.0%] |
| Tool-call correctness | 96.6% [93.1%, 99.1%] | 86.5% [77.1%, 97.3%] |
| Over-budget rate | 12.9% [3.2%, 25.8%] | 0.0% [0.0%, 0.0%] |
| Unnecessary calls / q | 1.23 [0.35, 2.42] | 0.00 [0.00, 0.00] |
| Mean latency (s) | 243.50 [166.77, 328.36] | 119.11 [86.86, 154.59] |

## Differences vs qwen3.6-27b-fp8

| Metric | Run | Difference [95% CI] | p | Reading |
| --- | --- | --- | --- | --- |
| Task completion | gemma-4-31b-fp8 | -9.5 pt [-23.5 pt, +0.0 pt] | 0.502 | no clear difference |
| Answer correctness | gemma-4-31b-fp8 | -9.5 pt [-23.5 pt, +0.0 pt] | 0.502 | no clear difference |
| Citation accuracy | gemma-4-31b-fp8 | +4.9 pt [+0.0 pt, +10.8 pt] | 0.473 | no clear difference |
| Evidence recall | gemma-4-31b-fp8 | +1.8 pt [-15.3 pt, +17.7 pt] | 0.842 | no clear difference |
| Tool selection | gemma-4-31b-fp8 | +0.0 pt [-9.7 pt, +9.7 pt] | 1.000 | no clear difference |
| Tool-call correctness | gemma-4-31b-fp8 | -10.1 pt [-20.5 pt, +1.5 pt] | 0.023 | worse |
| Over-budget rate | gemma-4-31b-fp8 | -12.9 pt [-25.8 pt, -3.2 pt] | 0.120 | no clear difference |
| Unnecessary calls / q | gemma-4-31b-fp8 | -1.23 [-2.32, -0.35] | 0.010 | better |
| Mean latency (s) | gemma-4-31b-fp8 | -124.39 [-194.05, -63.75] | <0.001 | better |

## Question-level changes (task completion)

| Run | Fixed | Regressed | Both pass | Both fail | McNemar p |
| --- | --- | --- | --- | --- | --- |
| gemma-4-31b-fp8 | 0 | 2 | 19 | 0 | 0.500 |

**gemma-4-31b-fp8**: `aapl-net-margin-fy2025` regressed (wrong value), `amzn-net-sales-fy2021-unsupported` regressed (unsupported)

## Failure causes

| Cause | qwen3.6-27b-fp8 | gemma-4-31b-fp8 |
| --- | --- | --- |
| unsupported | 0 | 1 |
| wrong value | 0 | 1 |

## Task completion by category

| Category | qwen3.6-27b-fp8 | gemma-4-31b-fp8 |
| --- | --- | --- |
| agent_workflow | n/m (n=3) | n/m (n=3) |
| failure_case | 100.0% (n=5) | 75.0% (n=5) |
| grounding | n/m (n=6) | n/m (n=6) |
| multi_document | 100.0% (n=4) | 100.0% (n=4) |
| retrieval | 100.0% (n=9) | 100.0% (n=9) |
| tool_use | 100.0% (n=4) | 75.0% (n=4) |
