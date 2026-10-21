"""The LLM judge: one fixed model (experiments/v2/config.yaml), whatever model is under test.

Numbers are never judged by the LLM; they're scored deterministically. The judge scores only
what has no deterministic check: whether a text answer covers its required points.
"""

from dataclasses import dataclass

from tenk_agent.models import Model, parse_json
from tenk_agent.prompts import PROMPTS

PROMPT = PROMPTS / "judge.md"


@dataclass
class Judge:
    model: Model

    def _ask(self, prompt: str, key: str, expected: int) -> list[bool]:
        response = self.model.complete([{"role": "user", "content": prompt}], max_tokens=2000)
        values = parse_json(response.text).get(key, [])
        if len(values) != expected:
            raise ValueError(f"judge returned {len(values)} verdicts, expected {expected}")
        return [bool(v) for v in values]

    def points_covered(self, question: str, points: list[str], answer: str) -> list[bool]:
        prompt = (
            PROMPT.read_text()
            .strip()
            .replace("{question}", question)
            .replace("{points}", "\n".join(f"{i}. {p}" for i, p in enumerate(points, 1)))
            .replace("{answer}", answer)
        )
        return self._ask(prompt, "covered", len(points))
