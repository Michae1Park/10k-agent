# V2 · Generation: chunks → one model call → a cited answer

**Builds on** V1: its top 8 chunks go to one model call. **Code:** [`generation/`](../../src/tenk_agent/generation/), prompt [prompts/answer.md](../../src/tenk_agent/prompts/answer.md). **Design and results:** [experiments/V2_GENERATION.md](../../experiments/V2_GENERATION.md): with V1's retrieval, 21 of 30 number questions right, all 8 declines declined, every numeric claim found in a chunk it cites. **Run it:** [Getting started](../GETTING_STARTED.md#4-ask). Overview: [ARCHITECTURE.md](../ARCHITECTURE.md).

## Data

| Data | Where |
|---|---|
| Chunks | V1 (`retrieval.k` of them) from `db` |
| Corpus table (company, fiscal year, period end) | the `filings` table, written into the prompt's `{corpus}` |
| Rules + answer format | [prompts/answer.md](../../src/tenk_agent/prompts/answer.md) (`generation.prompt`) |
| Gold questions | [experiments/v2/testdata/generation-gold-fy2025.jsonl](../../experiments/v2/testdata/generation-gold-fy2025.jsonl) ([format](V1_RETRIEVAL.md#gold-questions)) |

## Knobs

`config.yaml` → `generation:` (and `retrieval.k`); the V2 experiment pins its own in [experiments/v2/config.yaml](../../experiments/v2/config.yaml).

| Knob | What it does | Default |
|---|---|---|
| `model` | `openai:<model>` (vLLM) or `anthropic:<id>`; `--model` overrides | `openai:Qwen/Qwen3.6-27B-FP8` |
| `base_url` | The vLLM server, for `openai:*` models | `http://localhost:8001/v1` |
| `thinking` | Qwen's reasoning mode; `null` = the model's default (on) | false |
| `max_tokens` | Per call | 4,000 |
| `prompt` | The system prompt file | `src/tenk_agent/prompts/answer.md` |
| `retrieval.k` | Chunks in the prompt | 8 |

## Outputs

| Output | Where |
|---|---|
| The chunks shown (id, company, year, Item, kind, pages); the answer, table, scope notice; DECLINED / FORMAT ERROR | terminal (`tenk ask`, `tenk chat`) |
| Claims: status, value and unit, cited chunks, why | terminal; `--json` for the whole answer |
| Seconds, tokens in and out, whether the JSON retry ran | terminal |
| V2 experiment: per question and context, the chunks, answer, claims, scores and raw replies | `output/generation/<label>/results.json` |
| V2 experiment: per context, correct, declines, numbers in claims, grounded claims, gold cited | terminal, `output/generation/<label>/scores.json` |

## How it works

```
question ─► V1: top 8 chunks ─► prompt ─► one model call ─► parse JSON (one retry) ─► check each claim ─► answer
```

| Step | Code | What happens |
|---|---|---|
| 1. Retrieve | `retrieval/search.py` | V1's pipeline, unchanged |
| 2. Prompt | `generation/generate.py` | System: the rules, the corpus table and the answer format. User: each chunk as `<chunk id="AAPL-FY2025-8-002">` under a line naming company, fiscal year, period end, Item, kind and units; then the question. No chunks: `Excerpts: (none)` |
| 3. Call | `generate` | One `Model.complete` at temperature 0, thinking off; if the reply isn't JSON, one "Reply again with only the JSON object" retry, else a format error |
| 4. Finalize | `generation/answers.py` | The JSON normalized: claims with their value, unit, company, fiscal year, chunk IDs and calculation; table, scope notice, abstained |
| 5. Check | `generation/verify.py`, `calculator.py` | Each claim labeled, deterministically; the answer itself is not changed |

**The rules the model gets** ([answer.md](../../src/tenk_agent/prompts/answer.md)):

| Case | Example | Required behavior |
|---|---|---|
| Every number from an excerpt | — | Never from memory; cite the chunk |
| Ambiguous fiscal year | "NVIDIA's revenue in 2024" | Pick the fiscal year that ended in (or mostly covers) that year; say which (NVIDIA's FY2025 ended Jan 2025) |
| Several years side by side | three columns of a statement | Take the requested year's column |
| Comparing companies | Apple vs Netflix (thousands) | Same unit; say which fiscal periods |
| Can't answer | "revenue in 2027", Oracle, no excerpts | Say what is missing, `abstained: true`, state what the corpus covers |
| False premise | — | Correct it from the excerpts |
| Advice, prices, forecasts | "Should I buy NVIDIA stock?" | Decline |

**The answer** (JSON):

| Field | Meaning |
|---|---|
| `answer` | Plain language with `[n]` markers |
| `claims[]` | One per number or fact: `text`, `value` (as the excerpt shows it), `unit`, `company`, `fiscal_year` (the column's), `chunk_ids`, `calculation` (`op`, `inputs`, `input_unit`, `result`) for a derived number |
| `table` | Optional `{columns, rows}` for comparisons |
| `scope_notice` | What the corpus covers, when part of the question falls outside it |
| `abstained` | `true` when the excerpts can't answer |

**Claim status** (`verify.py`):

| Status | When |
|---|---|
| `verified` | The value is in a cited chunk, after unit normalization and to the precision either side is written with ("$31.4 billion" matches 31,370 in millions) |
| `calculated` | Rerunning the calculation reproduces the value, and every input is in a cited chunk |
| `cited` | No number; the cited chunk IDs exist |
| `unverified` | Anything else, with the reason: value not found, calculation gives another result, unknown chunk ID, or (parent-child database) the value is only in another company's chunks |

About 28 s per answer with 4 in parallel on the L40S (the V2 experiment); `tenk ask` is one call.

## Gotchas

- **A number only in the answer text has no status.** Only claims are checked; a claim with several numbers (`"value": null`) can't be verified.
- **No tools.** The model does arithmetic itself; the check reruns it and marks a wrong result `unverified`, but doesn't correct it.
- **Thinking mode.** Leave `thinking: false` for interactive use: Qwen with reasoning on takes minutes per answer.
- **One server at a time.** The judge (`gpt-oss-20b`) doesn't fit beside the 27B: generate first, then stop it, serve the judge and run `judge.py`.
