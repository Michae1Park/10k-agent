# Backtest all-defaults

90 forecasts; gate (MAPE below seasonal naive on revenue and main KPI): **pass**

| | Forecasts | Model MAPE | Naive MAPE | Model bias | Naive bias | Wilcoxon p | Guide-mid MAPE (n) |
|---|---|---|---|---|---|---|---|
| revenue | 48 | 3.14% | 3.77% | -0.15% | 0.64% | 0.145 | 2.54% (13) |
| main_kpi | 48 | 2.30% | 2.58% | -0.23% | 0.17% | 0.706 | 1.64% (31) |

## By company

| Company | Revenue: model / naive MAPE | Main KPI: model / naive MAPE |
|---|---|---|
| ABNB | 1.42% / 3.68% | 1.40% / 1.40% |
| BKNG | 4.04% / 4.41% | 2.44% / 1.77% |
| CART | 1.53% / 1.04% | 1.52% / 1.11% |
| DASH | 2.19% / 3.58% | 1.56% / 2.88% |
| EXPE | 3.46% / 2.90% | 3.42% / 3.21% |
| LYFT | 5.02% / 5.60% | 0.83% / 1.99% |
| TRIP | 5.88% / 5.88% | – / – |
| UBER | 1.59% / 3.07% | 1.31% / 2.37% |

Guided forecasts: 44; actual above the guided midpoint 43, below 1.
