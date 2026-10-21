# V2 · Generation

**Goal:** how often one model call turns the chunks V1 retrieves into a correct, cited answer, and declines when the filings can't answer.

**Result:** Qwen3.6-27B gets 21 of 30 number questions right with V1's retrieval, and 25 of 30 when every gold source is in its context. It declines all 8 questions it should and 1 of 46 it shouldn't. With no excerpts it declines everything, so it isn't answering from memory.

| Context | Number answers right (of 30) | Declines right (of 8) | Wrong declines (of 46 answerable) | Numbers in claims | Numeric claims grounded |
|---|---|---|---|---|---|
| **V1 retrieval** (the 8 chunks V1 finds) | **21** | 8 | 1 | 48.5% | 100% |
| Perfect retrieval (missed answer chunks added) | 25 | 8 | 0 | 63.2% | 100% |

With no chunks it declines all 54 questions, so it doesn't answer from memory.

The last two columns are checked by code, not by the LLM judge (the judge only scores text questions):

- **Claim:** the model returns JSON with two parts: the written answer, and a list of `claims`, one per number it uses. Each claim gives the value, the company, the fiscal year and the IDs of the chunks (10-K excerpts) it came from, e.g. `{"value": 112010, "company": "AAPL", "fiscal_year": 2025, "chunk_ids": ["AAPL-FY2025-8-002"]}`.
- **Why claims:** code can't reliably tell which company and year a number in free text refers to, but a claim states them. So code can look the number up in the cited chunk; a number that is only in the written answer can't be checked that way.
- **Numbers in claims:** of the 68 numbers the 30 number questions expect, the share the model lists as a claim with the right company and year. A right answer can still miss here, e.g. two years' revenue in one claim with `"value": null`.
  - Question it answers: did the model list the expected numbers as checkable claims?
- **Numeric claims grounded:** of all the claims with a number, the share whose value really is in a chunk it cites (or whose calculation reruns from those chunks). This tests that the model didn't invent or misattribute a number.
  - Question it answers: are the numbers the model claims actually in the chunks it cites?

Text questions: 4 of 16 as judged now, but the scoring doesn't fit them yet ([§5, Text questions](#text-questions-the-required-points-dont-fit-generation)).

