"""LLM-judge calibration (V4): hand-grade ~30 answers, then report judge-human agreement.

1. `tenk eval calibrate export <run>` writes eval/calibration/<run>.jsonl: one line per
   judged item (a required point or a claim), with the judge's verdict and "human": null.
2. A person fills in "human": true/false for each line, reading the answer and sources.
3. `tenk eval calibrate score <run>` reports agreement and Cohen's kappa.
"""

import json
import random
from pathlib import Path

from tenk_agent import gold
from tenk_agent.evaluation.runner import RUNS_DIR

CALIBRATION_DIR = Path("eval/calibration")


def export(label: str, n_answers: int = 30, seed: int = 0) -> Path:
    rows = [
        json.loads(line)
        for line in (RUNS_DIR / label / "results.jsonl").read_text().splitlines()
        if line.strip()
    ]
    judged = [r for r in rows if "points_covered" in r or r.get("faithfulness")]
    random.Random(seed).shuffle(judged)
    questions = {q["id"]: q for q in gold.load()}
    items = []
    for row in judged[:n_answers]:
        q = questions[row["id"]]
        answer = row["answer"]
        for point, verdict in zip(
            q.get("required_points", []), row.get("points_covered", []), strict=False
        ):
            items.append(
                {
                    "id": row["id"],
                    "kind": "required_point",
                    "question": q["question"],
                    "answer": answer["answer"],
                    "item": point,
                    "judge": verdict,
                    "human": None,
                }
            )
        verdicts = (row.get("faithfulness") or {}).get("verdicts", {})
        for claim in answer["claims"]:
            if claim["chunk_ids"]:
                items.append(
                    {
                        "id": row["id"],
                        "kind": "claim_supported",
                        "question": q["question"],
                        "item": claim["text"],
                        "chunk_ids": claim["chunk_ids"],
                        "judge": verdicts.get(str(claim["id"])),
                        "human": None,
                    }
                )
    path = CALIBRATION_DIR / f"{label}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(i) + "\n" for i in items))
    return path


def score(label: str) -> dict:
    items = [
        json.loads(line)
        for line in (CALIBRATION_DIR / f"{label}.jsonl").read_text().splitlines()
        if line.strip()
    ]
    pairs = [
        (i["judge"], i["human"])
        for i in items
        if isinstance(i.get("judge"), bool) and isinstance(i.get("human"), bool)
    ]
    if not pairs:
        return {"graded": 0}
    agree = sum(j == h for j, h in pairs) / len(pairs)
    p_judge = sum(j for j, _ in pairs) / len(pairs)
    p_human = sum(h for _, h in pairs) / len(pairs)
    expected = p_judge * p_human + (1 - p_judge) * (1 - p_human)
    kappa = (agree - expected) / (1 - expected) if expected < 1 else 1.0
    return {
        "graded": len(pairs),
        "answers": len({i["id"] for i in items if isinstance(i.get("human"), bool)}),
        "agreement": round(agree, 4),
        "cohens_kappa": round(kappa, 4),
    }
