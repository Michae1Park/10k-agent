# Stage 5 — Verify: is every number really in what it cites?

## Commands

Workstation terminal, repo root. No GPU, no model.

```bash
source .venv/bin/activate
python playground/verify.py --chunk AAPL-FY2024-8-002 --value 31370 --unit "USD millions"
python playground/verify.py --chunk AAPL-FY2024-8-002 --value 31.37 --unit "USD billions"     # unit normalization
python playground/verify.py --chunk AAPL-FY2024-8-002 --value 31500 --unit "USD millions"     # close, but wrong
python playground/verify.py --chunk AAPL-FY2025-8-002 --calc pct_change old=31370 new=34550 --value 10.1
python playground/verify.py --trace <trace id>                                               # re-check a stored answer
python playground/verify.py --help
```
Prints each claim's status and why, plus the numbers found in each cited chunk; saves `output/verify/<tag>.md` with every number the parser read from the chunk.

## Data

| Data | Where |
|---|---|
| Claims | the model's answer (stages 3–4), or `--chunk / --value / --unit / --calc` |
| Cited chunks | `data/tenk.db` |
| Stored answers | `traces` table (`--trace`; trace IDs are printed by `tenk ask` / `tenk research`) |

## Knobs

Defaults: `config.yaml` → `verify:`.

| Knob | What it does |
|---|---|
| `enabled` | Label every claim; off = every claim "unchecked" |
| `repair` | Research mode: send failed claims back to the agent once |
| `repair_calls` | Extra tool calls in that round (default 4) |

Matching has no tolerance knob on purpose: numbers match to the precision they're written with ([D-018](DECISIONS.md#d-018)).

## Outputs

| Output | Where |
|---|---|
| Status per claim: verified / calculated / cited / unverified, and why | terminal, report |
| Per cited chunk: how many numbers, which scales bare numbers get, the matched span | terminal |
| Every number read from the chunk: span, value, word scale ("billion"), percent | `output/verify/<tag>.md` |

## How it works

`tenk_agent.verify.verify_claim()`:

| Claim | Status if… | Otherwise |
|---|---|---|
| Has a `calculation` | it re-runs to the claimed value **and** every input is in a cited chunk → **calculated** | unverified + reason |
| Has a `value` | a cited chunk shows it, after unit normalization → **verified** | unverified |
| Text only | its chunk IDs exist → **cited** (faithfulness is the judge's job, [EVAL.md](EVAL.md)) | unverified |

**Reading numbers from a chunk** (`numbers_in`): `31,370` · `(3,105)` = −3,105 · `$1.2 billion` (word scale) · `26.2%`.

**Unit normalization** (`find_value`): both sides go to absolute units. The claim's unit gives its scale ("USD billions" = 10⁹); a bare number in the chunk gets the chunk's unit line ("in millions"), or every scale if it has none.

**Precision, not percent.** Two numbers match when they agree to the precision *either* is written with:

| Claim | Chunk shows | Match? | Why |
|---|---|---|---|
| 31,370 USD millions | 31,370 (in millions) | ✓ | exact |
| 31.37 USD billions | 31,370 (in millions) | ✓ | same number, other unit |
| 31.4 USD billions | 31,370 (in millions) | ✓ | the claim is written to ±0.05 billion |
| 1,234 USD millions | "$1.2 billion" | ✓ | the source is written to ±0.05 billion |
| 31,500 USD millions | 31,370 (in millions) | ✗ | a different figure (accepted before [D-018](DECISIONS.md#d-018)) |

**Repair** (Research mode, `verify.repair`): failed claims and their reasons go back to the agent once: "find the chunk that actually contains the value and cite it, correct the value, fix the inputs, or drop the number". The new answer is verified again; whatever still fails stays labeled unverified, never hidden ([D-015](DECISIONS.md#d-015)).

**Results:** citation accuracy (numeric claims verified or calculated) 95.2% without repair, 95.9% with it (Qwen3.5-9B, `test`); one more question answered correctly at +43% latency.

## Things to try

| Try | Question |
|---|---|
| `--value 31.37 --unit "USD billions"` then `--unit "USD millions"` | What does the unit change? |
| `--value 31500` | Why is "close" not good enough? |
| `--calc pct_change old=31370 new=34550 --value 10` | Is "10%" an acceptable rounding of 10.14%? |
| `--chunk NFLX-FY2025-8-006 --value 45.2 --unit "USD billions"` | Netflix reports in thousands: does it still match? |
| `--trace` on a Research answer | Which claims are only "cited", and what would check them? |

## Gotchas

- **Verified ≠ correct.** It proves the cited chunk shows the number, not that it's the right year's column or the right metric. Correctness against the gold set is [EVAL.md](EVAL.md)'s job.
- A year (2024) inside a chunk is a number too; a claim of "2,024" would match it. Rare, harmless so far.
- Narrative claims are only "cited": checking that the text supports them needs the LLM judge.
