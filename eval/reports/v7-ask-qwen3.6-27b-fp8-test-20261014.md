# Comparison: V2 vs V7-r1 vs V7-r2 vs V7-r3 vs V7-r4

- **V2**: `v2-ask-qwen3.6-27b-fp8-test-20261014` (openai:Qwen/Qwen3.6-27B-FP8, 31 questions)
- **V7-r1**: `v7-r1-parents-ask-qwen3.6-27b-fp8-test-20261014` (openai:Qwen/Qwen3.6-27B-FP8, 31 questions)
- **V7-r2**: `v7-r2-children-ask-qwen3.6-27b-fp8-test-20261014` (openai:Qwen/Qwen3.6-27B-FP8, 31 questions)
- **V7-r3**: `v7-r3-hybrid-ask-qwen3.6-27b-fp8-test-20261014` (openai:Qwen/Qwen3.6-27B-FP8, 31 questions)
- **V7-r4**: `v7-r4-items-ask-qwen3.6-27b-fp8-test-20261014` (openai:Qwen/Qwen3.6-27B-FP8, 31 questions)

Baseline: V2. Intervals are 95% bootstrap over questions; p-values are paired sign-flip permutation tests on the questions both runs answered, and a difference reads as better or worse only at p < 0.05. A ±5-point interval on task completion needs ~365 questions.

**Provisional:** includes unverified gold questions.

## Metrics

| Metric | V2 | V7-r1 | V7-r2 | V7-r3 | V7-r4 |
| --- | --- | --- | --- | --- | --- |
| Task completion | 61.3% [45.2%, 77.4%] | 61.3% [45.2%, 77.4%] | 71.0% [54.8%, 87.1%] | 64.5% [48.4%, 80.7%] | 67.7% [51.6%, 83.9%] |
| Answer correctness | 76.6% [62.5%, 88.1%] | 77.2% [64.9%, 88.7%] | 78.5% [65.7%, 90.5%] | 78.0% [65.9%, 88.7%] | 76.9% [63.4%, 89.0%] |
| Citation accuracy | 100.0% [100.0%, 100.0%] | 94.4% [86.7%, 100.0%] | 97.2% [91.4%, 100.0%] | 97.1% [90.3%, 100.0%] | 100.0% [100.0%, 100.0%] |
| Mean latency (s) | 43.81 [37.55, 50.85] | 42.41 [34.80, 50.88] | 45.57 [37.38, 56.08] | 44.82 [36.58, 56.04] | 43.49 [35.95, 53.09] |
| Recall@5 | 71.2% [54.4%, 85.7%] | 74.1% [59.4%, 87.9%] | 83.3% [70.2%, 94.6%] | 81.6% [68.4%, 92.9%] | 81.6% [68.8%, 93.3%] |
| MRR | 0.53 [0.40, 0.65] | 0.56 [0.44, 0.69] | 0.57 [0.44, 0.70] | 0.56 [0.44, 0.69] | 0.56 [0.44, 0.69] |

## Differences vs V2

