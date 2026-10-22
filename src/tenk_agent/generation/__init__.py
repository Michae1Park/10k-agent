"""V2 generation: a question and its chunks → one model call → a JSON answer whose claims cite
chunk IDs, each claim checked against the chunks it cites.

  generate.py    prompt (prompts/answer.md, the corpus table), one call, one "only the JSON" retry
  answers.py     the reply normalized, each claim verified
  verify.py      each claim's value found in a cited chunk (unit-aware), or its calculation rerun
  calculator.py  safe arithmetic for those reruns

Spec and results: experiments/V2_GENERATION.md. Spec: docs/stages/V2_GENERATION.md.
"""
