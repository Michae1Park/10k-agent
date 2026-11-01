# V4 build log

History of the V4 build: phase gates, the V3 migration, and what each phase found, dated as written. It is a record, not the current design: later entries can supersede earlier ones. Current design: [V4_TECHNICAL_DESIGN_DOC.md](V4_TECHNICAL_DESIGN_DOC.md) ("design §n" below). Decisions: [DECISIONS.md](DECISIONS.md). Results: [V4_RESULTS.md](V4_RESULTS.md).

Section references of the form "§n" without "§3.n" point to the design doc **as it was when the entry was written** (sections 1–17, before the 2026-11-01 restructure); see the old-to-new table at the end.

---

## 1. Build order and test gates

Each phase passes its gate before the next (spec §7). Pilot BKNG + UBER (UBER is the widest template), then all eight.

| Phase | Builds | Gate |
|---|---|---|
| **V4.0a Pre-flight** (spec §8) | `companies verify`; one company end to end on EDGAR (company facts, dates, rate limit); confirm the three §4.5 XBRL facts; release dates and numeric-guidance survey; dataset terms; D-001 successor entry | Findings written into this doc; `companies.yaml` filled |
| **V4.0b Data** | Migration M1–M6 (§14.1), then `facts`/`quarters`, prices, macro, as-of search | V3 tests and V3 agent gold green; unit tests incl. a leak test (as-of before a filing → zero rows from it); Q4 ties on the pilot |
| **V4.1 Extraction** | Pilot hand labels first; extractor, registry, guidance | Layer 1 on the pilot, then all eight |
| **V4.2 Ask** | Tool groups, `refs.py`, prompts, ask gold set | Ask gold bars |
| **V4.3 Model** | DSL, graph, templates (`segments`, `marketplace` first), checks, workbook Model tab + parity test | Layer 2 on the pilot |
| **V4.4 Forecast + backtest** | Forecast, proposer (defaults first, then LLM), S1–S5, backtest harness, baselines, leakage probe | Layer 3 on the pilot, then all eight |
| **V4.5 Valuation + Research** | S6–S8, model tools, research gold set | Layer 4; Research gold bars |
| **V4.6 Report** | S2 events, S9 debates, S10 note (sections 1–5, then 6–10), trace and lint, S11 export, review status | Layer 5 |
| **V4.7 Scale and write-up** | Cross-checks, Form 4, ablations, `--all` ratings, results table and limits (spec §6) | Results table complete |

---

## 2. Migration from V3

V3 stays runnable as the regression check for the shared code (retrieval, claim check, agent loop). Its eight megacaps and FY2023–2025 move to `companies-v3.yaml`; `tenk ... --companies companies-v3.yaml` rebuilds and reruns it. The V4 corpus lives in its own data directory, so neither build overwrites the other.

Today 15 modules assume 10-K only or `FISCAL_YEARS`: `cli.py`, `corpus.py`, `acquisition/` (`__init__`, `edgar`), `preprocess/` (`__init__`, `blocks`, `validation`), `retrieval/` (`index`, `decompose`), `agent/` (`__init__`, `loop`, `tools`, `sec`), `generation/generate.py`, `evaluation/gold.py`. The steps below change them in an order where every step leaves V3 green.

| Step | Change | Modules | Safety net |
|---|---|---|---|
| **M1** Config | Company list, fiscal years and data dir come from a companies file; `FISCAL_YEARS` becomes a per-corpus value; `companies-v3.yaml` reproduces today's tuple | `corpus.py`, `cli.py`, `acquisition/`, `decompose.py`, `gold.py` | All V3 tests pass unchanged |
| **M2** Forms | `preprocess/forms.py` with a 10-K `FormSpec` only; `blocks.py` and `validation.py` read it instead of hard-coding the 10-K; `index.py` labels by form | `preprocess/`, `retrieval/index.py` | V3 parse and chunk output byte-identical on the V3 corpus |
| **M3** Store + as-of | `asof.py`; new schema (§4.3) with `form`, `fiscal_period`, `filed`; dated reads require `asof`. V3 runs pass `AsOf(today)` | `asof.py`, `store.py`, callers | V3 store tests; new leak test |
| **M4** Refs | `research/refs.py` takes over `verify.py`'s three id cases; XBRL id part 6 becomes `<start>/<end>` for durations (§4.5), old ids still parse | `generation/verify.py`, `agent/sec.py` | V3 claim-check tests; V3 agent gold rerun within run-to-run spread of D-037 (~86–88%) |
| **M5** Tools | `Toolbox(asof, mode)` with tool groups; `mode="v3"` gives exactly V3's four tools and prompt | `agent/tools.py`, `agent/loop.py` | V3 agent tests; same V3 gold rerun |
| **M6** Forms, cont. | 10-Q and EX-99 `FormSpec`s, any-form fetch, 8-K exhibits, period chunk ids | `acquisition/edgar.py`, `preprocess/forms.py` | V4.0b gate |
| **M7+** | New V4 modules (`facts/`, `research/`, new evaluation) | new | V4 gates (§15) |

