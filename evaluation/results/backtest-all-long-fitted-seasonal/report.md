# Backtest all-long-fitted-seasonal

60 forecasts; gate (MAPE below seasonal naive on revenue and main KPI): **fail**

| | Forecasts | Model MAPE | Naive MAPE | Model bias | Naive bias | Wilcoxon p | Guide anchor MAPE (n) |
|---|---|---|---|---|---|---|---|
| revenue | 32 | 3.36% | 4.18% | -0.14% | 2.89% | 0.784 | – (0) |
| main_kpi | 32 | 3.65% | 3.38% | -0.58% | 1.05% | 0.370 | – (0) |

## By company

| Company | Revenue: model / naive MAPE | Main KPI: model / naive MAPE |
|---|---|---|
| ABNB | 3.51% / 2.06% | 2.88% / 3.56% |
| BKNG | 3.20% / 2.60% | 4.43% / 4.55% |
| CART | 1.18% / 0.96% | 1.90% / 1.48% |
| DASH | 4.54% / 3.73% | 6.21% / 4.89% |
| EXPE | 3.75% / 3.19% | 4.26% / 4.43% |
| LYFT | 5.88% / 15.89% | 3.69% / 2.29% |
| TRIP | 1.83% / 3.09% | – / – |
| UBER | 3.02% / 1.93% | 4.03% / 2.76% |

Guided forecasts: 0; actual above the guide anchor (the midpoint unless guidance_point: high) 0, below 0.
