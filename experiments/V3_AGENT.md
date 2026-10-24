# V3 · Agent

**Goal:** whether letting the model drive the work with tools (search again, read a section, calculate, look up SEC data) fixes V2's failures, and how much of any gain comes from the SEC data rather than from the tool use itself.

**Result:** the agent gets **91.7%** of number and decline questions right with 10-K tools only, against V2's **70.5%** on the same 78 questions, model and server. Adding XBRL and EDGAR tools gives **91.5%**: the same score, reached differently. On the held-out test split: V2 9 of 17, agent 13 (text tools) and 12 (with SEC data). With the text questions judged, overall: V2 67.9%, agent 88.5% / 88.5%.

| Variant | Correct (all 78) | Number and decline (59) | Text, judged (19) | Test split (24) | Declined right | Wrong declines | Numeric claims grounded | Numbers in claims | Seconds |
|---|---|---|---|---|---|---|---|---|---|
| **v2**: V1's top 8, one call | 67.9% | 42 (71.2%) | 11 | 15 | 13 / 13 | 3 / 65 | 100% | 35.3% | 35 |
| **text**: agent, 10-K tools | **88.5%** | **55** (93.2%) | 14 | **18** | 12 / 13 | 0 / 65 | 100% | 63.2% | 83 |
| **full**: agent, + XBRL, EDGAR | **88.5%** | 54 (91.5%) | **15** | 17 | 8 / 8 | 0 / 70 | 100% | **72.3%** | 88 |

Same model (`Qwen3.6-27B-FP8`, thinking off, temperature 0) and claim check in all three; only what the model can do changes. Each variant has its own decline set (§1): 5 questions need SEC data, so they are declines for `v2` and `text` only, and one V2 decline is answerable with SEC data, so `full` has 8 declines and 70 answerable questions.

**Takeaways:**
- **The agent's gain is the tool use, not the SEC data.** `v2` → `text` is +22 points on number and decline questions; `text` → `full` adds nothing overall. XBRL changes *how* questions are answered (fewer searches, more claims checkable), not how many are right.
- **V2's multi-value failures are gone.** Year ranges 5 → 9 of 9, multi-step 0 → 4 of 5, "which company…" (`open`) 0 → 6 of 8. The agent searches again for a missing company instead of ranking without it.
- **SEC data rescues retrieval misses.** With a gold source missing from what the agent saw, `full` is right 82.5% of the time, `text` 33.3%: XBRL gives the figure the search didn't find.
- **SEC data doesn't cost verifiability.** Every numeric claim of every variant is grounded. The first count gave `full` 93.1%, but its 11 misses were gaps in the claim check, not in the citations: a comparison's inputs cited as another company's XBRL facts, per-share units, a unit conversion done with the calculator, and filing dates citing an accession number. All are now checked (2026-10-25).