**Contents:** [1 Test data](#1-test-data) · [2 Contexts](#2-contexts) · [3 Generation](#3-generation) · [4 Metrics](#4-metrics) · [5 Results](#5-results) · [6 Open issues](#6-open-issues)

---

## 1. Test data

**File:** [generation-gold-fy2025.jsonl](v2/v2_testdata/generation-gold-fy2025.jsonl). To read it: [PDF](v2/v2_testdata/generation-gold-fy2025.pdf) (one card per question, evidence highlighted) and [summary](v2/v2_testdata/generation-gold-fy2025-summary.pdf).

54 questions on the FY2025 10-Ks of AAPL, AMZN, MSFT and NVDA, in V1's parent-child corpus (`data/v0-chunking/parent_child.db`): [V1's 46](V1_RETRIEVAL.md#1-test-data), unchanged, plus 8 to decline.

| Kind | Questions | Scored on | Reviewed |
|---|---|---|---|
| `figure` | 11 | Expected numbers | Yes |
| `compare_year` | 9 | Expected numbers | Yes |
| `compare_company` | 10 | 7 expected numbers, 3 required points | Yes |
| `open` | 8 | 3 expected numbers, 5 required points | Yes |
| `risk`, `drivers` | 8 | Required points | Yes |
| `decline` (new) | 8 | Declined or not | Not yet |

That makes 30 number questions, 16 text questions and 8 declines.

**Decline questions:** Oracle Corporation and Tesla (not in the corpus), FY2026 and FY2027, a stock price, CEO pay (in the proxy statement, not the 10-K), Azure's dollar revenue, investment advice.

**Earlier years come from the FY2025 filings.** Each 10-K shows prior years beside the current one (three years in the income and cash-flow statements, two in the balance sheet), so "from 2023 to 2025" is answerable from one filing: `Net income | 30,425 | 59,248 | 77,670` (Amazon).

---

## 2. Contexts

Each question is answered three times, all in V2 with the same model. Only the chunks shown to the model change, so retrieval failures can be told apart from generation failures.

| Context | Chunks shown | Measures | In the output files |
|---|---|---|---|
| **V1 retrieval** | The 8 chunks V1's pipeline returns, unchanged ([V1 config](v1/v1_src/config.yaml)) | The real system: retrieval and generation together | `retrieved` |
| **Perfect retrieval** | The same 8, with each gold source V1 missed added in place of the lowest-ranked chunk that isn't gold for any source | Generation alone: with the evidence there, every error is the model's | `oracle` |
| **No chunks** | None (`Excerpts: (none)`), same prompt | Whether the model answers from memory | `none` |

Perfect retrieval is the same as V1 retrieval on the 39 questions where V1 finds every source. On the other 7 it adds 15 chunks: both capex questions, employees, highest revenue and three risk questions.

---

## 3. Generation

```
V1 retrieval ─► 8 chunks + question ─► prompt ─► one model call ─► JSON reply (one "only the JSON" retry)
                                                                   ├─ answer  (the written text)
                                                                   └─ claims  (one per number used)
                                                                          │
                                         code checks each claim against the chunks it cites,
                                         and reruns its calculation with the calculator
```

| Setting | Value |
|---|---|
| Model | `Qwen/Qwen3.6-27B-FP8` on vLLM, thinking off, temperature 0 |
| Prompt | [system.md](v2/v2_prompt/system.md): rules, the corpus table (company, fiscal year, period end), the answer format |
| User message | Each chunk as `<chunk id="…">` with company, fiscal year, period end, Item, kind and units; then the question |
| Answer format | `answer` with `[n]` markers; `claims[]`, one per number (`value`, `unit`, `company`, `fiscal_year`, `chunk_ids`, `calculation`); `table`; `scope_notice`; `abstained` |

The claim check is deterministic and runs after the answer is written. It labels each claim (`verified`, `calculated`, `cited`, `unverified`) but doesn't change the answer.

There are no tools: the model does any arithmetic itself and writes the result into the claim's `calculation`. The check reruns it with the agent's calculator ([calculator.py](../src/tenk_agent/calculator.py)) and marks a wrong result `unverified`, so math errors are caught but not corrected.

Code: [generate.py](v2/v2_src/generate.py). Settings: [config.yaml](v2/v2_src/config.yaml).

---

## 4. Metrics

Code: [score.py](v2/v2_src/score.py); judge: [judge.py](v2/v2_src/judge.py).

| Metric | Counts as a hit |
|---|---|
| **Correct** | Number question: not declined, and every expected value appears in the answer text or table (±0.5%) |
| | Text question: not declined, and the LLM judge (`gpt-oss-20b`) finds every required point covered |
| | Decline question: declined |
| Numbers in claims | Expected value listed as a claim for the right company and fiscal year. Stricter than Correct, since only claims can be verified (V4) |
| Numeric claims grounded | Numeric claim whose value is in a chunk it cites, or whose calculation reruns from them |
| Gold sources cited | Gold source with a chunk cited by some claim |
| Declined when it should / when answerable | `abstained` on a decline question / on an answerable one |

---

## 5. Results

Output: `output/generation/qwen3.6-27b-fp8/` (results.json, scores.json).

### By kind

Correct, V1 retrieval / Perfect retrieval

| Kind | V1 retrieval | Perfect retrieval |
|---|---|---|
| figure (11) | **100%** | **100%** |
| compare_year (9) | 56% | 56% |
| compare_company (10)* | 50% | 60% |
| open (8)* | 0% | 38% |
| decline (8) | **100%** | **100%** |
| risk (4)* | 25% | 25% |
| drivers (4)* | 75% | 75% |

\* Includes the kind's text questions (3 in `compare_company`, 5 in `open`, all of `risk` and `drivers`), as the judge scores them now. See Text questions below.

### Where generation fails

| Failure | Questions | Example |
|---|---|---|
| **Picks a winner with a company missing from the context** | 2, V1 retrieval only | "Which company reported the highest revenue?": Amazon's line wasn't retrieved, and the model answers **Apple** while saying Amazon's revenue isn't in the excerpts. "Most employees" answers Microsoft the same way. Amazon is first in both; the answer should be that the question can't be settled without it |
| **Picks the wrong line** | 1, V1 retrieval only | `open-largest-capex`: gross property and equipment ($534,098M for Amazon) instead of purchases ($131,819M). Right with perfect retrieval |
| **Thinks aloud in the answer** | 1, V1 retrieval only | `cmp-capex-all4`: the answer and one claim hold its reasoning ("…but wait, MSFT capex is not explicitly stated… Let me re-read"), and it ends declined. This is the 1 wrong decline |
| **Skips the middle year** | 3 | "How did Amazon's net income change from 2023 to 2025?": gives 2023 and 2025 but not 2024, though the context holds the row with all three (`Net income \| 30,425 \| 59,248 \| 77,670`) |
| **Gives the change, not the values** | 1 | "NVIDIA's revenue growth, FY2024 to FY2025": "$69,575 million, 114%", without either year's revenue |
| **Several numbers in one claim** | most `compare_year` | `"text": "…$383,285 million in fiscal 2023 and $416,161 million in fiscal 2025", "value": null`. The answer is right, but the claim can't be verified. This is why Numbers in claims is 63% against 90% stated (Perfect retrieval) |

### Text questions: the required points don't fit generation

V1's required points are its evidence quotes, kept whole. The judge (correctly) marks them uncovered when an answer gives only what the question asks:

| Question | Answer | Required point | Judged |
|---|---|---|---|
| Whose cloud services revenue grew 34% in fiscal 2025? | Microsoft's Azure and other cloud services revenue grew 34% | Azure and other cloud services revenue grew 34% **driven by demand for our portfolio of services** | not covered |
| Which company repurchased 310 million shares? | NVIDIA Corporation | In fiscal year 2025, we repurchased 310 million shares **for $34.0 billion** | not covered |

As judged now, 4 of 16 text questions are right with both V1 and perfect retrieval, and 7 of the 12 misses are right answers like these.

### Takeaways

- **Grounding holds.** Every numeric claim is in a chunk it cites, nothing is answered from memory with no excerpts, every decline is declined, and no cited chunk ID is one the model wasn't shown.
- **Single figures are solved** (11 / 11). The failures are in multi-value answers: missing years, and companies missing from the context.
- **When retrieval drops a company, the model still names a winner.** It now says which company it has no figure for, but answers anyway. A prompt rule against ranking with a company missing is the cheapest fix to try.
- **Claims need one number each.** Half the verifiable numbers are lost to bundled claims.

---

## 6. Open issues

| Issue | Fix |
|---|---|
| Text questions' required points are whole evidence quotes (§5) | Rewrite them as the facts an answer must state ("Microsoft", "Azure and other cloud services", "34%"), reviewed like the rest of the gold set |
| Amazon's capex as "$128.3 billion" (MD&A cash capex, net of incentives) is scored wrong against the gold `Purchases of property and equipment` (131,819) | Consider adding it as an acceptable answer |
| The 8 decline questions haven't been reviewed | Review them |
