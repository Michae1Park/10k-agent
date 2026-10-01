"""The LLM judge: the same fixed model (TENK_JUDGE_MODEL) whatever model is under test.

Numbers are never judged by the LLM; they're scored deterministically. The judge scores
only what has no deterministic check: narrative correctness against required points,
faithfulness of claims to their cited chunks, and context relevance.
"""

import json
from dataclasses import dataclass

from tenk_agent.models import Model, parse_json

POINTS_PROMPT = """You grade an answer against a list of required points. A point counts as covered only if the answer states it (paraphrase is fine; exact figures must match to the precision given, allowing rounding). Ignore extra material.

Question: {question}

Required points:
{points}

Answer:
{answer}

Reply with only JSON: {{"covered": [true or false for each point, in order], "notes": "one short sentence"}}"""

FAITHFULNESS_PROMPT = """You check whether each claim is supported by the source excerpts it cites. A claim is supported only if the cited excerpts state it or directly imply it (for a calculated figure: the inputs appear in the excerpts). Do not use outside knowledge.

{claims}

Reply with only JSON: {{"supported": [true or false for each claim, in order]}}"""

RELEVANCE_PROMPT = """For each excerpt, say whether it contains information that helps answer the question (not just the same company or topic).

Question: {question}

{excerpts}

Reply with only JSON: {{"relevant": [true or false for each excerpt, in order]}}"""


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
        prompt = POINTS_PROMPT.format(
            question=question,
            points="\n".join(f"{i}. {p}" for i, p in enumerate(points, 1)),
            answer=answer,
        )
        return self._ask(prompt, "covered", len(points))

    def faithfulness(self, claims: list[dict], citations: dict[str, dict]) -> list[bool]:
        blocks = []
        for i, claim in enumerate(claims, 1):
            sources = "\n".join(
                f'<excerpt id="{cid}">\n{citations[cid]["text"]}\n</excerpt>'
                for cid in claim["chunk_ids"]
                if cid in citations
            )
            calculation = (
                f"\nCalculation: {json.dumps(claim['calculation'])}"
                if claim.get("calculation")
                else ""
            )
            blocks.append(f"Claim {i}: {claim['text']}{calculation}\nCited:\n{sources or '(none)'}")
        return self._ask(
            FAITHFULNESS_PROMPT.format(claims="\n\n".join(blocks)), "supported", len(claims)
        )

    def relevance(self, question: str, texts: list[str]) -> list[bool]:
        excerpts = "\n\n".join(f"Excerpt {i}:\n{text[:3000]}" for i, text in enumerate(texts, 1))
        return self._ask(
            RELEVANCE_PROMPT.format(question=question, excerpts=excerpts), "relevant", len(texts)
        )