**Contents:** [1 Test data](#1-test-data) · [2 Variants](#2-variants) · [3 Agent](#3-agent) · [4 Metrics](#4-metrics) · [5 Results](#5-results) · [6 Iterations](#6-iterations) · [7 Open issues](#7-open-issues)

---

## 1. Test data

One self-contained set, [agent-gold-fy2025.jsonl](../evaluation/gold/agent-gold-fy2025.jsonl): 78 questions, all reviewed (MP). Each question's `gold_set` names where it comes from:

| `gold_set` | Questions | Dev / test | What it tests |
|---|---|---|---|
| `generation-gold-fy2025` | 54 | 38 / 16 | V2's set, copied in (one question gained a `with_sec_data` override, below). V2's own file stays as V2's gold |
| `agent-gold-fy2025` (new) | 24 | 16 / 8 | What V2 couldn't do: calculation, several steps, SEC data, knowing when not to use a tool |

The 24 new questions (`tenk gold --gold evaluation/gold/agent-gold-fy2025.jsonl render` for the review PDF of all 78):

| Kind | Questions | Example | Scored on |
|---|---|---|---|
| `calculate` | 12 | "What was Microsoft's operating margin in fiscal 2025?" | The derived number **and** its inputs, ±0.5% |
| `multi_step` | 5 | "Which of the four had the highest operating margin, and what was it?" (8 inputs, 4 ratios) | The winner's number and every input |
| `sec_data` | 5 | Tesla's 2024 revenue (XBRL only); Netflix's 2024 growth; Microsoft's FY2026 10-K filing date (EDGAR) | Numbers, or required points |
| `lookup` | 1 | NVIDIA's two segments: text, no calculator, no XBRL | Required points |
| `decline` | 1 | "If Amazon's sales keep growing at their 2025 rate, what will they be in 2026?": the calculator could, the agent must not | Declined |

Plus one false premise ("Apple's net income fell in fiscal 2025…": it rose 19.5%). Every expected number was computed with the calculator from an evidence row in the 10-K, which also matches the XBRL fact.

**Questions that depend on the tool set.** Five of the new questions (`text_tools: abstain`) need SEC data: for `v2` and `text` the right answer is to decline, so they score as declines there. One V2 decline question has the opposite override (`with_sec_data`): "Microsoft's net income in fiscal 2026" can't be answered from the corpus, but Microsoft filed that 10-K in July 2026 and its XBRL facts are in the cache, so under `full` the right answer is $133,749M.

---

## 2. Variants

| Variant | Tools | Context | Measures |
|---|---|---|---|
| **v2** | none | V1's top 8, then one call ([V2](V2_GENERATION.md)) | The baseline, rerun on the same server and scoring |
| **text** | `search_filings`, `get_section`, `calculator` | What the agent fetches | What tool use adds |
| **full** | + `xbrl_facts`, `edgar_filings` | Also XBRL facts and filing metadata | What the SEC data adds on top |

SEC data comes from the cache (`data/raw/edgar/`, fetched 2026-09-30) with the client off: no request leaves the machine during a run ([D-036](../docs/DECISIONS.md#d-036)).

---

## 3. Agent

Spec: [docs/stages/V3_AGENT.md](../docs/stages/V3_AGENT.md). Code: [agent/](../src/tenk_agent/agent/). Settings: [config.yaml](v3/config.yaml).

```
question ─► model ──tool calls──► tools ─► model ─► … ─► JSON answer (V2's format) ─► claim check
```

| Setting | Value |
|---|---|
| Loop | LangGraph `model ⇄ tools`, over the project's own `Model` interface (D-013) |
| Prompt | [agent.md](../src/tenk_agent/prompts/agent.md): how to work (plan, cover every company and year, calculator for all arithmetic), V2's rules, the corpus table, V2's answer format |
| First turn | Must be a tool call (`tool_choice: required`) |
| Budget | 20 tool calls; a repeated call returns an error; then one answer turn with tools off. The same once the conversation reaches 20k input tokens (the server holds 32k) |
| Answer | A message (not a tool call) holding a JSON object with `answer`; anything else gets one "only the JSON" retry |
| Claim check | V2's, plus `xbrl:` citations verified against the cached fact, and a percent claim may come from a ratio (8.3 percent from 0.083) |

Every route the model takes is its own: nothing in the loop picks a tool or a query.

---

## 4. Metrics

V2's metrics ([§4](V2_GENERATION.md#4-metrics)), plus tool use from each agent run's steps (`tools.json`):

| Metric | Counts |
|---|---|
| Correct, numbers stated, numbers in claims, grounded, declines | As in V2. A number with no unit that equals the gold value in dollars now matches ("$97,690,000,000" for 97,690 USD millions); V2 is rescored with the same rule |
| Gold sources cited | A cited chunk holds a gold source's evidence; for an XBRL source, the fact id is cited |
| Calls per question, errors, repeats, budget stops | From the steps |
| Expected-tools recall / precision | Called tools vs the question's `expected_tools` (only tools the variant has) |

Text answers were judged by `gpt-oss-20b` (the fixed judge, D-029) with [v2/judge.py](v2/judge.py) `--config experiments/v3/config.yaml`.

---

## 5. Results

Output: `output/generation/v3-qwen3.6-27b-fp8/` (results.json with every step, scores.json, tools.json). Run: `taskset -c 16-31 python experiments/v3/run.py`, then the judge.

### By kind

Correct, all 78 questions, text questions judged

| Kind | v2 | text | full |
|---|---|---|---|
| figure (11) | **11** | **11** | **11** |
| compare_year (9) | 5 | **9** | **9** |
| compare_company (10) | 5 | 6 | 6 |
| open (8) | 0 | **6** | 4 |
| risk (4) | 1 | **3** | 2 |
| drivers (4) | 3 | **4** | **4** |
| decline (9) | **9** | **9** | **9** |
| calculate (12) | 9 | **11** | 10 |
| multi_step (5) | 0 | **4** | **4** |
| sec_data (5) | 4 | 3 | **5** |
| lookup (1) | 1 | 1 | 1 |

`v2`'s and `text`'s `sec_data` are mostly declines (right when declined); only `full` can answer them.

### By split

| Split | v2 | text | full |
|---|---|---|---|
| V2's set, dev (38) | 26 | **36** | 34 |
| V2's set, test (16) | 7 | **11** | 10 |
| Agent set, dev (16) | 10 | 14 | **16** |
| Agent set, test (8) | 5 | **6** | 5 |

The prompt was changed on dev questions only (§6); the test split was run but not looked at until the end. Test is where the agent's lead over V2 is measured: 17 and 15 of 24 against 12.

### Tool use

| | text | full |
|---|---|---|
| Calls per question (max) | 3.9 (17) | 4.3 (20) |
| `search_filings` | 135 | 77 |
| `get_section` | 32 | 16 |
| `calculator` | 137 | 94 |
| `xbrl_facts` | – | 137 |
| `edgar_filings` | – | 8 |
| Calls that returned an error | 60 | 65 |
| Repeated calls | 8 | 7 |
| Budget stops | 4 | 3 |
| Input tokens per question | 30,200 | 22,400 |

- **Given XBRL, the agent prefers it:** `full` searches 43% less and reads 50% fewer sections, with 26% fewer input tokens. This is also why `full`'s "gold sources cited" (47.5%) and expected-tools recall (68%) are low: the gold sets name 10-K chunks and `search_filings`, and it cited the XBRL fact instead.
- **Errors are mostly recovered from.** The common ones: the calculator's input names (`a`, `b` for `pct_change`'s `old`, `new`: 18 in `text`), XBRL concept guesses (28 in `full`, each answered with the closest concept names), and companies outside the corpus. The model reads the error and calls again.
- **2 calls in `text` were to an `answer` tool** that doesn't exist (the model "calling" its answer); each got an unknown-tool error and the model then answered as text.

### Where the agent still fails

| Failure | Variant | Example |
|---|---|---|
| **Derived number without its inputs** | both | `nvda-net-margin-fy2025`: "NVIDIA kept 55.8% of its revenue as net income", right, but without the revenue and net income the gold set also expects |
| **Answers the "decline" with partial context** | text | `decline-msft-azure-dollars-q4`: gives Azure's growth rate and Intelligent Cloud's revenue, without declining the dollar figure the 10-K doesn't report |
| **Amazon's capex as $128.3 billion** | full | V2's open issue: the MD&A's cash capex, net of incentives, against the gold `Purchases of property and equipment` ($131,819M) |
| **Calculated claims without every input's source** | full | The claim cites the chunk of one input or none; the number is right but `unverified` |
| **Text questions' required points** | all | Rewritten as short facts on 2026-10-25 and re-judged; the counts above use them (with the whole-quote points: v2 6, text 12, full 11 of 19) |

---

## 6. Iterations

The prompt and loop were fixed on dev questions, each fix for a failure seen in a run; then the agent variants were rerun in full.

| Round | Change | Why |
|---|---|---|
| Smoke runs | First turn must call a tool | The agent answered Tesla's revenue from memory, citing an invented `xbrl:` id |
| | Rule: every year of a range; never project or forecast | V2's "skips the middle year"; the calculator made extrapolating easy |
| | A repeated call returns an error; `op: "expression"` accepted | 9 identical calls in one run |
| Round 1 → 2 | Rule: cite every calculation input, in its unit; advice and forecasts set `abstained` | `full` 84.1% grounded; advice answered with a summary |
| | Stop at 20k input tokens | One run exceeded the server's 32k context |
| Round 2 rerun | Only a JSON object with `answer` is an answer (20 affected runs rerun) | On the answer turn the model sometimes wrote a tool call as text, whose JSON arguments were taken for the answer: 9 empty answers |

Round 1 (before the grounding fix): `text` 85.2%, `full` 91.5% of number and decline questions, `full` 84.1% grounded; kept in `output/generation/v3-qwen3.6-27b-fp8-round1/`. Between rounds the two agent variants move by up to 7 points, so a difference of a few points between `text` and `full` is within run-to-run variation; V2's gap is not.

---

## 7. Open issues

| Issue | Fix |
|---|---|
| ~~Text questions' required points are whole evidence quotes~~ | Done 2026-10-25 ([V2 §5](V2_GENERATION.md#text-questions-the-required-points-dont-fit-generation)); review pending |
| ~~`full` grounds 93% of numeric claims~~ | Done 2026-10-25: claim-check gaps fixed, 100% |
| Variance between runs (up to 7 points) | Repeat each variant 3× and report the spread |
| Amazon's $128.3B capex scored wrong | Add it as an acceptable answer (still open from V2) |
| A derived number's inputs left out of the answer | Prompt: state the inputs of every calculated number |
| The SEC cache is a snapshot (2026-09-30) | `full`'s answers about recent filings (FY2026) depend on it; refresh with `tenk fetch --refresh` |