**Rules**
- M1–M5 change no V3 behavior; a V3 test edited to pass is a regression unless the change is a rename noted in the commit.
- `experiments/v4/v3_regress.py` checks byte identity offline after every step: blocks, documents, validator output, embedding texts, keyword search, store reads, and the claim check re-run over all 2,348 claims of the saved V3 runs. Where it is identical and the prompt and tool schemas are unchanged, the agent cannot behave differently, so the V3 agent gold rerun (vLLM, ~78 questions) runs once at the end of V4.0b and at the end of each later phase that touches shared code, not after each step.
- `xbrl_facts` stays in `mode="v3"`; V4 modes get `financials` (§7.1).
- When V4 no longer needs it, V3 retirement is its own decision entry, not a side effect.

---

## 3. Findings and results by phase

Checked against live SEC and data-source endpoints; raw responses cached under `data/v4/raw/`.

### 3.1 Companies (spec §8 items 1–2)

`tenk companies verify` filled `companies.yaml`. All eight are listed (UBER on NYSE, the rest on Nasdaq) with a December year-end. Filings since 2023-01-01:

| | BKNG | EXPE | ABNB | TRIP | UBER | LYFT | DASH | CART |
|---|---|---|---|---|---|---|---|---|
| CIK | 1075531 | 1324424 | 1559720 | 1526520 | 1543151 | 1759509 | 1792789 | 1579091 |
| 10-K | 4 | 4 | 4 | 4 | 4 | 4 | 4 | 3 |
| 10-Q | 11 | 11 | 11 | 11 | 11 | 11 | 11 | 9 |
| 8-K 2.02 | 15 | 15 | 15 | 15 | 15 | 15 | 15 | 12 |

The 10-K count includes the FY2022 10-K filed in early 2023. The 15 releases are Q4 2022 to Q2 2026, so all 15 periods in the window are there; CART starts at Q3 2023 (12 = Q3 2023 to Q2 2026), as expected. Its legal name is Maplebear Inc. ABNB's older filings sit on a second submissions page, so counting needs every page, which `verify` and `fetch_company` already read.

### 3.2 EDGAR end to end (UBER, BKNG)

- **Rate limit and User-Agent:** the existing client (one request per 0.12 s, User-Agent with an email) got no 429s. SEC's published limit is still 10 requests/s. No change.
- **Release timing:** `acceptanceDateTime` is UTC. Most companies release after the close (20:00–21:10 UTC). **UBER releases before the open** (~10:56 UTC), and TRIP (since 2024-05), CART and DASH sometimes do too. So `filing_date` = the release day, and with strictly-before an as-of of that day correctly excludes it; the backtest as-of = the day after `accepted_at` (§13.2) works for both cases.
- **One release can have two `EX-99.1` documents** (BKNG 2026-08-04). The fetcher takes every `EX-99.*` and labels them by order.

### 3.3 The three XBRL facts (§4.5): all confirmed, plus one more

1. **No dimensional facts in company facts:** fact fields are only `accn, end, filed, form, fp, frame, fy, start, val`. The inline XBRL has them: UBER's Q2 2026 10-Q has 664 of 1,274 facts with dimensions, including `us-gaap:Revenues` by `uber:MobilityMember`/`DeliveryMember`/`FreightMember`.
2. **10-Q cash flows are year-to-date only:** UBER and BKNG operating cash flow comes as 89–90, 180–181 and 272–273 day durations, with no three-month Q2 or Q3 fact. Income-statement items come as both three-month and year-to-date, sharing an end date (confirms the id change in §4.5).
3. **Operating KPIs are untagged:** no gross bookings, trips or room-nights concept in either filing's XBRL. "Gross Bookings" appears 48 times in UBER's 10-Q text only.
4. **New: company facts drops company-specific concepts.** It holds only `dei`, `us-gaap`, `srt`, `ecd`, `ffd`. Concepts such as `bkng:DeferredMerchantBookings` and `uber:PlatformParticipantDirectTransactionCosts` exist only in the inline XBRL. **Change:** `facts/xbrl.py` reads every company-specific fact, not just dimensional ones.

### 3.4 Numeric guidance (from the latest release; the full 14-quarter survey runs with the V4.1 extractor)

