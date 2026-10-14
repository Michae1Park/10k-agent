# Comparison: V1 vs V7-r1 vs V7-r2 vs V7-r3

- **V1**: `v1-retrieval-dev-20261014` (None, 69 questions)
- **V7-r1**: `v7-r1-parents-retrieval-dev-20261014` (None, 69 questions)
- **V7-r2**: `v7-r2-children-retrieval-dev-20261014` (None, 69 questions)
- **V7-r3**: `v7-r3-hybrid-retrieval-dev-20261014` (None, 69 questions)

Baseline: V1. Intervals are 95% bootstrap over questions; p-values are paired sign-flip permutation tests on the questions both runs answered, and a difference reads as better or worse only at p < 0.05. A ±5-point interval on Recall@5 needs ~371 questions.

**Provisional:** includes unverified gold questions.

## Metrics

| Metric | V1 | V7-r1 | V7-r2 | V7-r3 |
| --- | --- | --- | --- | --- |
| Recall@5 | 59.3% [48.4%, 69.4%] | 58.3% [47.6%, 69.2%] | 74.8% [65.2%, 83.0%] | 74.0% [64.1%, 82.9%] |
| Recall@10 | 83.1% [75.2%, 90.4%] | 84.0% [76.2%, 91.2%] | 87.6% [80.5%, 93.9%] | 84.6% [76.7%, 91.5%] |
| MRR | 0.45 [0.37, 0.54] | 0.46 [0.38, 0.55] | 0.57 [0.48, 0.65] | 0.56 [0.48, 0.65] |

## Differences vs V1

| Metric | Run | Difference [95% CI] | p | Reading |
| --- | --- | --- | --- | --- |
| Recall@5 | V7-r1 | -1.0 pt [-5.9 pt, +4.2 pt] | 0.800 | no clear difference |
| Recall@5 | V7-r2 | +15.4 pt [+6.4 pt, +24.6 pt] | 0.001 | better |
| Recall@5 | V7-r3 | +14.7 pt [+5.7 pt, +24.2 pt] | 0.004 | better |
| Recall@10 | V7-r1 | +0.9 pt [-1.5 pt, +3.3 pt] | 0.620 | no clear difference |
| Recall@10 | V7-r2 | +4.5 pt [-0.3 pt, +10.1 pt] | 0.103 | no clear difference |
| Recall@10 | V7-r3 | +1.4 pt [-4.1 pt, +6.9 pt] | 0.644 | no clear difference |
| MRR | V7-r1 | +0.01 [-0.04, +0.06] | 0.703 | no clear difference |
| MRR | V7-r2 | +0.11 [+0.04, +0.18] | 0.002 | better |
| MRR | V7-r3 | +0.11 [+0.04, +0.18] | 0.001 | better |

## Question-level changes (Recall@5)

| Run | Fixed | Regressed | Both pass | Both fail | McNemar p |
| --- | --- | --- | --- | --- | --- |
| V7-r1 | 1 | 2 | 31 | 34 | 1.000 |
| V7-r2 | 14 | 2 | 31 | 21 | 0.004 |
| V7-r3 | 13 | 2 | 31 | 22 | 0.007 |

**V7-r1**: `googl-antitrust-fy2025` regressed (retrieval miss), `nflx-tsla-net-income-fy2024-units` regressed (retrieval miss), `nvda-revenue-fy2023` fixed

**V7-r2**: `msft-revenue-decrease-fy2024-premise` regressed (retrieval miss), `nflx-tsla-net-income-fy2024-units` regressed (retrieval miss), `aapl-googl-meta-net-income-fy2024` fixed, `amzn-capex-fy2025` fixed, `amzn-operating-income-fy2023-2025` fixed, `googl-cloud-revenue-fy2025` fixed, `meta-operating-margin-fy2025` fixed, `meta-rd-share-fy2025` fixed, `meta-revenue-fy2023-2025` fixed, `msft-capex-fy2025` fixed, `msft-capex-growth-fy2025` fixed, `msft-rd-2024-ambiguous` fixed, `nflx-meta-revenue-fy2025-units` fixed, `nvda-rd-fy2025` fixed, `nvda-revenue-fy2023` fixed, `nvda-revenue-growth-fy2025` fixed

**V7-r3**: `msft-revenue-decrease-fy2024-premise` regressed (retrieval miss), `nflx-tsla-net-income-fy2024-units` regressed (retrieval miss), `aapl-googl-meta-net-income-fy2024` fixed, `amzn-capex-fy2025` fixed, `amzn-operating-income-fy2023-2025` fixed, `googl-cloud-revenue-fy2025` fixed, `meta-operating-margin-fy2025` fixed, `meta-rd-share-fy2025` fixed, `meta-revenue-fy2023-2025` fixed, `msft-capex-fy2025` fixed, `msft-capex-growth-fy2025` fixed, `msft-rd-2024-ambiguous` fixed, `nflx-meta-revenue-fy2025-units` fixed, `nvda-rd-fy2025` fixed, `nvda-revenue-fy2023` fixed

## Failure causes

| Cause | V1 | V7-r1 | V7-r2 | V7-r3 |
| --- | --- | --- | --- | --- |
| retrieval miss | 35 | 36 | 23 | 24 |

## Recall@5 by category

| Category | V1 | V7-r1 | V7-r2 | V7-r3 |
| --- | --- | --- | --- | --- |
| agent_workflow | 23.9% (n=7) | 23.9% (n=7) | 23.9% (n=7) | 23.9% (n=7) |
| failure_case | 61.1% (n=10) | 61.1% (n=10) | 72.2% (n=10) | 72.2% (n=10) |
| grounding | 90.7% (n=9) | 79.6% (n=9) | 90.7% (n=9) | 90.7% (n=9) |
| multi_document | 40.9% (n=11) | 34.8% (n=11) | 50.0% (n=11) | 54.5% (n=11) |
| retrieval | 71.4% (n=21) | 76.2% (n=21) | 95.2% (n=21) | 95.2% (n=21) |
| tool_use | 50.0% (n=11) | 50.0% (n=11) | 81.8% (n=11) | 72.7% (n=11) |
