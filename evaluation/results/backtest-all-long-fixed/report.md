# Backtest all-long-fixed

60 forecasts; gate (MAPE below seasonal naive on revenue and main KPI): **fail**

| | Forecasts | Model MAPE | Naive MAPE | Model bias | Naive bias | Wilcoxon p | Guide anchor MAPE (n) |
|---|---|---|---|---|---|---|---|
| revenue | 32 | 4.03% | 4.18% | -1.15% | 2.89% | 0.624 | – (0) |
| main_kpi | 32 | 4.18% | 3.38% | -1.51% | 1.05% | 0.304 | – (0) |

## By company

| Company | Revenue: model / naive MAPE | Main KPI: model / naive MAPE |
|---|---|---|
| ABNB | 3.43% / 2.06% | 2.35% / 3.56% |
| BKNG | 3.23% / 2.60% | 2.87% / 4.55% |
| CART | 2.36% / 0.96% | 3.02% / 1.48% |
| DASH | 7.04% / 3.73% | 8.25% / 4.89% |
| EXPE | 2.97% / 3.19% | 4.07% / 4.43% |
| LYFT | 5.45% / 15.89% | 4.06% / 2.29% |
| TRIP | 2.60% / 3.09% | – / – |
| UBER | 5.17% / 1.93% | 6.25% / 2.76% |

Guided forecasts: 0; actual above the guide anchor (the midpoint unless guidance_point: high) 0, below 0.
