"""Layer 5, note quality (V4 design doc §13.1): ten sections, number trace, seeded errors, rubric.

  taskset -c 16-31 uv run python -m tenk_agent.evaluation.note_eval output/reports/UBER/2026-08-06 …
  … --judge openai:openai/gpt-oss-20b --judge-url http://localhost:8002/v1

  sections   all ten present, in order
  traced     every number in the note body traces to a fact (research/pipeline.lint)
  seeded     corrupt a table figure or a prose figure (+7%, and a changed magnitude) and the lint
             (number trace plus the tables as code printed them) must catch each one
  rubric     the judge scores five criteria 1–5 (frozen 2026-10-28, before any run; the spec
             names a rubric but not its criteria): grounded, complete, analysis, balance,
             clarity. A human sample is the user's (not run here)
Bars: 100% traced; rubric average ≥ 4 of 5; every seeded error caught.
"""

import argparse
import json
import random
import re
from pathlib import Path

from tenk_agent.research.note.facts import Fact, Facts
from tenk_agent.research.note.sections import TITLES
from tenk_agent.research.note.trace import figures
from tenk_agent.research.pipeline import lint

RUBRIC = {
    "grounded": "Every statement is supported by the note's own tables, facts and sources; "
                "nothing is asserted without support.",
    "complete": "Each section does its job: results against estimates and guidance, drivers and "
                "debates, revisions, financial summary, valuation, risks, catalysts, sources, "
                "review items.",
    "analysis": "It explains what drove the quarter and the estimates (volume, price, take "
                "rate, margin), not only what the numbers are.",
    "balance": "Risks, open questions and the thesis are stated as such; hypotheses are labelled; "
               "it is a draft for review, not advice.",
    "clarity": "Plain, brief, readable by a senior analyst; tables are labelled and consistent.",
}


def sections_present(note: str) -> bool:
    found = re.findall(r"^## (\d+)\. (.+)$", note, re.MULTILINE)
    return [int(n) for n, _ in found][:10] == list(range(1, 11)) and [
        t.lower() for _, t in found][:10] == [t.lower() for t in TITLES]


def seeded(note: str, facts: Facts, seeds: int, rng: random.Random,
           tables: list[str] | None = None) -> tuple[int, int]:
    """(caught, planted): corrupt one figure of the body at a time (×1.07, then ×10) and count
    the corruptions the lint flags. Seeds are drawn from the figures the trace reads (never a
    date or section number), at least 1 in size."""
    body = note.split("\n## EVIDENCE LOG")[0]
    lines = body.splitlines()
    candidates = [(i, raw) for i, line in enumerate(lines) if not line.startswith("#")
                  for raw, value, _ in figures(line) if abs(value) >= 1]
    caught = planted = 0
    for i, raw in rng.sample(candidates, min(seeds, len(candidates))):
        digits = re.search(r"\d[\d,]*(?:\.\d+)?", raw).group(0)
        value = float(digits.replace(",", ""))
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        for wrong in (value * 1.07, value * 10):
            text = f"{wrong:,.{decimals}f}" if "," in digits else f"{wrong:.{decimals}f}"
            if text == digits:
                continue
            changed = list(lines)
            changed[i] = lines[i].replace(raw, raw.replace(digits, text, 1), 1)
            planted += 1
            caught += bool(lint("\n".join(changed), facts, tables))
    return caught, planted


def rubric(note: str, llm) -> dict:
    from tenk_agent.models import parse_json

    criteria = "\n".join(f"- {k}: {v}" for k, v in RUBRIC.items())
    prompt = ("Score this draft equity research note on each criterion from 1 (poor) to 5 "
              f"(excellent).\n\nCriteria:\n{criteria}\n\nReply with only a JSON object: "
              '{"grounded": {"score": n, "reason": "..."}, …}\n\nNote:\n\n'
              + note.split("\n## EVIDENCE LOG")[0])
    reply = llm.complete([{"role": "user", "content": prompt}], max_tokens=4000)
    scores = parse_json(reply.text)
    return {k: scores.get(k, {}) for k in RUBRIC}


def evaluate(folder: Path, llm=None, seeds: int = 10) -> dict:
    note = (folder / "note.md").read_text()
    facts = Facts([Fact(**f) for f in json.loads((folder / "facts.json").read_text())])
    tables = json.loads((folder / "note.json").read_text()).get("tables") or []
    untraced = lint(note.split("\n## EVIDENCE LOG")[0], facts, tables)
    caught, planted = seeded(note, facts, seeds, random.Random(folder.name), tables)
    out = {"folder": str(folder), "sections": sections_present(note),
           "untraced": [u.text for u in untraced], "seeded_caught": caught,
           "seeded_planted": planted}
    if llm is not None:
        scores = rubric(note, llm)
        values = [v.get("score") for v in scores.values()
                  if isinstance(v.get("score"), int | float)]
        out |= {"rubric": scores, "rubric_mean": sum(values) / len(values) if values else None}
    out["pass"] = (out["sections"] and not untraced and caught == planted
                   and (llm is None or (out.get("rubric_mean") or 0) >= 4))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folders", nargs="+", type=Path)
    ap.add_argument("--judge", help="e.g. openai:openai/gpt-oss-20b")
    ap.add_argument("--judge-url", default="http://localhost:8002/v1")
    ap.add_argument("--seeds", type=int, default=10)
    args = ap.parse_args()
    llm = None
    if args.judge:
        from tenk_agent.models import get_model

        llm = get_model(args.judge, base_url=args.judge_url)
    results = [evaluate(f, llm, args.seeds) for f in args.folders]
    for r in results:
        print(json.dumps({k: v for k, v in r.items() if k != "rubric"}))
    (args.folders[0].parents[1] / "note_eval.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
