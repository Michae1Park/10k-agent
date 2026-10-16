# Comparison: V1 vs V7-r1 vs V7-r2 vs V7-r3 vs V7-r4

- **V1**: `v1-retrieval-test` (None, 31 questions)
- **V7-r1**: `v7-r1-parents-retrieval-test-20261014` (None, 31 questions)
- **V7-r2**: `v7-r2-children-retrieval-test-20261014` (None, 31 questions)
- **V7-r3**: `v7-r3-hybrid-retrieval-test-20261014` (None, 31 questions)
- **V7-r4**: `v7-r4-items-retrieval-test-20261014` (None, 31 questions)

Baseline: V1. Intervals are 95% bootstrap over questions; p-values are paired sign-flip permutation tests on the questions both runs answered, and a difference reads as better or worse only at p < 0.05. A ±5-point interval on Recall@5 needs ~315 questions.

**Provisional:** includes unverified gold questions.

## Metrics

| Metric | V1 | V7-r1 | V7-r2 | V7-r3 | V7-r4 |
| --- | --- | --- | --- | --- | --- |
| Recall@5 | 71.2% [54.9%, 85.5%] | 74.1% [59.0%, 87.6%] | 83.3% [68.9%, 94.6%] | 81.6% [68.2%, 93.3%] | 81.6% [67.6%, 93.7%] |
| Recall@10 | 88.0% [76.1%, 97.5%] | 89.8% [80.2%, 97.7%] | 87.0% [73.8%, 97.3%] | 87.8% [76.5%, 97.4%] | 87.8% [75.7%, 97.4%] |
| MRR | 0.53 [0.41, 0.65] | 0.56 [0.43, 0.69] | 0.57 [0.45, 0.70] | 0.56 [0.44, 0.69] | 0.56 [0.44, 0.69] |

## Differences vs V1

| Metric | Run | Difference [95% CI] | p | Reading |
| --- | --- | --- | --- | --- |
| Recall@5 | V7-r1 | +2.9 pt [+0.0 pt, +7.5 pt] | 0.499 | no clear difference |
| Recall@5 | V7-r2 | +12.1 pt [+0.0 pt, +25.0 pt] | 0.135 | no clear difference |
| Recall@5 | V7-r3 | +10.3 pt [+0.0 pt, +23.2 pt] | 0.182 | no clear difference |
| Recall@5 | V7-r4 | +10.3 pt [+0.0 pt, +23.2 pt] | 0.195 | no clear difference |
| Recall@10 | V7-r1 | +1.7 pt [+0.0 pt, +5.4 pt] | 1.000 | no clear difference |
| Recall@10 | V7-r2 | -1.1 pt [-10.1 pt, +6.2 pt] | 0.874 | no clear difference |
| Recall@10 | V7-r3 | -0.2 pt [-9.6 pt, +7.5 pt] | 1.000 | no clear difference |
| Recall@10 | V7-r4 | -0.2 pt [-9.5 pt, +7.3 pt] | 1.000 | no clear difference |
| MRR | V7-r1 | +0.04 [+0.01, +0.08] | 0.045 | better |
| MRR | V7-r2 | +0.05 [-0.04, +0.14] | 0.366 | no clear difference |
| MRR | V7-r3 | +0.04 [-0.06, +0.13] | 0.434 | no clear difference |
| MRR | V7-r4 | +0.04 [-0.05, +0.13] | 0.444 | no clear difference |

## Question-level changes (Recall@5)

| Run | Fixed | Regressed | Both pass | Both fail | McNemar p |
| --- | --- | --- | --- | --- | --- |
| V7-r1 | 1 | 0 | 19 | 9 | 1.000 |
| V7-r2 | 5 | 1 | 18 | 5 | 0.219 |
| V7-r3 | 4 | 1 | 18 | 6 | 0.375 |
| V7-r4 | 4 | 1 | 18 | 6 | 0.375 |

**V7-r1**: `aapl-services-share-fy2025` fixed

**V7-r2**: `aapl-msft-operating-cash-flow-fy2025` regressed (retrieval miss), `aapl-msft-net-income-fy2025` fixed, `aapl-services-share-fy2025` fixed, `facebook-revenue-fy2024-entity` fixed, `nvda-export-controls-fy2025` fixed, `tsla-competition-fy2025` fixed

**V7-r3**: `aapl-msft-operating-cash-flow-fy2025` regressed (retrieval miss), `aapl-msft-net-income-fy2025` fixed, `facebook-revenue-fy2024-entity` fixed, `nvda-export-controls-fy2025` fixed, `tsla-competition-fy2025` fixed

**V7-r4**: `aapl-msft-operating-cash-flow-fy2025` regressed (retrieval miss), `aapl-msft-net-income-fy2025` fixed, `facebook-revenue-fy2024-entity` fixed, `nvda-export-controls-fy2025` fixed, `tsla-competition-fy2025` fixed

## Failure causes

| Cause | V1 | V7-r1 | V7-r2 | V7-r3 | V7-r4 |
| --- | --- | --- | --- | --- | --- |
| retrieval miss | 10 | 9 | 6 | 7 | 7 |

## Recall@5 by category

| Category | V1 | V7-r1 | V7-r2 | V7-r3 | V7-r4 |
| --- | --- | --- | --- | --- | --- |
| agent_workflow | 22.0% (n=3) | 22.0% (n=3) | 22.0% (n=3) | 22.0% (n=3) | 22.0% (n=3) |
| failure_case | 66.7% (n=5) | 66.7% (n=5) | 100.0% (n=5) | 100.0% (n=5) | 100.0% (n=5) |
| grounding | 66.7% (n=6) | 72.2% (n=6) | 100.0% (n=6) | 100.0% (n=6) | 100.0% (n=6) |
| multi_document | 87.5% (n=4) | 87.5% (n=4) | 87.5% (n=4) | 87.5% (n=4) | 87.5% (n=4) |
| retrieval | 100.0% (n=9) | 100.0% (n=9) | 100.0% (n=9) | 100.0% (n=9) | 100.0% (n=9) |
| tool_use | 37.5% (n=4) | 50.0% (n=4) | 50.0% (n=4) | 37.5% (n=4) | 37.5% (n=4) |
