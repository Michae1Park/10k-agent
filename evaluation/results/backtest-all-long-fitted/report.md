# Backtest all-long-fitted

60 forecasts; gate (MAPE below seasonal naive on revenue and main KPI): **fail**

| | Forecasts | Model MAPE | Naive MAPE | Model bias | Naive bias | Wilcoxon p | Guide anchor MAPE (n) |
|---|---|---|---|---|---|---|---|
| revenue | 32 | 3.29% | 4.18% | -0.14% | 2.89% | 0.845 | – (0) |
| main_kpi | 32 | 3.73% | 3.38% | -0.55% | 1.05% | 0.278 | – (0) |

## By company

| Company | Revenue: model / naive MAPE | Main KPI: model / naive MAPE |
|---|---|---|
| ABNB | 3.57% / 2.06% | 2.33% / 3.56% |
| BKNG | 2.44% / 2.60% | 4.55% / 4.55% |
| CART | 1.18% / 0.96% | 1.90% / 1.48% |
| DASH | 4.61% / 3.73% | 6.28% / 4.89% |
| EXPE | 2.99% / 3.19% | 4.43% / 4.43% |
| LYFT | 5.88% / 15.89% | 3.69% / 2.29% |
| TRIP | 2.50% / 3.09% | – / – |
| UBER | 3.14% / 1.93% | 4.20% / 2.76% |

Guided forecasts: 0; actual above the guide anchor (the midpoint unless guidance_point: high) 0, below 0.
