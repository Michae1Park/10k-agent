# V2 · Generation

**Goal:** given the chunks V1 retrieves, how often one model call turns them into a correct, cited answer, and declines when the filings can't answer.

**Result (number questions and declines):** with V1's retrieval, Qwen3.6-27B gets 21 of 30 number questions right; with every gold source in its context, 25 of 30. It declines all 8 questions it should and wrongly declines only 1 of 46 answerable. With no excerpts it declines everything, so it isn't answering from memory.

| Context | Number questions correct | Declined when it should | Declined when answerable | Numbers in claims | Numeric claims grounded |
|---|---|---|---|---|---|
| **Retrieved** (V1's top 8) | **21 / 30** | 8 / 8 | 1 / 46 | 48.5% | 76.0% |
| Oracle (gold swapped in) | 25 / 30 | 8 / 8 | 0 / 46 | 63.2% | 80.4% |
| None (no excerpts) | 0 / 30 | 8 / 8 | 46 / 46 | – | – |

**Text questions are not scored yet.** Their required points are V1's verbatim evidence quotes, and the judge marks right answers wrong when they leave out details the question never asked for (§5).

**Contents:** [1 Test data](#1-test-data) · [2 Contexts](#2-contexts) · [3 Generation](#3-generation) · [4 Metrics](#4-metrics) · [5 Results](#5-results)

---

## 1. Test data

**File:** [generation-gold-fy2025.jsonl](v2/v2_testdata/generation-gold-fy2025.jsonl). To read it: [PDF](v2/v2_testdata/generation-gold-fy2025.pdf) (one card per question, evidence highlighted) and [summary](v2/v2_testdata/generation-gold-fy2025-summary.pdf).

54 questions, built up from V1's set:

| Part | Questions | What | Reviewed |
|---|---|---|---|
| From V1, unchanged | 46 | [V1's questions](V1_RETRIEVAL.md#1-test-data): 30 with expected numbers, 16 with required points | Yes |
| Added in V2 | 8 | Should be declined: Oracle and Tesla (not in the corpus), FY2026 and FY2027, a stock price, CEO pay (proxy statement), Azure's dollar revenue, investment advice | Not yet |

**Earlier years come from the FY2025 filings.** Each FY2025 10-K shows the prior years beside the current one (three years in the income and cash-flow statements, two in the balance sheet), so "from 2023 to 2025" is answerable from one filing: `Net income | 30,425 | 59,248 | 77,670` (Amazon).

All on the FY2025 10-Ks of AAPL, AMZN, MSFT and NVDA, in V1's parent-child corpus (`data/v0-chunking/parent_child.db`).

---

## 2. Contexts

Each question is answered three times. Only the chunks shown to the model change, so retrieval failures and generation failures can be told apart.

| Context | Chunks shown | Measures |
|---|---|---|
| **Retrieved** | V1's top 8, unchanged ([V1 config](v1/v1_src/config.yaml)) | The real pipeline |
| **Oracle** | V1's top 8, with each gold source V1 missed swapped in for the lowest-ranked chunk that is no source's gold | Generation alone, when the evidence is there |
| **None** | No excerpts (`Excerpts: (none)`), same prompt | Whether the model answers from memory |

Oracle equals Retrieved on the 39 questions where V1 finds every source; it adds 15 chunks over the other 7 (both capex questions, employees, highest revenue, three risk questions).

---

## 3. Generation

```
excerpts + question ─► one model call ─► JSON (one "only the JSON" retry) ─► claims checked against cited chunks
```

| Setting | Value |
|---|---|
| Model | `Qwen/Qwen3.6-27B-FP8` on vLLM, thinking off, temperature 0 |
| Prompt | [system.md](v2/v2_prompt/system.md): rules, the corpus table (company, fiscal year, period end), the answer format |
| User message | Each chunk as `<chunk id="…">` with company, fiscal year, period end, Item, kind and units; then the question |
| Answer format | `answer` with `[n]` markers; `claims[]`, one per number (`value`, `unit`, `company`, `fiscal_year`, `chunk_ids`, `calculation`); `table`; `scope_notice`; `abstained` |

Code: [generate.py](v2/v2_src/generate.py). Settings: [config.yaml](v2/v2_src/config.yaml).

---

## 4. Metrics

Code: [score.py](v2/v2_src/score.py); judge: [judge.py](v2/v2_src/judge.py).

| Metric | How |
|---|---|
| **Correct** | Decline questions: declined. Number questions: not declined, and every expected value is stated in the answer text or table (±0.5%). Text questions: not declined, and the LLM judge (`gpt-oss-20b`) finds every required point covered |
| Numbers in claims | Expected values listed as a claim for the right company and fiscal year. Only claims can be verified (V4), so this is stricter than Correct |
| Numeric claims grounded | Numeric claims whose value is in a chunk they cite (or whose calculation reruns from them) |
| Gold sources cited | Gold sources with a chunk cited by some claim |
| Declined when it should / when answerable | `abstained` on decline questions / on answerable ones |

---

## 5. Results

Output: `output/generation/qwen3.6-27b-fp8/` (results.json, scores.json).

### By kind

Correct, Retrieved → Oracle

| Kind | Questions | Retrieved | Oracle |
|---|---|---|---|
| figure | 11 | **100%** | **100%** |
| compare_year | 9 | 56% | 56% |
| compare_company | 10 (7 numeric) | 50% | 60% |
| open | 8 (3 numeric) | 0% | 38% |
| decline | 8 | **100%** | **100%** |
| risk, drivers | 8 | text: not scored yet | |

### Where generation fails

| Failure | Questions | Example |
|---|---|---|
| **Answers with a company missing from the context** | 2, Retrieved only | "Which company reported the highest revenue?": Amazon's line wasn't retrieved, and the model answers **Apple**; "most employees" answers Microsoft. Neither says Amazon is missing (in `cmp-employees-all4` it does say Apple's count isn't in the excerpts) |
| **Picks the wrong line** | 2 | Capex (`cmp-capex-all4`, Retrieved): gives Apple's R&D ($34,550M) as its capex. `open-largest-capex`: gross property and equipment instead of purchases |
| **Skips the middle year** | 3 | "How did Amazon's net income change from 2023 to 2025?": gives 2023 and 2025, not 2024, though the context holds the row with all three (`Net income \| 30,425 \| 59,248 \| 77,670`) |
| **Gives the change, not the values** | 1 | "NVIDIA's revenue growth, FY2024 to FY2025": "$69,575 million, 114%" without either year's revenue |
| **Several numbers in one claim** | most `compare_year` | `"text": "…$383,285 million in fiscal 2023 and $416,161 million in fiscal 2025", "value": null`. The answer is right but the claim can't be verified; hence Numbers in claims 63% against 90% stated (Oracle) |

Amazon's capex as "$128.3 billion" (its MD&A's cash capex, net of incentives) is scored wrong against the gold `Purchases of property and equipment` (131,819). The gold may need it as an acceptable answer.

### Text questions: the required points don't fit generation

V1's required points are the evidence quotes, kept whole. The judge (correctly) marks them uncovered when the answer gives only what the question asks:

| Question | Answer | Required point | Judged |
|---|---|---|---|
| Whose cloud services revenue grew 34% in fiscal 2025? | Microsoft's Azure and other cloud services revenue grew 34% | Azure and other cloud services revenue grew 34% **driven by demand for our portfolio of services** | not covered |
| Which company repurchased 310 million shares? | NVIDIA Corporation | In fiscal year 2025, we repurchased 310 million shares **for $34.0 billion** | not covered |

As judged now, text questions are 4 of 16 right in both contexts; 7 of the 12 misses are right answers like these. Fixing it means rewriting the points as the facts an answer must state ("Microsoft", "Azure and other cloud services", "34%"), reviewed like the rest of the gold set.

### Takeaways

- **Grounding holds.** No answer from memory with no excerpts, every decline declined, no chunk ID cited that wasn't shown.
- **Single figures are solved** (11 / 11). The failures are in multi-value answers: missing years, and companies missing from the context.
- **When retrieval drops a company, the model guesses** instead of saying which company it has no figure for. A rule for that in the prompt is the cheapest fix to try.
- **Claims need one number each.** Half the verifiable numbers are lost to bundled claims.
