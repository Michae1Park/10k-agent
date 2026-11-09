# Backtest all-proposer-llm

90 forecasts; gate (MAPE below seasonal naive on revenue and main KPI): **fail**

| | Forecasts | Model MAPE | Naive MAPE | Model bias | Naive bias | Wilcoxon p | Guide anchor MAPE (n) |
|---|---|---|---|---|---|---|---|
| revenue | 48 | 3.78% | 3.77% | 1.09% | 0.64% | 0.600 | 2.54% (13) |
| main_kpi | 48 | 2.90% | 2.58% | 0.39% | 0.17% | 0.315 | 1.64% (31) |

## By company

| Company | Revenue: model / naive MAPE | Main KPI: model / naive MAPE |
|---|---|---|
| ABNB | 1.44% / 3.68% | 1.88% / 1.40% |
| BKNG | 5.28% / 4.41% | 2.77% / 1.77% |
| CART | 1.23% / 1.04% | 1.52% / 1.11% |
| DASH | 1.99% / 3.58% | 1.56% / 2.88% |
| EXPE | 3.99% / 2.90% | 4.43% / 3.21% |
| LYFT | 6.09% / 5.60% | 0.83% / 1.99% |
| TRIP | 8.89% / 5.88% | – / – |
| UBER | 1.30% / 3.07% | 1.31% / 2.37% |

Guided forecasts: 44; actual above the guide anchor (the midpoint unless guidance_point: high) 43, below 1.
