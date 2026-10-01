# Stage 3 — Answer (Ask mode): chunks → one model call → cited claims

## Commands

Workstation terminal, repo root. Needs a model: Claude (`ANTHROPIC_API_KEY`) or an open model on vLLM ([SERVING.md](SERVING.md)).

```bash
source .venv/bin/activate
python playground/answer.py "What was Apple's revenue in 2024?"
python playground/answer.py --gold nvda-revenue-2024-shifted                    # gold question, scored
python playground/answer.py "What will Apple's revenue be in 2027?"             # should decline
python playground/answer.py "What was Apple's revenue in 2024?" --dry-run       # the exact prompt, no model call
python playground/answer.py "..." --model openai:Qwen/Qwen3.5-9B                # open model on vLLM :8001
python playground/answer.py --help

tenk ask "What was Netflix's revenue in 2024?"                                  # the product command
tenk eval run ask --split dev --include-unverified --no-judge                   # V2 over the gold set
```
The playground prints the retrieved chunks, the answer and a claims table, and saves `output/answer/<tag>.md` with the system prompt, the user message, the raw reply and the verified claims.

## Data

| Data | Where |
|---|---|
| Retrieved chunks | stage 2 (`answer.k` of them) |
| Corpus table (company, fiscal year, period end) | `filings` table, written into the system prompt |
| Rules + answer format | `src/tenk_agent/answers.py` (`RULES`, `ANSWER_FORMAT`) |
| **Answer** | returned; trace in `traces` (see it in the UI, or `tenk serve` → `/api/traces/<id>`) |

## Knobs

Defaults: `config.yaml` → `answer:`, `model:`. Flags override per run.

| Knob | Flag | What it does | Try |
|---|---|---|---|
| `answer.k` | `--k` | Chunks in the prompt | 4: cheaper, misses multi-year questions |
| `model.name` | `--model` | `anthropic:<id>` or `openai:<model>` | `openai:Qwen/Qwen3.5-9B` |
| `model.thinking` | `TENK_OPENAI_THINKING` | Qwen's reasoning mode; `false` = off | on: 286 s and 7.9k output tokens for one answer; off: 7 s |
| `verify.enabled` | `--no-verify` | Label claims (stage 5) | off: every claim "unchecked" |

## Outputs

| Output | Where |
|---|---|
| Retrieved chunk IDs, prompt size | terminal |
| Model, latency, tokens, cost | terminal |
| Answer text with [n] markers, scope notice, abstained | terminal, report |
| Claims: status, value, unit, cited chunks, why | terminal, report |
| Gold expected values and score (`--gold`) | terminal, report |
| System prompt, user message, raw reply | `output/answer/<tag>.md` |

## How it works

`tenk_agent.ask.ask()`:

```
question ─► retrieve (stage 2) ─► prompt ─► one model call ─► parse JSON ─► verify (stage 5) ─► answer + trace
```

| Step | What happens |
|---|---|
| 1. Retrieve | `answer.k` chunks, slot search + rerank |
| 2. Prompt | System: rules, the corpus table, the answer format. User: each chunk as `<chunk id="AAPL-FY2024-8-002">` with company, fiscal year, period end, Item, kind, units; then the question |
| 3. Call | One `complete()`; if the reply isn't JSON, one "reply with only the JSON" retry |
| 4. Finalize | Claims normalized, verified (stage 5), citations get company, fiscal year, section title and EDGAR link |

**The rules the model gets** (`answers.RULES`):

| Case | Example | Required behavior |
|---|---|---|
| Every number from a source | — | Never from memory; cite the chunk |
| Ambiguous fiscal year | "Apple's revenue in 2024" | Use the fiscal year ending in that year; say which |
| Shifted fiscal year | "NVIDIA's revenue in 2024" | FY2025 ended Jan 2025 and mostly covers 2024; say so |
| Similar numbers | three years side by side | Take the requested year's column |
| Cross-company units | Apple vs Netflix (thousands) | Same unit and say which periods before comparing |
| Entity naming | "Google", "Facebook" | Alphabet, Meta |
| Unsupported | "revenue in 2027", "Oracle" | Decline (`abstained: true`); state what the corpus covers |
| False premise | "Why did Netflix stop reporting subscribers in 2019?" | Correct it from the filings |

**The answer format** (JSON, same in both modes):

| Field | Meaning |
|---|---|
| `answer` | Plain-language answer with `[n]` markers |
| `claims[]` | One per number or fact: `text`, `value` (as the source shows it), `unit`, `company`, `fiscal_year`, `chunk_ids`, `calculation` |
| `table` | Optional rows × columns for comparisons |
| `scope_notice` | What the corpus covers, when part of the question falls outside it |
| `abstained` | `true` when the filings can't answer |

**Results** (V2, Qwen3.5-9B, `test`, draft gold): correctness **71.4%** (21 scored), numeric citation accuracy 97.5%, 20.6 s mean with 4 parallel requests. By category: retrieval 100%, multi-document 75%, failure cases 50%, tool use 25%.

## Things to try

| Try | Question |
|---|---|
| `--dry-run` then read the prompt | What exactly does the model know about fiscal years? |
| `--gold nvda-revenue-2024-shifted` | Does it mention FY2025, or only FY2024? |
| `"What will Apple's revenue be in 2027?"` | Does it decline, with a scope notice? |
| `--gold aapl-net-margin-fy2025` | It computes the margin in its head: right number, scored 0. Why? ([I-008](DECISIONS.md#i-008)) |
| `--k 2` on a three-year question | What does it say about the missing years? |

## Gotchas

- **Ask mode has no calculator.** Percentages and growth rates belong in Research mode (stage 4); Ask mode's mental arithmetic breaks the "all arithmetic via the calculator" rule.
- **Open hybrid-reasoning models are slow with thinking on.** Set `TENK_OPENAI_THINKING=0` for Ask mode's < 5 s target.
- Claims the model forgets to list aren't verified: a number only in the answer text has no status.
