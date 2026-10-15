# Comparison: V2 vs V7-r1 vs V7-r2 vs V7-r3 vs V7-r4

- **V2**: `v2-ask-qwen3.6-27b-fp8-stress-20261014` (openai:Qwen/Qwen3.6-27B-FP8, 29 questions)
- **V7-r1**: `v7-r1-parents-ask-qwen3.6-27b-fp8-stress-20261014` (openai:Qwen/Qwen3.6-27B-FP8, 29 questions)
- **V7-r2**: `v7-r2-children-ask-qwen3.6-27b-fp8-stress-20261014` (openai:Qwen/Qwen3.6-27B-FP8, 29 questions)
- **V7-r3**: `v7-r3-hybrid-ask-qwen3.6-27b-fp8-stress-20261014` (openai:Qwen/Qwen3.6-27B-FP8, 29 questions)
- **V7-r4**: `v7-r4-items-ask-qwen3.6-27b-fp8-stress-20261014` (openai:Qwen/Qwen3.6-27B-FP8, 29 questions)

Baseline: V2. Intervals are 95% bootstrap over questions; p-values are paired sign-flip permutation tests on the questions both runs answered, and a difference reads as better or worse only at p < 0.05. A ±5-point interval on task completion needs ~373 questions.

**Provisional:** includes unverified gold questions.

## Metrics

| Metric | V2 | V7-r1 | V7-r2 | V7-r3 | V7-r4 |
| --- | --- | --- | --- | --- | --- |
| Task completion | 58.6% [41.4%, 75.9%] | 58.6% [41.4%, 75.9%] | 58.6% [41.4%, 75.9%] | 58.6% [41.4%, 75.9%] | 58.6% [41.4%, 75.9%] |
| Answer correctness | 71.8% [56.0%, 85.9%] | 72.7% [57.2%, 86.2%] | 72.7% [58.6%, 85.6%] | 71.8% [57.8%, 85.1%] | 71.8% [56.6%, 84.8%] |
| Citation accuracy | 94.4% [85.7%, 100.0%] | 96.8% [90.3%, 100.0%] | 88.2% [73.0%, 100.0%] | 90.3% [78.0%, 100.0%] | 90.3% [77.8%, 100.0%] |
| Mean latency (s) | 31.73 [26.59, 38.83] | 35.56 [30.88, 40.65] | 36.20 [29.83, 43.85] | 36.77 [30.47, 45.35] | 35.87 [30.10, 42.72] |
| Recall@5 | 88.5% [76.0%, 98.2%] | 84.6% [70.8%, 96.2%] | 90.4% [78.0%, 100.0%] | 94.2% [84.6%, 100.0%] | 94.2% [84.6%, 100.0%] |
| MRR | 0.66 [0.51, 0.80] | 0.64 [0.50, 0.79] | 0.66 [0.52, 0.80] | 0.69 [0.55, 0.83] | 0.69 [0.55, 0.83] |

## Differences vs V2

