# V4 · Report pipeline: how it works

Companion to [design doc §9](../V4_TECHNICAL_DESIGN_DOC.md#9-report-pipeline), which holds the contract (steps, artifacts, rules, note sections, consistency check). This doc does not restate the rules; where the two disagree, the design doc wins.

## The runner

<p align="center">
  <img src="../img/v4-report-runner.svg" alt="Report pipeline S1 to S11: code steps and LLM steps, the artifact each writes, which outputs --resume reuses, and the failure policy: integrity and valuation checks stop the run, LLM steps degrade" width="100%">
</p>

`research/pipeline.py` `run()` looks up the parent version, then runs S1–S11 in order, timing each step (seconds, LLM calls, tokens) into `report.json`. Code steps always rerun; with `--resume` the LLM outputs already in the folder (`events.json`, `debates.json`, prose slots) are reused. S11 writes the files and the `model_versions` row last, so a failed export saves no version.

## The note

`research/note/sections.py` builds the ten sections from the artifacts: code writes every table and fixed block; each prose slot (`takeaways`, `quality`, `valuation`) gets only its own facts. `write.py` makes one LLM call per slot, traces it ([V4_VERIFICATION.md](V4_VERIFICATION.md)), repairs once, and drops what stays untraced. Sections 1 and 10 are built last, after the consistency check numbers the exceptions.

## The note's ten sections

| Section | Built by | Content |
|---|---|---|
| 1 Executive summary | code + slot `takeaways`, **last** | price, PT, return, rating, change vs parent; takeaways from other sections' facts |
| 2 Earnings results | code + slot `quality` | 2.1 actual vs estimate vs guide mid vs y/y; 2.2 guidance vs our forecast; 2.3 quality of the quarter |
| 3 Drivers and debates | S9 | per scenario driver: bull, bear, hypothesis, trigger, gap |
| 4 Estimate revisions | code | old vs new, change, type (source), confidence; 4.3 layer-2 results |
| 5 Financial summary | code | annual 2024A–2028E |
| 6 Valuation | code + slot `valuation` | comps, DCF, scenarios, weighted PT, return, rating; target multiple justification |
| 7 Risks and sensitivity | code | sensitivity rows |
| 8 Catalysts | code + S2 events | next earnings (estimated), events with ids |
| 9 Sources, assumptions | code | sources by date, key assumptions, Form 4 context, rating definitions |
| 10 Senior analyst review | code, **last** | decisions; every open warning and error by X-id |

**Consistency check** (before export; spec §2.3 req. 5): `rating_thresholds`, `section1_numbers`, `debated_drivers`, `guidance_in_2_2`, `open_issues` (run after numbering). Code checks; a failure is an `error` exception and is in `report.json` `consistency`; the takeaways get one repair; other sections are never rewritten (spec §2.3 req. 6).

## Tests

`test_note.py` (13): number figures skip dates, periods and ids; true figures trace; seeded errors are caught; a table cell changed to another fact's value is caught; prose traces to its own slot only; the writer drops sentences still untraced after repair; consistency passes a consistent note and catches each inconsistency. Pipeline runs with `--no-llm` on all eight (build log §3.15). Layer 5: [V4_EVALUATION.md](V4_EVALUATION.md).
