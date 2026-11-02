# V4 · Evaluation: how it works

Companion to [design doc §12](../V4_TECHNICAL_DESIGN_DOC.md#12-evaluation), which holds the contract (gold files, layers, frozen bars, backtest rules). Results: [V4_RESULTS.md](../V4_RESULTS.md). This doc does not restate the rules; where the two disagree, the design doc wins.

## Runs

| Layer | Run | Code |
|---|---|---|
| 1 Extraction | score the stored rows against 337 gold labels | `evaluation/extraction.py` |
| 2 Model integrity | the layer-2 checks on every model built, plus unit tests | `research/model/checks.py` |
| 3 Forecast | walk-forward backtest, baselines, ablations, leakage probe | `python -m tenk_agent.evaluation.backtest` |
| 4 Valuation | rerun on identical inputs, compare bytes; WACC sweep | `test_valuation.py`, backtest |
| 5 Note | trace, sections, judge rubric, seeded errors | `python -m tenk_agent.evaluation.note_eval` |
| Ask, Research | the agent on gold, then the judge as a second pass | `experiments/v4/ask_run.py`, `research_run.py` |

The judge (gpt-oss-20b) runs after vLLM releases the GPU. The leakage probe checks every forecast input's `filed` against D and stops the backtest on a miss.

## Tests

`test_extract.py::score_kpis_guidance_and_critical`; `test_evaluation.py` (gold validation and verification marking, retrieval metrics); `test_note.py::seeded_errors_are_caught`; `test_forecast.py::revenue_bridge_adds_up`.
