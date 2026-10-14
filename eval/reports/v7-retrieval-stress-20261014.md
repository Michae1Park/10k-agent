# Comparison: V1 vs V7-r1 vs V7-r2 vs V7-r3 vs V7-r4

- **V1**: `v1-retrieval-stress-20261014` (None, 29 questions)
- **V7-r1**: `v7-r1-parents-retrieval-stress-20261014` (None, 29 questions)
- **V7-r2**: `v7-r2-children-retrieval-stress-20261014` (None, 29 questions)
- **V7-r3**: `v7-r3-hybrid-retrieval-stress-20261014` (None, 29 questions)
- **V7-r4**: `v7-r4-items-retrieval-stress-20261014` (None, 29 questions)

Baseline: V1. Intervals are 95% bootstrap over questions; p-values are paired sign-flip permutation tests on the questions both runs answered, and a difference reads as better or worse only at p < 0.05. A ±5-point interval on Recall@5 needs ~157 questions.

**Provisional:** includes unverified gold questions.

## Metrics

| Metric | V1 | V7-r1 | V7-r2 | V7-r3 | V7-r4 |
| --- | --- | --- | --- | --- | --- |
| Recall@5 | 88.5% [76.8%, 98.0%] | 84.6% [71.2%, 96.2%] | 90.4% [79.2%, 100.0%] | 94.2% [84.6%, 100.0%] | 94.2% [84.6%, 100.0%] |
| Recall@10 | 92.3% [82.6%, 100.0%] | 90.4% [78.0%, 100.0%] | 94.2% [85.2%, 100.0%] | 94.2% [84.6%, 100.0%] | 94.2% [84.6%, 100.0%] |
| MRR | 0.66 [0.51, 0.79] | 0.64 [0.50, 0.79] | 0.66 [0.52, 0.80] | 0.69 [0.55, 0.82] | 0.69 [0.55, 0.82] |

## Differences vs V1

| Metric | Run | Difference [95% CI] | p | Reading |
| --- | --- | --- | --- | --- |
| Recall@5 | V7-r1 | -3.8 pt [-12.0 pt, +0.0 pt] | 1.000 | no clear difference |
| Recall@5 | V7-r2 | +1.9 pt [-13.0 pt, +17.3 pt] | 1.000 | no clear difference |
| Recall@5 | V7-r3 | +5.8 pt [-5.6 pt, +18.8 pt] | 0.547 | no clear difference |
| Recall@5 | V7-r4 | +5.8 pt [-5.8 pt, +18.5 pt] | 0.555 | no clear difference |
| Recall@10 | V7-r1 | -1.9 pt [-11.5 pt, +5.5 pt] | 1.000 | no clear difference |
| Recall@10 | V7-r2 | +1.9 pt [-9.3 pt, +14.0 pt] | 1.000 | no clear difference |
| Recall@10 | V7-r3 | +1.9 pt [-7.4 pt, +12.5 pt] | 1.000 | no clear difference |
| Recall@10 | V7-r4 | +1.9 pt [-7.2 pt, +12.5 pt] | 1.000 | no clear difference |
| MRR | V7-r1 | -0.01 [-0.03, +0.01] | 0.382 | no clear difference |
| MRR | V7-r2 | +0.00 [-0.12, +0.11] | 0.986 | no clear difference |
| MRR | V7-r3 | +0.03 [-0.07, +0.15] | 0.563 | no clear difference |
| MRR | V7-r4 | +0.03 [-0.07, +0.14] | 0.568 | no clear difference |

## Question-level changes (Recall@5)

| Run | Fixed | Regressed | Both pass | Both fail | McNemar p |
| --- | --- | --- | --- | --- | --- |
| V7-r1 | 0 | 1 | 21 | 4 | 1.000 |
| V7-r2 | 3 | 2 | 20 | 1 | 1.000 |
| V7-r3 | 3 | 1 | 21 | 1 | 0.625 |
| V7-r4 | 3 | 1 | 21 | 1 | 0.625 |

**V7-r1**: `nvda-customer-concentration-fy2025` regressed (retrieval miss)

**V7-r2**: `googl-msft-fcf-fy2025` regressed (retrieval miss), `meta-tax-rate-obbba-fy2025` regressed (retrieval miss), `msft-intelligent-cloud-growth-fy2025-recast` fixed, `nvda-data-center-decline-premise` fixed, `nvda-rd-calendar-2023-shifted` fixed

**V7-r3**: `googl-msft-fcf-fy2025` regressed (retrieval miss), `msft-intelligent-cloud-growth-fy2025-recast` fixed, `nvda-data-center-decline-premise` fixed, `nvda-rd-calendar-2023-shifted` fixed

**V7-r4**: `googl-msft-fcf-fy2025` regressed (retrieval miss), `msft-intelligent-cloud-growth-fy2025-recast` fixed, `nvda-data-center-decline-premise` fixed, `nvda-rd-calendar-2023-shifted` fixed

## Failure causes

| Cause | V1 | V7-r1 | V7-r2 | V7-r3 | V7-r4 |
| --- | --- | --- | --- | --- | --- |
| retrieval miss | 4 | 5 | 3 | 2 | 2 |

## Recall@5 by category

| Category | V1 | V7-r1 | V7-r2 | V7-r3 | V7-r4 |
| --- | --- | --- | --- | --- | --- |
| agent_workflow | 75.0% (n=2) | 75.0% (n=2) | 75.0% (n=2) | 50.0% (n=2) | 50.0% (n=2) |
| failure_case | 75.0% (n=9) | 75.0% (n=9) | 100.0% (n=9) | 100.0% (n=9) | 100.0% (n=9) |
| grounding | 100.0% (n=3) | 100.0% (n=3) | 66.7% (n=3) | 100.0% (n=3) | 100.0% (n=3) |
| multi_document | 100.0% (n=1) | 100.0% (n=1) | 0.0% (n=1) | 50.0% (n=1) | 50.0% (n=1) |
| retrieval | 100.0% (n=12) | 91.7% (n=12) | 100.0% (n=12) | 100.0% (n=12) | 100.0% (n=12) |
| tool_use | 50.0% (n=2) | 50.0% (n=2) | 100.0% (n=2) | 100.0% (n=2) | 100.0% (n=2) |
