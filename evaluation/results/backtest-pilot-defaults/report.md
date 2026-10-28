# Backtest pilot-defaults

24 forecasts; gate (MAPE below seasonal naive on revenue and main KPI): **pass**

| | Forecasts | Model MAPE | Naive MAPE | Model bias | Naive bias | Wilcoxon p | Guide-mid MAPE (n) |
|---|---|---|---|---|---|---|---|
| revenue | 12 | 2.82% | 3.74% | -0.98% | 1.15% | 0.233 | 3.11% (4) |
| main_kpi | 12 | 1.88% | 2.07% | -1.00% | 0.18% | 0.765 | 1.71% (10) |

## By company

| Company | Revenue: model / naive MAPE | Main KPI: model / naive MAPE |
|---|---|---|
| BKNG | 4.04% / 4.41% | 2.44% / 1.77% |
| UBER | 1.59% / 3.07% | 1.31% / 2.37% |

Guided forecasts: 14; actual above the guided midpoint 13, below 1.
