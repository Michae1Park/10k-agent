# Backtest all-guidance-high

90 forecasts; gate (MAPE below seasonal naive on revenue and main KPI): **pass**

| | Forecasts | Model MAPE | Naive MAPE | Model bias | Naive bias | Wilcoxon p | Guide-mid MAPE (n) |
|---|---|---|---|---|---|---|---|
| revenue | 48 | 3.00% | 3.77% | 0.68% | 0.64% | 0.011 | 1.67% (13) |
| main_kpi | 48 | 2.01% | 2.58% | 0.50% | 0.17% | 0.011 | 1.19% (31) |

## By company

| Company | Revenue: model / naive MAPE | Main KPI: model / naive MAPE |
|---|---|---|
| ABNB | 0.68% / 3.68% | 1.40% / 1.40% |
| BKNG | 3.46% / 4.41% | 2.13% / 1.77% |
| CART | 1.09% / 1.04% | 0.85% / 1.11% |
| DASH | 2.18% / 3.58% | 1.01% / 2.88% |
| EXPE | 3.03% / 2.90% | 2.98% / 3.21% |
| LYFT | 5.72% / 5.60% | 1.11% / 1.99% |
| TRIP | 5.88% / 5.88% | – / – |
| UBER | 1.95% / 3.07% | 0.71% / 2.37% |

Guided forecasts: 44; actual above the guided midpoint 31, below 13.
