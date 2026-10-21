"""The LLM prompts, one Markdown file per task, with its output schema beside it (same stem):

  structure.md, structure.schema.json   V0 structure agent: system prompt, window and repair
                                        templates; the JSON schema its labels are decoded with
  answer.md                             V2 generation: rules, {corpus} table, answer format
  judge.md                              V2 judge: an answer's coverage of its required points

Placeholders ({corpus}, {question}, …) are filled by plain substitution, so braces in the JSON
examples are literal.
"""

from pathlib import Path

PROMPTS = Path(__file__).resolve().parent
