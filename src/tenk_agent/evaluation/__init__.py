"""Evaluation shared by the experiments: gold question sets, metrics and the LLM judge.

  gold.py       load, review and resolve gold questions to the chunks holding their evidence
  render.py     a gold set as a PDF for human review (tenk gold render)
  retrieval.py  V1: Recall@k and MRR against the gold chunks
  scoring.py    V2: expected numbers matched in an answer, within tolerance
  judge.py      V2: text answers against their required points

Each version's own scoring (V0's ground truth, V2's grading) is in experiments/v*/score.py.
"""
