"""Evaluation shared by the experiments: gold question sets, metrics, scoring and reports.

  gold.py               load, review and resolve gold questions to the chunks holding their evidence
  render.py             a gold set as a PDF for human review (tenk gold render)
  retrieval_metrics.py  V1: Recall@k and MRR against the gold chunks
  answer_metrics.py     V2: expected numbers matched in an answer, within tolerance
  judge.py              V2: text answers against their required points (the LLM judge)
  scoring.py            V2: one answer's scores (correct, grounded, declined) and a run's summary
  report.py             a finished V2 run as a Markdown report (tenk report)

The gold sets and the reports are in evaluation/ at the repo root. The experiments' run.py
scripts only run the pipeline and call this package to score it.
"""
