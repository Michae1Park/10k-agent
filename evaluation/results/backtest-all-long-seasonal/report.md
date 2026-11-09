# Backtest all-long-seasonal

60 forecasts; gate (MAPE below seasonal naive on revenue and main KPI): **fail**

| | Forecasts | Model MAPE | Naive MAPE | Model bias | Naive bias | Wilcoxon p | Guide anchor MAPE (n) |
|---|---|---|---|---|---|---|---|
| revenue | 32 | 4.07% | 4.18% | -1.15% | 2.89% | 0.769 | – (0) |
| main_kpi | 32 | 4.38% | 3.38% | -1.53% | 1.05% | 0.103 | – (0) |

## By company

| Company | Revenue: model / naive MAPE | Main KPI: model / naive MAPE |
|---|---|---|
| ABNB | 3.37% / 2.06% | 2.90% / 3.56% |
| BKNG | 3.21% / 2.60% | 3.51% / 4.55% |
| CART | 2.36% / 0.96% | 3.02% / 1.48% |
| DASH | 6.97% / 3.73% | 8.18% / 4.89% |
| EXPE | 3.79% / 3.19% | 4.91% / 4.43% |
| LYFT | 5.45% / 15.89% | 4.06% / 2.29% |
| TRIP | 2.37% / 3.09% | – / – |
| UBER | 5.05% / 1.93% | 6.08% / 2.76% |

Guided forecasts: 0; actual above the guide anchor (the midpoint unless guidance_point: high) 0, below 0.