| Company | Latest guidance (Q2 2026 release) | Basis |
|---|---|---|
| BKNG | Q3 and full year 2026, as growth rates in a table | `growth_yoy` |
| EXPE | Q3 and FY gross bookings, revenue, adj. EBITDA in $ ranges, plus growth | `level` |
| ABNB | Raised FY revenue-growth and adj. EBITDA margin outlook; Q3 numbers in a table | `growth_yoy`, `margin` |
| TRIP | The release refers to adj. EBITDA (margin) guidance but has **no numeric outlook in the text** | n/a so far (§4.6) |
| UBER | Q3 gross bookings $58.25–60.25B, non-GAAP EPS, adj. EBITDA $2.86–2.96B | `level` |
| LYFT | Q3 gross bookings $5.50–5.67B, adj. EBITDA $183–203M | `level` |
| DASH | Q3 marketplace GOV $33.0–34.0B, adj. EBITDA $950–1,100M; FY SBC, D&A | `level` |
| CART | Q3 GTV $10.30–10.55B, adj. EBITDA | `level` |

### 3.5 Other data sources (spec §8 item 5)

| Source | Reachable by script | Terms | Status |
|---|---|---|---|
| FRED / ALFRED | ✓ key works; all five series found; vintages (`realtime_start`) returned | Free API; attribute FRED; UMCSENT is © University of Michigan, cite it | OK |
| Damodaran ERP | ✓ (HTTP 200) | Public, free | OK |
| NYC TLC | ✓ `fhvhv_tripdata_YYYY-MM.parquet`, ~540 MB per month | NYC Open Data, public | OK; aggregate at fetch (§4.2). License codes (HV0003 Uber, HV0005 Lyft) to confirm on the first file |
| BTS | ✓ (HTTP 200) | US government, public domain | OK |
| Stooq | ✗ behind a JavaScript proof-of-work bot check; the CSV endpoint returns the challenge page | Getting around the check is not acceptable | **Dropped; replaced by Tiingo** |
| Tiingo | ✓ free Starter: 1,000 requests/day, 500 symbols/month, 30+ years | "Internal Use Only… may not display or share the data with another person". Checked alternatives (Alpha Vantage free = last 100 days only; EODHD, Nasdaq.com, Yahoo: personal use only) | **Used, kept private** (§11 publication rule) |
| **TSA** | ✗ HTTP 403 to scripts | Public data, but the site blocks automated access | **Dropped** (BTS covers air traffic) |
| Google Trends | not tested | No official API; unofficial clients break its terms | **Dropped** (optional in the spec) |

### 3.6 V4.0b findings (2026-10-27)

**Filings.** `tenk fetch` (V4 corpus) got every filing in the window, no problems: 30 per company (4 10-K, 11 10-Q, 15 releases), 24 for CART. Each release's quarter (the last quarter end before it was filed) matched a 10-Q or 10-K period end in every case.

**10-Q structure by rules works** (`preprocess/rules.py`): all 86 10-Qs get Items I.1–I.4, II.1 and II.1A. Two layouts needed handling: EXPE writes "Part I. Item 1." on one line, and TRIP's styled initial reads "I tem 2." in the text.

**Releases: three of eight are images.** ABNB (all), CART (through Q4 2025) and BKNG (since Q4 2024) publish EX-99.1 as slide or page images with a hidden 1-point text layer: the text is there, the tables are not. Extraction (V4.1) must read KPI values from flat text for these, so the extraction gold set must include them.

**Facts.** Company facts gave 44,223 facts and inline XBRL 71,571 more (dimensional and company-specific). Quarters are derived on read from facts under the store's AsOf (no stored quarters table, so nothing goes stale between as-ofs).
- Within a fiscal year a metric uses **one concept**: mixing SBC's income-statement concept (three-month facts) with its cash-flow concept (year-to-date) broke the Q2 arithmetic by $3–6M.
- Q4 ties and year-to-date consistency: **571 of 586 pass at as-of 2026-10-27, 0 fail**; 15 read `restated`, all TRIP. TRIP's Q2 2026 10-Q (filed 2026-08-06) recast 2025 (H1 revenue $927.0M → $828.5M), leaving Q1 and Q3 2025 and Q1 2026 on the old basis until re-reported. A check that fails in a year on two bases reads `restated` (logged as an exception, spec §1.6) instead of `fail`. At as-ofs before the recast (2026-08-06, 2025-05-01) every check passes.
- Gaps to fill from inline XBRL or alternative concepts in V4.3: BKNG net income, ABNB diluted EPS, capex and D&A, LYFT capex, EXPE total liabilities. EPS and diluted shares have no Q4 (they don't add up); Q4 comes from the release (V4.1).

**Market data.** As-of 2026-08-06 sees UBER's 2026-08-05 close (spec example). CPI January 2025 reads its first-published value before its 2026-02-13 revision and the revised value after. DGS10 is taken as published the day after its date (FRED's H.15 timing); Damodaran's monthly ERP three days after the month starts.

### 3.7 V4.1 extraction pilot (2026-10-28)

