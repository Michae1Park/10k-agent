# V4 · Verification and number trace

How every number the system prints is checked against its source. Rules and the id format live in the [design doc §10](../V4_TECHNICAL_DESIGN_DOC.md#10-verification-and-number-trace).

There are two checks: one for agent answers, one for the note.

## Claim check (Ask and Research answers)

For each claim in an answer, `generation/verify.py`:

1. Looks up its cited ids at the run's as-of (`research/refs.py`).
2. Checks the claimed value matches, allowing for units and rounding.
3. Reruns any stated calculation from the looked-up inputs.

## Number trace (the note)

<p align="center">
  <img src="../img/v4-number-trace.svg" alt="Number trace: fact list from artifacts, writer marks facts, trace.py checks every number, one repair turn, failed-check section, tables rendered by code" width="100%">
</p>

The LLM never types a raw number; it cites facts code prepared.

1. **Code** writes `facts.json`: every printable figure, pre-formatted, with its source id.
2. **The writer** produces prose that marks each figure, e.g. `{F-2}`.
3. **`trace.py`** finds every number in the text (signs, scales, percentages). Each must be a marked fact for that slot or an allowed token (like a year).
4. **On failure:** one repair turn. Still failing → the sentence is dropped and logged as an error.

## Tests

| File | Checks |
| --- | --- |
| `test_refs.py` | Every id kind parses and resolves |
| `test_generation.py` | Number finding; unit and rounding handling |
| `test_agent_tools.py`, `test_ask.py` | Claims verify; another company's fact doesn't |
| `test_note.py` | Trace and seeded errors ([V4_REPORT.md](V4_REPORT.md)) |