| Metric | Run | Difference [95% CI] | p | Reading |
| --- | --- | --- | --- | --- |
| Task completion | V7-r1 | +0.0 pt [+0.0 pt, +0.0 pt] | 1.000 | no clear difference |
| Task completion | V7-r2 | +9.7 pt [+0.0 pt, +22.6 pt] | 0.240 | no clear difference |
| Task completion | V7-r3 | +3.2 pt [+0.0 pt, +9.7 pt] | 1.000 | no clear difference |
| Task completion | V7-r4 | +6.5 pt [+0.0 pt, +16.1 pt] | 0.493 | no clear difference |
| Answer correctness | V7-r1 | +0.6 pt [-1.8 pt, +3.2 pt] | 1.000 | no clear difference |
| Answer correctness | V7-r2 | +2.0 pt [-6.6 pt, +11.9 pt] | 0.744 | no clear difference |
| Answer correctness | V7-r3 | +1.5 pt [-2.4 pt, +6.0 pt] | 0.750 | no clear difference |
| Answer correctness | V7-r4 | +0.4 pt [-6.7 pt, +7.5 pt] | 1.000 | no clear difference |
| Citation accuracy | V7-r1 | -5.6 pt [-13.3 pt, +0.0 pt] | 0.245 | no clear difference |
| Citation accuracy | V7-r2 | -2.8 pt [-8.6 pt, +0.0 pt] | 0.531 | no clear difference |
| Citation accuracy | V7-r3 | -2.9 pt [-9.7 pt, +0.0 pt] | 0.326 | no clear difference |
| Citation accuracy | V7-r4 | +0.0 pt [+0.0 pt, +0.0 pt] | 1.000 | no clear difference |
| Mean latency (s) | V7-r1 | -1.40 [-6.01, +4.07] | 0.622 | no clear difference |
| Mean latency (s) | V7-r2 | +1.76 [-3.51, +8.90] | 0.699 | no clear difference |
| Mean latency (s) | V7-r3 | +1.01 [-5.38, +9.86] | 0.922 | no clear difference |
| Mean latency (s) | V7-r4 | -0.32 [-5.18, +6.44] | 0.952 | no clear difference |
| Recall@5 | V7-r1 | +2.9 pt [+0.0 pt, +7.4 pt] | 0.501 | no clear difference |
| Recall@5 | V7-r2 | +12.1 pt [+1.7 pt, +25.0 pt] | 0.131 | no clear difference |
| Recall@5 | V7-r3 | +10.3 pt [+0.0 pt, +22.6 pt] | 0.190 | no clear difference |
| Recall@5 | V7-r4 | +10.3 pt [+0.0 pt, +22.4 pt] | 0.196 | no clear difference |
| MRR | V7-r1 | +0.04 [+0.00, +0.08] | 0.048 | better |
| MRR | V7-r2 | +0.05 [-0.05, +0.14] | 0.350 | no clear difference |
| MRR | V7-r3 | +0.04 [-0.06, +0.13] | 0.477 | no clear difference |
| MRR | V7-r4 | +0.04 [-0.06, +0.13] | 0.431 | no clear difference |

## Question-level changes (task completion)

| Run | Fixed | Regressed | Both pass | Both fail | McNemar p |
| --- | --- | --- | --- | --- | --- |
| V7-r1 | 0 | 0 | 19 | 12 | 1.000 |
| V7-r2 | 3 | 0 | 19 | 9 | 0.250 |
| V7-r3 | 1 | 0 | 19 | 11 | 1.000 |
| V7-r4 | 2 | 0 | 19 | 10 | 0.500 |

**V7-r2**: `aapl-msft-net-income-fy2025` fixed, `googl-rd-cagr-fy2023-2025` fixed, `tsla-revenue-decline-2023-premise` fixed

**V7-r3**: `aapl-msft-net-income-fy2025` fixed

**V7-r4**: `aapl-msft-net-income-fy2025` fixed, `tsla-revenue-decline-2023-premise` fixed

## Failure causes

| Cause | V2 | V7-r1 | V7-r2 | V7-r3 | V7-r4 |
| --- | --- | --- | --- | --- | --- |
| false abstention | 1 | 0 | 1 | 1 | 1 |
| unsupported | 1 | 1 | 1 | 1 | 1 |
| retrieval miss | 4 | 3 | 0 | 1 | 1 |
| wrong value | 6 | 8 | 7 | 8 | 7 |
| citation | 0 | 0 | 1 | 0 | 0 |

## Task completion by category

| Category | V2 | V7-r1 | V7-r2 | V7-r3 | V7-r4 |
| --- | --- | --- | --- | --- | --- |
| agent_workflow | 0.0% (n=3) | 0.0% (n=3) | 0.0% (n=3) | 0.0% (n=3) | 0.0% (n=3) |
| failure_case | 60.0% (n=5) | 60.0% (n=5) | 80.0% (n=5) | 60.0% (n=5) | 80.0% (n=5) |
| grounding | 16.7% (n=6) | 16.7% (n=6) | 16.7% (n=6) | 16.7% (n=6) | 16.7% (n=6) |
| multi_document | 75.0% (n=4) | 75.0% (n=4) | 100.0% (n=4) | 100.0% (n=4) | 100.0% (n=4) |
| retrieval | 100.0% (n=9) | 100.0% (n=9) | 100.0% (n=9) | 100.0% (n=9) | 100.0% (n=9) |
| tool_use | 75.0% (n=4) | 75.0% (n=4) | 100.0% (n=4) | 75.0% (n=4) | 75.0% (n=4) |