Gold: 96 labels, BKNG and UBER, Q3 2025 to Q2 2026 releases, 12 fields each (`evaluation/gold/extraction-gold.jsonl`, drafted by `experiments/v4/draft_extraction_gold.py` from table rows and sentences with verbatim quotes, before the extractor ran; the 18 that XBRL also reports match it exactly). **Drafts until a human verifies them** (`verified_by`).

Extractor (`facts/extract.py`, Qwen3.6-27B, thinking off), three runs:

| Run | Exact | Critical | What changed |
|---|---|---|---|
| 1 | 0% | 0% | Thinking on (the model default): every call spent its 6,000 tokens reasoning and returned no JSON |
| 2 | 97.9% | 95.8% | Thinking off. Both misses BKNG Q2 2026 room nights and gross bookings: right values, quotes elided with "...", rejected by the verbatim check, repair failed the same way |
| 3 | **100%** | **100%** | Prompt and repair forbid elided quotes; series quoted from label to number |

Run 3: 7 of 8 releases in one call, no exceptions; 9–14k input tokens per release; ~3–4 minutes each on a GPU shared with another job. The validator also rejects a prior-year column when the row shows growth between two values (`growth_pair`); rows without a growth column (UBER's EPS line) rely on the XBRL reconciliation.

### 3.8 V4.0b exit: V3 agent gold rerun (2026-10-28)

After migration steps M1–M6 and the V4 data layer, the V3 agent gold set (78 questions, Qwen3.6-27B, judge gpt-oss-20b; `output/generation/v4-migration-check/`): **`text` 87.2%, `full` 87.2% correct**, against 85.9–88.5% (`text`) and 85.9–87.2% (`full`) in the two runs before (D-037). Within run-to-run variation, numeric claims grounded 100% in both, so V3 behaves as before. With the offline byte-identity check (`experiments/v4/v3_regress.py`) passing at every step, **V4.0b is done**.

### 3.9 Extraction gold verification (2026-10-28)

The user delegated the spot-check. All 96 labels were checked by methods independent of the drafting script:
GAAP labels (revenue, net income, diluted EPS; 18 of 20) equal the XBRL-derived quarters exactly; the two Q4 EPS
labels (absent from XBRL) agree with net income ÷ diluted shares and with FY − 9M. UBER segment bookings sum to the
total in all four quarters; every two-column UBER KPI matches its printed growth rate (current column is the later);
BKNG's quarter series sum to the printed fiscal-year totals (2024, 2025), fixing the column position. Guidance and
non-GAAP labels are verbatim from their quotes. `verified_by` reads `claude-crosscheck-2026-10-28` (not a human
check). The V4.1 pilot (96/96 exact, critical 100%) therefore stands.

### 3.10 V4.1 exit: all eight companies (2026-10-28)

Gold: 337 labels, all eight companies, Q3 2025 to Q2 2026 (32 releases); the 241 new labels were checked the same way as §3.9.

| Run | Exact | Critical | Gate | Misses |
|---|---|---|---|---|
| 1 (`v4.1-all-qwen3.6-27b`) | 99.1% | 97.6% | fail | TRIP Q4 2025 revenue (current = prior, $411M, rejected as prior-year column); ABNB Q4 2025 GBV and Q1 2026 nights (image text splits labels; model rejoined them in the quote) |
| ABNB + TRIP rerun | | | | Those fixed; new: TRIP Q1 2026 non-GAAP EPS taken for GAAP, ABNB GBV in another quarter |
| 2 (`v4.1-all-qwen3.6-27b-r2`, `b47699f`) | **98.5%** | **100%** | **pass** | EXPE Q3 2025 guidance growth ranges (4 labels, 4 exceptions in that release); ABNB Q3 2025 GBV |

Fixes between runs: equal current/prior values pass the column check; releases too long to send whole send registry-matched chunks first; a failed quote retry shows the model the chunk text; the prompt requires GAAP diluted EPS. Remaining misses are non-critical and logged as exceptions. **V4.1 is done**; next is V4.2 Ask.

### 3.11 V4.2 Ask gold runs (2026-10-28)

Data: `tenk build --stage kpis` loads the extraction of all 117 releases (`v4.2-releases-all`, gate still 98.5% / 100%) as 1,002 KPI and 227 guidance rows. Tool set `ask` (§7.1): `search_filings` (periods, forms), `calculator`, `financials`, `company_kpis`, `guidance`, `filings`, `market_data`; prompt `prompts/ask.md`; `kpi:`, `guide:`, `q:`, `px:`, `macro:` ids resolve and verify (§10.1). Not built at this phase: `crosscheck` (added in V4.7, §3.16) and `research_report` (added later, §7.1; until then questions on saved reports declined).

Gold: `evaluation/gold/ask-gold.jsonl`, 79 questions (`experiments/v4/draft_ask_gold.py`). 54 numeric ones take values from the extraction labels or XBRL quarters (calculations computed from them); 25 written by hand: 12 filing text, 10 declines (our estimates, forecasts, advice, cross-check data, two as-of leaks, a future quarter, a company outside the corpus), 3 false premises. Every evidence quote is checked in its filing at the question's as-of. **Not yet reviewed by a person** (`verified_by: null`). Runner `experiments/v4/ask_run.py` (Qwen3.6-27B, judge gpt-oss-20b): *correct and verified* = correct, and every expected number stated by a verified or calculated claim.

| Run | Correct | Correct and verified | Cited ids resolve | Gate | What changed before it |
|---|---|---|---|---|---|
| 1 (`v4.2-ask-qwen3.6-27b`) | 93.7% | 77.2% | 100% | fail | — |
| 2 (`-r2`, `b524b23`) | 96.2% | 79.5% | 99.5% | fail | Decomposer crashed on any year in a query when the corpus lists no fiscal years (44 failed searches in run 1); prompt: ranges as two claims, decline figures not yet public |
| 3 (`-r3`, `edf2e2e`) | 94.9% | **84.8%** | **100%** | **fail (−0.2 pt, 67 of 79; 68 needed)** | Prompt example shows a range as low and high claims; `filings` rows get a citable id and accessions resolve from the store (run 2 cited an invented `filings:UBER:ER`) |

Run 3 by category (correct and verified): reported financials, KPIs, our estimates, cross-check 100%; filing text 83%; failure cases 80%; guidance 43%. The 12 misses:

- **Agent:** two guidance ranges still written as one claim without a value (CART GTV, LYFT EBITDA); EXPE drivers omit Rapid API; TheFork buyer premise says "sold" for a pending sale.
- **Gold too strict:** six beat-or-miss questions are right (judged 1/1, full range in the text) but claim only the bound beaten, while the runner wants low, high and actual claimed; UBER's decline premise wants "22% constant currency" where the answer gives the reported 24.1% (58,022 / 46,756).
- **Judge:** `trip-thefork-sale` scored 1/3 though the answer states all three points.

The bar was frozen before the first run and is reported as missed. Before any run 4: (1) review the gold set (the hand-written points first); (2) decide whether a beat-or-miss answer needs the unbeaten bound as a claim; (3) recheck the TheFork judgment by hand. Changing gold after seeing answers must be logged as such, and the rerun reported beside these three.

**Run 4 (gold changed after run 3, at the user's decision; `015d3a1`).** Two gold changes: UBER's decline premise accepts the reported 24% beside the 22% constant-currency figure, and a beat-or-miss question expects the actual and the bound it crossed (both bounds when the actual lands within the range), not the unbeaten bound. No code or prompt change.

| Run | Gold | Correct | Correct and verified | Cited ids resolve | Gate |
|---|---|---|---|---|---|
| 3, rescored (`-r3-newgold`) | changed | 96.2% | 91.1% | 100% | pass |
| **4** (`v4.2-ask-qwen3.6-27b-r4`) | changed | 96.2% | **89.9%** | **100%** | **pass** |

The rescore of run 3's unchanged answers shows the gain is the gold change, not the agent (run 4 is within run-to-run spread of it). Run 4 misses (8): two guidance ranges still written as one claim without a value (CART GTV, LYFT EBITDA, as in run 3); LYFT's within-range answer claims one bound; EXPE's beat margin claimed as an unverified $125M; DASH Deliveroo premise has an unverified $2,900M; LYFT working capital misses the insurance-reserves point; EXPE drivers omit Rapid API (as in run 3); `trip-thefork-sale` again judged 1/3 though the answer states all three points. **V4.2 passes** on run 4, with the gold set still unreviewed by a person.

### 3.12 V4.3 exit: history models on the pilot (2026-10-28)

Built: the DSL (`research/model/dsl.py`; Python and Excel evaluators), the graph (`graph.py`; lines × quarters with FY columns, lags skip FY), the S1 data pack (`research/datapack.py`; every input with its citable id and filing date), templates `statements` (condensed §5.2), `segments` (UBER) and `marketplace` (BKNG, EXPE, ABNB), layer-2 checks (`checks.py`), and the workbook Model tab with a LibreOffice parity check (`research/export/workbook.py`). Runner: `python -m tenk_agent.research.model.history <tickers> <as_of>`. History only: forecast columns, `formula_only` on them, and the Assumptions tab come with V4.4.

| As of | UBER | BKNG | Parity (formula cells) |
|---|---|---|---|
| 2026-09-01 | all checks pass | all pass | 313 + 195, no difference |
| 2026-03-01 | all pass | all pass (pre-split basis) | 291 + 177 |
| 2025-08-15 | all pass | all pass | 222 + 136 |

Checks run: `balance`, `quarters_sum`, `q4_ties`, `take_rate`, `segments_sum`, `no_future_input` (413 and 252 inputs), `formula_only` (nothing to test yet). Unit tests break a model on purpose for each check. **Layer 2 passes on the pilot.**

Found while building:

- **UBER balance:** `StockholdersEquity` excludes non-controlling interests; the sheet ties only with total equity and redeemable NCI (new metrics `equity_total`, `mezzanine`; 2026Q2: 65,801 − 37,402 − 28,219 − 180 = 0).
- **BKNG 25-for-1 split (effective 2026-04-02):** later filings restate earlier quarters, earlier ones don't, so shares and EPS mixed bases (815M then 33M). The data pack puts every share and per-share point on the latest visible filing's basis using the tagged ratio, marks it `split_adjusted`, and leaves a pre-split as-of untouched.
- **UBER segment measure changed in 2026Q1** from Segment Adjusted EBITDA to Segment Operating Income (prior year recast); segment EBITDA is missing from 2026, logged, and `segments_sum` skips those periods. UBER Mobility take rate steps from 29.9% (2025Q4) to 25.8% (2026Q1) with segment revenue still summing to total: open, to explain before forecasting.
- Logged gaps: Q4 diluted shares (no XBRL three-month fact; Q4 EPS now falls back to the release), BKNG Q4 SBC, UBER buybacks before 2023Q3 (taken as 0).

Not yet: `orders` (DASH, LYFT, CART) and `trip` (TRIP) templates; EXPE and ABNB are bound to `marketplace` but not yet gated. Both before the V4.4 all-eight run.

**All eight (2026-10-28).** Templates `orders` (DASH, LYFT, CART) and `trip` (TRIP) added; EXPE and ABNB gated on `marketplace`. All eight pass every layer-2 check with full workbook parity at 2026-09-01 and 2025-08-15 (`python -m tenk_agent.research.model.history UBER BKNG EXPE ABNB DASH LYFT CART TRIP <as_of>`). Fixes on the way:

- **Balance:** a second test compares total assets with the reported liabilities-and-equity total for every company; EXPE tags no total liabilities, so its liabilities are derived from that total (and its first test is by construction). Temporary equity: DASH's redeemable NCI (`TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests`, $7–13M) and CART's Series A redeemable preferred ($175–192M, tagged only per class of stock in inline XBRL; the data pack sums classes).
- **TRIP:** the old segments (Tripadvisor brand, Viator, TheFork) sum to the total only with intersegment eliminations, which the registry now extracts (`revenue.eliminations`; TRIP re-extracted, gold 27/27; merged run `v4.3-releases-all`, 1,243 rows). Segment sums take alternative part lists for the redrawn segments. TRIP's 2025 was recast to continuing operations in the 2026 10-Qs, so `quarters_sum` skips recast years (as `q4_ties` already did) and lists them.
- **Missing buybacks** were filled with 0 by a template default, which broke FY sums; they stay blank and logged.
- **Not tagged:** ABNB D&A and capex (no free cash flow line in its history model).
- **UBER Mobility take rate** (29.9% → 25.8% in 2026Q1) is explained: from 2026-01-02 Uber is agent, not principal, in certain UK markets; 2026Q1 Mobility revenue −$1.0B and driver payments −$606M (10-Q MD&A). A structural break: the V4.4 forecast must start from the post-change rate.

### 3.13 V4.4 exit: forecast and backtest (2026-10-28)

Built: forecast columns to 2027Q4 (`research/forecast.py`; graph `forecast`/`forecast_first`/`forecast_late`, separate history and forecast evaluation orders), the Assumptions tab, guidance anchors (a bracketing search solves one assumption per guide: bookings, then revenue, then EBITDA; an anchor the bounds stop is logged), bear/base/bull scenarios (±1σ of each template driver), the bounded LLM proposer (`research/propose.py`), and the walk-forward backtest with a leakage probe (`evaluation/backtest.py`; D = the day after each release's EDGAR acceptance, US Eastern; actuals as first reported). All eight forecast with every layer-2 check passing and full workbook parity. Design changes made while building, each to keep a forecast economically sound: adj. EBITDA = revenue − costs held as a share of gross bookings (§5.3's margin × GB left a take-rate cut without effect on EBITDA); a ratio's change holds until lapped (four quarters), then 0, so a one-off change is not compounded; a quarter missing a year ago steps from two years back (`first()` in the DSL; ABNB's unextracted 2025 GBV).

**Layer 3 gate (90 forecasts, Q1 2025–Q2 2026, defaults): pass.**

| Run | Revenue MAPE (model / naive) | Revenue bias | Main KPI MAPE (model / naive) | Main KPI bias | Wilcoxon p (rev / KPI) |
|---|---|---|---|---|---|
| **defaults (gate)** | **3.14% / 3.77%** | −0.15% | **2.30% / 2.58%** | −0.23% | 0.145 / 0.706 |
| guidance high end | 3.00% / 3.77% | +0.68% | 2.01% / 2.58% | +0.50% | **0.011 / 0.011** |
| proposer `llm` | 3.78% / 3.77% | +1.09% | 2.90% / 2.58% | +0.39% | 0.600 / 0.315 (gate fails) |
| fitted fade | identical to defaults (a next-quarter forecast uses only the starting growth rate) | | | | |
| long horizon, fixed d (32) | 4.03% / 4.18% | −1.15% | 4.18% / 3.38% | −1.51% | 0.624 / 0.304 |
| long horizon, fitted d (32) | 3.29% / 4.18% | −0.14% | 3.73% / 3.38% | −0.55% | 0.845 / 0.278 |
| **seasonal share (2026-11-01, spec §3.2)**: next quarter | 3.14% / 3.77% | −0.15% | 2.30% / 2.58% | −0.23% | 0.145 / 0.706 (identical: the first quarter keeps the q-4 rule) |
| seasonal share: long horizon, fixed d (32) | 4.07% / 4.18% | −1.15% | 4.38% / 3.38% | −1.53% | 0.769 / 0.103 |
| seasonal share: long horizon, fitted d (32) | 3.36% / 4.18% | −0.14% | 3.65% / 3.38% | −0.58% | 0.784 / 0.370 |
| plain LLM, no tools | 56.3% (all 90; 1 within 2%) | | | | |

By company (defaults, revenue model / naive): ABNB 1.4/3.7, BKNG 4.0/4.4, CART 1.5/1.0, DASH 2.2/3.6, EXPE 3.5/2.9, LYFT 5.0/5.6, TRIP 5.9/5.9, UBER 1.6/3.1. Findings: companies beat their own guidance midpoint 43 times in 44, so anchoring at the high end is significantly better (p = 0.011); `guidance_point: mid` stays the default (frozen before the runs) and the change is the user's call. The LLM proposer hurt accuracy, most on TRIP and BKNG; `defaults` stays. The fitted fade helps the year-ahead forecast (bias −0.14%) but the main KPI stays worse than naive a year out. The no-tools LLM is far off (56%), so there is no sign the actuals are memorized; the LLM's training cutoff is not published, so no quarter can be called held out. Leakage probe: every forecast passed (no input filed on or after D).

Extraction ablation (layer 1 gold, 337 labels): rules (label-pattern table rows, `facts/rules.py`) 31.5% exact (KPIs 47.7%, guidance 0%: not attempted) vs the LLM's 98.5% (KPIs 99.5%).

Known limit of the defaults: a structural break (UBER's UK move to agent, 2026Q1) enters as the last y/y change and is held for four forecast quarters from the forecast start, not from the break, so UBER's 2027E adj. EBITDA margin rises to 23.9%. The proposer is the step meant to catch such breaks (it found the UK change in a smoke test) but on the backtest it hurt more than it helped.

### 3.14 V4.5: valuation and Research mode (2026-10-28)

Built: `research/valuation.py` (market cap and EV at the as-of; multiple PT on the sector peers' median 2027E EV/EBITDA, bear/bull ∓/± 15%; DCF on unlevered FCF with Ke = DGS10 + β × ERP, β from 60 monthly returns vs SPY; 60/40 blend; 25/50/25 scenarios; absolute and `--all` relative ratings; knobs in `research.yaml`), the model book and `model:` ids (`research/book.py`; base, scratch-n, val-*, var-*), the model tools `model_view`, `run_scenario`, `compare_peers`, `variance_review`, the Research prompt, and the Research gold set (`evaluation/gold/research-gold.jsonl`, 72 questions: 24 forecast, 16 scenario, 16 valuation, 8 variance review, 8 peer comparison; debates come with Report mode). New metrics: debt (with DASH's convertible notes), short-term investments, interest expense.

**Layer 4: pass.** Identical inputs give byte-identical `valuation.json` for all eight; the PT falls as WACC rises wherever the DCF EV is positive; every target multiple is inside the peer range or flagged. A DCF with a non-positive EV (TRIP: continuing-operations unlevered FCF is negative) is left out of the PT, an exception, so TRIP's PT rests on 14× a thin EBITDA and is fragile. Ratings at 2026-09-01 are driven mostly by the sector-median multiple (DASH at its peers' 8× against its own ~19×); see the limits (§3.16).

**Research gold: miss.** Bars frozen 2026-10-27: ≥ 90% pass (a cited expected model: id that verifies, assumptions listed, no unverified numeric claim) and 100% of forward-looking numbers from model tools.

| Run | Pass | Model ref | Assumptions | From model | What changed before it |
|---|---|---|---|---|---|
| 1 (`v4.5-research-qwen3.6-27b`) | 79.2% | 93.1% | 90.3% | 92.9% | — |
| 2 (`-r2`, `410d4d2`) | **87.5%** | 97.2% | 98.6% | **94.6%** | `compare_peers` lists each company's assumptions (peer questions 12.5% → 87.5%) |
| 2, alternative scoring | 88.9% | 98.6% | 98.6% | 100% | adopted by the user after run 1 (2026-10-28, [V4_RESULTS.md](V4_RESULTS.md#decisions-2026-10-28)); the reported result |

Run 2 by category: valuation 100%, forecast 87.5%, peer comparison 87.5%, scenario 81%, variance review 75%. Remaining misses: the agent multiplies model values itself (take rate × GTV) instead of citing the model's line (CART, BKNG forecasts), scenario answers that cite the base rather than the scratch run, one peer answer. Two scoring corrections proposed after run 1, kept out of the frozen gate and later adopted by the user: a variance answer may cite the bridge's own `var-` ids (the gold expected the prior estimate's revenue id); a claim citing only company guidance is reported data, not a computed number.

### 3.15 V4.6: Report mode (2026-10-28)

Built: the resumable S1–S11 pipeline (`research/pipeline.py`; LLM steps saved and reused with `--resume`), `model_versions` (draft, parent), the events and debates sub-agents (`research/subagents.py`), the note (`research/note/`: facts with sources, ten code-built sections, prose slots written from their own facts, `prompts/section.md`), the number trace and lint (every code-written block verbatim; every prose slot traced to its own facts; one repair turn), and S11 export (`note.md`, `note.pdf`, workbook tabs Model, Assumptions, Summary, Scenarios, Valuation, Revisions, Checks, Log; parity). Layer 5 evaluation: `evaluation/note_eval.py` (rubric criteria frozen before any run: grounded, complete, analysis, balance, clarity). Code-only notes for all eight: 100% traced, full parity.

**Layer 5: UBER pass, BKNG miss.** UBER (2026-08-06): ten sections, 100% traced, 60/60 seeded errors caught, rubric 4.2. BKNG (2026-08-05): ten sections, 100% traced, 60/60 caught, rubric 3.8 then 3.4 on near-identical text (judge noise ±0.4; grounding and analysis scored 3). Fixes before the final scores, each to the checker or to facts, none to the notes' text: BKNG's first note had one untraced figure (a debated assumption, now a fact); the first seeded check missed 1 and 6 corruptions (a figure changed to another fact's value passed a global trace), fixed by checking code-written blocks verbatim and tracing prose to its own slot.

### 3.16 V4.7: scale and write-up (2026-10-28)

Built: NYC TLC (44 months, HV0003 Uber and HV0005 Lyft confirmed) and BTS airline passengers (`acquisition/crosscheck.py`; `cc:` ids; `crosscheck` tool; as-of by publish date), Form 4 (`acquisition/insiders.py`; 8,418 transactions; dilution context in note section 9), the rules extractor and fitted-fade, guidance-high, LLM-proposer and long-horizon ablations, the no-tools LLM baseline, `--all` ratings. Results table, chart, example note and limits: **`docs/V4_RESULTS.md`**. Not run: Qwen vs Claude on the LLM steps (later dropped: not in the spec); the cross-check ablation (no target-quarter data is ever public at the forecast date); a human sample of the rubric.

---

## Old design-doc section numbers

The design doc was restructured on 2026-11-01. Bare "§n" references in this log use the old numbers:

| Old § | Topic | Now (design doc) |
|---|---|---|
| 3 | As-of rule | §2.5 |
| 4.1–4.3 | Companies, acquisition, store | §3 |
| 4.4 | Text over new forms | §3.4 |
| 4.5 | Structured facts | §3.5 |
| 4.6 | KPI and guidance extraction | §6 |
| 5 | Model engine | §7.3–7.4 |
| 6.1 | Forecast | §7.5 |
| 6.2 | Valuation | §7.7 |
| 7 | Tools | §5.2 |
| 7.3 | Proposer | §7.6 |
| 8 | Context management | §5.5 |
| 9 | Report pipeline | §8 |
| 10 | Number trace, evidence log | §9 |
| 11 | Outputs | §10 |
| 12 | New technical problems | §13 |
| 13 | Evaluation and backtest | §11 |
| 14 | Code layout, config, CLI | Appendix |
| 14.1, 15, 17 | Migration, build order, findings | this log, §2, §1, §3 |
| 16 | Decisions | [DECISIONS.md](DECISIONS.md) D-040 to D-044 |
