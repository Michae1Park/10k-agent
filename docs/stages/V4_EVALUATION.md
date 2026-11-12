# V4 · Evaluation

How each of the five evaluation layers is run. Rules and pass bars live in the [design doc §12](../V4_TECHNICAL_DESIGN_DOC.md#12-evaluation); results in [V4_RESULTS.md](../V4_RESULTS.md).

## What runs

| Layer | Question | How | Code |
| --- | --- | --- | --- |
| 1 Extraction | Are extracted KPIs right? | Score against 337 gold labels | `evaluation/extraction.py` |
| 2 Model integrity | Does every model tie out? | Integrity checks on every model built | `research/model/checks.py` |
| 3 Forecast | Is the forecast accurate? | Backtest vs baselines, ablations, leakage probe | `python -m era_agent.evaluation.backtest` |
| 4 Valuation | Is it deterministic and sane? | Rerun on identical inputs; WACC sweep | `test_valuation.py`, backtest |
| 5 Note | Is every number traced? | Trace, sections, judge rubric, seeded errors | `python -m era_agent.evaluation.note_eval` |
| Ask, Research | Are answers right? | Agent on gold questions, then an LLM judge | `experiments/v4/ask_run.py`, `research_run.py` |

The judge (gpt-oss-20b) runs after the main model frees the GPU.

## The backtest

Replays history: for each past release, forecast the next quarter using only what was known then.

```text
for each of the 8 companies:
  for each release from Q4 2024 on:          6 per company → 48 per metric
     D = the day after the release
     build the model at D, forecast the next quarter
     score vs the first-reported actual and vs the baselines
```

| | |
| --- | --- |
| **Metrics** | MAPE; bias (positive = too high); guidance call; per-company medians; Wilcoxon test vs each baseline |
| **Ablations** | LLM vs rule extraction; fixed vs fitted fade; guidance mid vs high; proposer on vs off; outside inputs on vs off; one long-horizon run |
| **Leakage probe** | Every forecast input must be filed before D, or the backtest stops |

## Tests

`test_extract.py` (scoring), `test_evaluation.py` (gold validation, retrieval metrics), `test_note.py::seeded_errors_are_caught`, `test_forecast.py::revenue_bridge_adds_up`.