| Metric | Run | Difference [95% CI] | p | Reading |
| --- | --- | --- | --- | --- |
| Task completion | V7-r1 | +0.0 pt [-10.3 pt, +10.3 pt] | 1.000 | no clear difference |
| Task completion | V7-r2 | +0.0 pt [-10.3 pt, +10.3 pt] | 1.000 | no clear difference |
| Task completion | V7-r3 | +0.0 pt [-10.3 pt, +10.3 pt] | 1.000 | no clear difference |
| Task completion | V7-r4 | +0.0 pt [-10.3 pt, +10.3 pt] | 1.000 | no clear difference |
| Answer correctness | V7-r1 | +0.9 pt [-9.5 pt, +11.2 pt] | 1.000 | no clear difference |
| Answer correctness | V7-r2 | +0.9 pt [-6.0 pt, +10.3 pt] | 1.000 | no clear difference |
| Answer correctness | V7-r3 | +0.0 pt [-10.4 pt, +10.4 pt] | 1.000 | no clear difference |
| Answer correctness | V7-r4 | +0.0 pt [-10.4 pt, +10.4 pt] | 1.000 | no clear difference |
| Citation accuracy | V7-r1 | +2.3 pt [-7.9 pt, +13.3 pt] | 0.820 | no clear difference |
| Citation accuracy | V7-r2 | -6.2 pt [-23.7 pt, +5.2 pt] | 0.751 | no clear difference |
| Citation accuracy | V7-r3 | -4.1 pt [-19.4 pt, +11.1 pt] | 0.685 | no clear difference |
| Citation accuracy | V7-r4 | -4.1 pt [-19.4 pt, +11.1 pt] | 0.679 | no clear difference |
| Mean latency (s) | V7-r1 | +3.83 [-1.09, +8.58] | 0.142 | no clear difference |
| Mean latency (s) | V7-r2 | +4.47 [-0.05, +9.92] | 0.093 | no clear difference |
| Mean latency (s) | V7-r3 | +5.04 [-1.55, +12.22] | 0.193 | no clear difference |
| Mean latency (s) | V7-r4 | +4.14 [-1.93, +10.01] | 0.205 | no clear difference |
| Recall@5 | V7-r1 | -3.8 pt [-12.5 pt, +0.0 pt] | 1.000 | no clear difference |
| Recall@5 | V7-r2 | +1.9 pt [-13.0 pt, +18.0 pt] | 1.000 | no clear difference |
| Recall@5 | V7-r3 | +5.8 pt [-5.5 pt, +19.6 pt] | 0.548 | no clear difference |
| Recall@5 | V7-r4 | +5.8 pt [-5.6 pt, +19.2 pt] | 0.565 | no clear difference |
| MRR | V7-r1 | -0.01 [-0.03, +0.01] | 0.374 | no clear difference |
| MRR | V7-r2 | +0.00 [-0.12, +0.11] | 0.981 | no clear difference |
| MRR | V7-r3 | +0.03 [-0.07, +0.14] | 0.563 | no clear difference |
| MRR | V7-r4 | +0.03 [-0.07, +0.14] | 0.560 | no clear difference |

## Question-level changes (task completion)

| Run | Fixed | Regressed | Both pass | Both fail | McNemar p |
| --- | --- | --- | --- | --- | --- |
| V7-r1 | 1 | 1 | 16 | 11 | 1.000 |
| V7-r2 | 1 | 1 | 16 | 11 | 1.000 |
| V7-r3 | 1 | 1 | 16 | 11 | 1.000 |
| V7-r4 | 1 | 1 | 16 | 11 | 1.000 |

**V7-r1**: `googl-msft-fcf-fy2025` regressed (wrong value), `nvda-rd-calendar-2023-shifted` fixed

**V7-r2**: `googl-msft-fcf-fy2025` regressed (retrieval miss), `nvda-rd-calendar-2023-shifted` fixed

**V7-r3**: `googl-msft-fcf-fy2025` regressed (wrong value), `nvda-rd-calendar-2023-shifted` fixed

**V7-r4**: `googl-msft-fcf-fy2025` regressed (wrong value), `nvda-rd-calendar-2023-shifted` fixed

## Failure causes

| Cause | V2 | V7-r1 | V7-r2 | V7-r3 | V7-r4 |
| --- | --- | --- | --- | --- | --- |
| false abstention | 1 | 1 | 0 | 0 | 0 |
| retrieval miss | 2 | 2 | 2 | 1 | 1 |
| wrong value | 9 | 9 | 10 | 11 | 11 |
| citation | 1 | 0 | 0 | 0 | 0 |

## Task completion by category

| Category | V2 | V7-r1 | V7-r2 | V7-r3 | V7-r4 |
| --- | --- | --- | --- | --- | --- |
| agent_workflow | 0.0% (n=2) | 0.0% (n=2) | 0.0% (n=2) | 0.0% (n=2) | 0.0% (n=2) |
| failure_case | 77.8% (n=9) | 88.9% (n=9) | 88.9% (n=9) | 88.9% (n=9) | 88.9% (n=9) |
| grounding | 0.0% (n=3) | 0.0% (n=3) | 0.0% (n=3) | 0.0% (n=3) | 0.0% (n=3) |
| multi_document | 100.0% (n=1) | 0.0% (n=1) | 0.0% (n=1) | 0.0% (n=1) | 0.0% (n=1) |
| retrieval | 75.0% (n=12) | 75.0% (n=12) | 75.0% (n=12) | 75.0% (n=12) | 75.0% (n=12) |
| tool_use | 0.0% (n=2) | 0.0% (n=2) | 0.0% (n=2) | 0.0% (n=2) | 0.0% (n=2) |
