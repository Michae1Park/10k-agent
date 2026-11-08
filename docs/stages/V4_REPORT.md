# V4 · Report pipeline

How a Report run turns a ticker and date into a note. Rules live in the [design doc §9](../V4_TECHNICAL_DESIGN_DOC.md#9-report-pipeline). For one run end to end, see [V4_WORKED_EXAMPLE.md](V4_WORKED_EXAMPLE.md).

## The runner

<p align="center">
  <img src="../img/v4-report-runner.svg" alt="Report pipeline S1 to S11: code steps and LLM steps, the artifact each writes, which outputs --resume reuses, and the failure policy: integrity and valuation checks stop the run, LLM steps degrade" width="100%">
</p>

`research/pipeline.py` runs eleven fixed steps (S1–S11) in order and logs time, LLM calls and tokens per step to `report.json`.

- **Code steps always rerun.** With `--resume`, LLM outputs already in the folder are reused.
- **The version is saved last.** A failed export saves nothing.

## Writing the note

1. Code builds all ten sections: every table and fixed block.
2. Three prose slots (`takeaways`, `quality`, `valuation`) each get one LLM call, seeing only their own facts.
3. Each slot is traced ([V4_VERIFICATION.md](V4_VERIFICATION.md)), repaired once, and untraced sentences dropped.
4. The consistency check runs and issues are numbered.
5. Sections 1 and 10 are built last, so they can summarize everything.

## The ten sections

| Section | Built by | Content |
| --- | --- | --- |
| 1 Executive summary | code + `takeaways`, last | Price, PT, return, rating, change vs last version |
| 2 Earnings results | code + `quality` | Actual vs estimate vs guidance; guidance vs our forecast; quality of the quarter |
| 3 Drivers and debates | debates | Per driver: bull, bear, hypothesis, trigger, gap |
| 4 Estimate revisions | code | Old vs new, change, source, confidence; integrity results |
| 5 Financial summary | code | Annual, two actual years to the as-of year + 1 (2024A–2027E at a 2026 as-of) |
| 6 Valuation | code + `valuation` | Comps, DCF, scenarios, PT, rating; multiple justification |
| 7 Risks and sensitivity | code | Sensitivity rows |
| 8 Catalysts | code + events | Next earnings date; events with sources |
| 9 Sources, assumptions | code | Sources, key assumptions, insider context, rating definitions |
| 10 Senior analyst review | code, last | Decisions; every open warning and error |

## Consistency check

Runs before export. Code checks that:

- the rating matches its thresholds;
- section 1's numbers match the sections they come from;
- every debated driver has a sensitivity row in section 7;
- a guidance event (S2) has guided lines in section 2.2;
- every warning and error is listed in section 10.

A failure becomes an `error` issue. Only the takeaways get a repair; other sections are never rewritten.

## Tests

`test_note.py`: true figures trace; seeded and swapped figures are caught; prose traces only to its own slot; untraced sentences are dropped; the consistency check catches each kind of mismatch. The pipeline also runs with `--no-llm` on all eight companies. Note scoring: [V4_EVALUATION.md](V4_EVALUATION.md).
