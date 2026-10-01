# Stage 4 — Agent (Research mode): plan, call tools, calculate, report

## Commands

Workstation terminal, repo root. The tools need no model; the agent needs Claude or an open model on vLLM ([SERVING.md](SERVING.md)).

```bash
source .venv/bin/activate

# Part 1: the tools by hand, exactly what the agent sees
python playground/tools.py --list                                                  # the schemas the model gets
python playground/tools.py search_filings query="R&D expense" companies=Apple fiscal_years=2024
python playground/tools.py get_filing_section company=Google fiscal_year=2025 item=7
python playground/tools.py calculator op=pct_change old=29915 new=31370
python playground/tools.py verify_citation chunk_id=AAPL-FY2024-8-002 value=31.37 unit="USD billions"
python playground/tools.py search_filings query=revenue companies=Oracle            # the error the agent gets

# Part 2: the agent
python playground/agent.py "Compare Apple's and Microsoft's R&D spending from 2023-2025."
python playground/agent.py --gold aapl-rd-yoy-fy2023-2025                          # gold question, scored
python playground/agent.py "..." --max-calls 5 --tag tight                         # small budget
python playground/agent.py "..." --no-repair                                       # V3 (no repair round)

tenk research "Investigate how Apple's revenue mix changed over the last three years."
tenk eval run research --split dev --include-unverified --no-judge                 # V4 over the gold set
```
`tools.py` prints the call, the step label, the summary and the JSON result (`output/tools/<tag>.md`). `agent.py` streams each step, then prints the answer, its claims and every model turn and tool call with tokens and latency (`output/agent/<tag>.md`).

## Data

| Data | Where |
|---|---|
| Chunks, sections, filings | `data/tenk.db` (stages 1–2) |
| System prompt: how to work, rules, corpus table, answer format | `src/tenk_agent/agent.py` (`SYSTEM`), `answers.py` |
| Tool schemas (defined once, translated per provider) | `src/tenk_agent/tools.py` |
| **Trace** | `traces` table; the UI's trace view |

## Knobs

Defaults: `config.yaml` → `agent:`, `verify:`, `model:`. Flags override per run.

| Knob | Flag | What it does | Try |
|---|---|---|---|
| `agent.max_tool_calls` | `--max-calls` | Budget; calls beyond it get an error telling the model to answer | 5 vs 15 vs 30 |
| `agent.section_chars` | (config) | One page of `get_filing_section` | 12000: more paging |
| `agent.structured_data` | `--structured-data` | Adds the V5 XBRL tool ([D-021](DECISIONS.md#d-021)) | compare citations |
| `verify.repair` | `--no-repair` | Send failed claims back once (stage 5) | V3 vs V4 |
| `verify.repair_calls` | (config) | Extra tool calls in the repair round | 0 |
| `model.name` | `--model` | Which model runs the loop | 9B vs 27B |

## Outputs

| Output | Where |
|---|---|
| Each step as it happens: ✓/✗, label ("Searching Apple FY2024 for …"), summary | terminal |
| Answer, claims table, usage (model calls, tool calls, seconds, tokens, cost) | terminal, report |
| Tool selection, duplicate and over-budget calls, numbers matched (`--gold`) | terminal, report |
| Every span: model turn or tool call, input, tokens, seconds | `output/agent/<tag>.md` |

## How it works

**The tools** (`tenk_agent.tools.Toolbox`):

| Tool | Arguments | Returns |
|---|---|---|
| `search_filings` | query; companies, fiscal_years, items, k | Ranked chunks with IDs, company, fiscal year, period end, Item, units, text. Explicit filters = one search (no slots) |
| `get_filing_section` | company, fiscal_year, item; start | The section in order, 24k chars per page, `next_start` to continue |
| `calculator` | op (`pct_change`, `cagr`, `ratio`, `percent_of`, `difference`, `sum`) or expression; named inputs | Result + formula. Safe AST evaluation, no `eval` |
| `verify_citation` | chunk_id; value + unit, or quote | Found / not found + matched span (stage 5's check) |

**The loop** (`tenk_agent.agent.research()`, about 80 lines in `_Run`):

```
question ─► model ─► tool calls? ─yes─► run tools ─► results back ─► model ─► …
                          └─no──► final JSON ─► verify ─► failed claims? ─yes─► one repair round ─► verify
```

| Step | What happens |
|---|---|
| 1. Turn | `complete(messages, tools)`. Text before tool calls streams as a "thought" |
| 2. Tools | Each call runs; errors (unknown company, FY2022, bad arguments) return as results the model can fix. Calls past the budget get "budget exhausted, answer now" |
| 3. Stop | A turn without tool calls is the final answer (JSON, same format as stage 3) |
| 4. Verify + repair | Stage 5; then usage and the trace are saved |

**The flagship task** ("how Apple's revenue mix changed over the last three years") has a 10-step reference trajectory: find the net-sales-by-category table, find MD&A explanations, compute shares and changes, verify two figures (`eval/gold/reference_trajectories.json`). Calls beyond it count as unnecessary.

**Results** (`test`, 31 questions, draft gold; correctness on 21):

| | V3 (no repair) | V4 | V4, 27B model |
|---|---|---|---|
| Correctness | 90.5% | 95.2% | 100% |
| Citation accuracy | 95.2% | 95.9% | 97.6% |
| Tool selection · valid calls | 90.3% · 97.7% | 90.3% · 97.5% | 93.5% · 95.5% |
| Questions over budget | 19% | 19% | 26% |
| Mean latency | 84 s | 120 s | 178 s |

Ask (V2) was 71.4%: the agent wins on calculations (tool use 25% → 100%) and multi-filing questions (75% → 100%).

## Things to try

| Try | Question |
|---|---|
| `tools.py --list` | What does the model actually know about each tool? |
| `tools.py search_filings query=revenue fiscal_years=2022` | What does the agent see for a year outside the corpus? |
| `agent.py --gold aapl-rd-yoy-fy2023-2025` | Does it compute year-over-year for *each* year, or one two-year change? |
| `--max-calls 5` vs 15 | What does it drop when the budget is tight? |
| `--no-repair` vs default on the same question | Which claims did the repair round fix? |
| `--model openai:Qwen/Qwen3.6-27B-FP8` (27B served) | Fewer wasted calls? |

## Gotchas

- **The budget gets spent on checking.** Open models often fire many `verify_citation` calls on quotes ([I-007](DECISIONS.md#i-007)); the system prompt asks to verify key numbers only.
- **Parallel tool calls count individually** toward the budget.
- Answers depend on the model; with the same model and `temperature 0` (open models) runs are close but not identical across vLLM batches.
