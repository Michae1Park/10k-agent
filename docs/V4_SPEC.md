# Junior Sell-Side Equity Research Agent: Spec

> **Status:** this spec and the [design doc](V4_TECHNICAL_DESIGN_DOC.md) agree; the build follows both. Results: [V4_RESULTS.md](V4_RESULTS.md).

A portfolio project: one agent covers eight US travel and mobility companies using only free, public data. It answers questions, runs the analyst loop on demand (model, forecast, value) and writes a draft sell-side note. A human reviews every rating and price target.

Four modes (details in §2):

| Mode | You give it | You get |
| --- | --- | --- |
| **Ask** | A question | A short cited answer from filed data |
| **Research** | A question | An analytical answer from the model, forecast or valuation |
| **Investigate** | A multi-part question | A planned answer: the question split into Ask and Research runs, then combined |
| **Report** | A ticker and as-of | A draft note, a model-change workbook and an evidence log |

**Every run takes an as-of date.** The agent sees only data published strictly before it, which is what makes backtesting possible (§6).

---

## 1. Inputs: what the system consumes

### 1.1 Companies

| Company | Ticker | Travel | Mobility and delivery |
| --- | --- | :---: | :---: |
| Booking | BKNG | ✓ |  |
| Expedia | EXPE | ✓ |  |
| Airbnb | ABNB | ✓ |  |
| TripAdvisor | TRIP | ✓ |  |
| Uber | UBER |  | ✓ |
| Lyft | LYFT |  | ✓ |
| DoorDash | DASH |  | ✓ |
| Instacart | CART |  | ✓ |

- All have a December fiscal year-end.
- Peers for comps: the other companies in the same sector.
- Instacart (legal name Maplebear Inc.) IPO'd on 2023-09-19:
  - Data starts at Q3 2023: 12 releases, 9 10-Qs, 3 10-Ks.
  - 6 backtest points, like the others (its Q4 2024 release onward).
  - Its S-1 is not fetched.

### 1.2 Data sources

Window: **filings made from 2023-01-01 through the day before the as-of date**.

- Starts at Q4 2022: the FY2022 10-K and Q4 2022 release were filed in early 2023.
- Example, as-of 2026-10-27: Q4 2022 to Q2 2026 (15 periods); Q3 2026 not yet included.
- Each number carries its source document, period and filing or publish date.

| Source | What | Period | Used for |
| --- | --- | --- | --- |
| **10-K** (EDGAR) | Full text and financial statements | FY2022 (filed early 2023), FY2023, FY2024, FY2025; CART from FY2023 (+ earlier years via XBRL) | Annual financials, risks, business description, Q4 derivation |
| **10-Q** (EDGAR) | Full text and financial statements | Q1 2023 to Q2 2026 (11 per company; CART 9) | Quarterly financials, MD&A |
| **8-K Item 2.02, Ex. 99.1** | Earnings release and shareholder letter | Q4 2022 to Q2 2026 (15 per company; CART 12) | KPIs, non-GAAP reconciliations, next-quarter guidance |
| **XBRL company facts** (EDGAR API) and inline XBRL | Structured financial values, each with its filing date | All periods above | Core numbers, point-in-time history |
| **DEF 14A** (EDGAR) | Annual proxy statement, text only (DEFC14A when contested) | First filed each year, 2023 to as-of; keyed to the fiscal year it reports on (FY2022 to FY2025; CART from FY2023) | Executive pay, equity plans and share requests: dilution context |
| **Form 4** (EDGAR) | Insider transactions | 2023 to as-of | Dilution context |
| **Daily prices** (Tiingo; internal use only, never published) | Daily close, adjusted close and volume per ticker, plus SPY | 2023 to as-of | Market cap, EV, beta, backtest prices |
| **FRED / ALFRED** | 10-year Treasury (DGS10), CPI (CPIAUCSL), consumer sentiment (UMCSENT), USD index (DTWEXBGS), restaurant sales (RSFSDP); revised series use each value's first-published date | 2023 to as-of | Discount rate and demand inputs |
| **Damodaran** | Implied equity risk premium | Latest before as-of | DCF |
| **BTS** | Monthly US airline passengers (publish date estimated as month end + 90 days) | 2023 to as-of | Travel forecast input and cross-check |
| **NYC TLC** | Monthly high-volume for-hire trips per license (HV0003 Uber, HV0005 Lyft; ~2 months lag, NYC only) | 2023 to as-of | Uber and Lyft forecast input; Uber vs Lyft trip cross-check |

### 1.3 Non-numerical data

| Text | Source | Used by |
| --- | --- | --- |
| Risk factors | 10-K Item 1A; 10-Q risk-factor updates | Ask, Research, Report |
| MD&A | 10-K, 10-Q | Ask, Research, Report |
| Management commentary | Earnings release and shareholder letter | Ask, Research, Report |
| Business description | 10-K Item 1 | Ask, Research |
| Executive pay, equity plans | DEF 14A | Ask, Research |

### 1.4 Not available (free-data limits)

| Missing | Handling |
| --- | --- |
| Consensus estimates (paid) | Benchmark against management guidance and baselines |
| Call transcripts | Use releases, shareholder letters and 10-Q text |
| Paid alternative data (hotel occupancy, app usage, card spend) | BTS and NYC TLC as forecast inputs and directional checks, with coverage limits stated |
| **Stooq** prices | Dropped: a bot check blocks scripts; Tiingo replaces it |
| **TSA**: daily US air passengers (2023 to as-of) | Dropped: the site blocks automated access (HTTP 403); BTS covers air traffic |
| **Google Trends**: search interest (weak, noisy; 2023 to as-of) | Dropped: no official API, and unofficial clients break its terms |

### 1.5 Core KPIs

**What each company reports** (checked against each company's Q2 2026 release):

| KPI | BKNG | EXPE | ABNB | TRIP | UBER | LYFT | DASH | CART |
| --- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Revenue | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Adjusted EBITDA | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Free cash flow | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Net income | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Diluted EPS | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Gross bookings / GOV / GTV ¹ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Volume: room nights, nights, bookings, trips, rides, orders ² | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Segment split ³ |  | ✓ |  | ✓ | ✓ |  |  | ✓ |
| Adjusted EPS | ✓ | ✓ |  | ✓ | ✓ |  |  |  |
| Users: MAPCs, active riders ⁴ |  |  |  |  | ✓ | ✓ |  |  |
| Price per unit: ADR, AOV ⁵ |  | ✓ | ✓ |  |  |  |  | ✓ |
| Take rate / net revenue margin ⁶ |  |  |  |  |  |  | ✓ | ✓ |

- ¹ ABNB: gross booking value. TRIP: Viator gross bookings value. DASH: marketplace GOV. CART: GTV.
- ² ABNB: nights and experiences booked. TRIP: Viator experience bookings.
- ³ EXPE: B2C and B2B. TRIP: Tripadvisor, Viator, TheFork. UBER: Mobility, Delivery, Freight. CART: transaction and advertising revenue.
- ⁴ DASH gives monthly active users only as a December figure in its Q4 letter, and describes them in other quarters without a number.
- ⁵ BKNG mentions ADR only in a footnote; DASH cites average order value as a driver without a figure.
- ⁶ DASH: net revenue margin. CART: revenue as a share of GTV. The others state no take rate.

**How we get each one:**

| KPI | From the release (`facts/kpis.yaml`) | From XBRL | Derived by the model |
| --- | --- | --- | --- |
| Revenue | All 8, with segments for EXPE, TRIP, UBER, CART | All 8 | |
| Adjusted EBITDA | All 8, with segments for TRIP, UBER | | |
| Free cash flow | All 8 (BKNG 10 of 15 quarters, LYFT 11, CART 2 of 12) | Operating cash flow - capex, where available | |
| Net income | BKNG, ABNB, CART | All but BKNG | |
| Diluted EPS | All 8 | Direct quarterly and annual facts; Q4 from the release | |
| Gross bookings / GOV / GTV | All 8 (TRIP: Viator, 13 of 15 quarters) | | |
| Volume | All 8 (TRIP: experience bookings, 6 of 15 quarters) | | |
| Segment split | EXPE, TRIP, UBER, CART | | |
| Users | UBER, LYFT | | |
| Adjusted EPS | BKNG, EXPE, TRIP; UBER from Q4 2025 (3 quarters) | | |
| Take rate | | | Revenue / gross bookings (DASH, CART also state it) |
| Price per unit | | | Gross bookings / volume |

### 1.6 Collection rules

- Where a figure is both tagged in XBRL and printed in the release, the model reads XBRL; the two are not reconciled. The release supplies the KPIs XBRL lacks (§1.5).
- Restate history when a KPI definition or segment structure changes, and note it.
- Keep raw data separate from adjusted figures.
- Q4 = full year minus (Q1 + Q2 + Q3), reconciled to the 10-K.
- Send an identifying User-Agent to EDGAR and respect its rate limit (SEC allows 10 requests per second; the client sends one per 0.12 s).

---

## 2. Outputs: four product modes

| Mode | Input | Output | Runs |
| --- | --- | --- | --- |
| **Ask** | A question + as-of | Short cited answer from filed data | Seconds to minutes |
| **Research** | A question + as-of | Analytical answer from the model, forecast or valuation, with assumptions shown | Minutes |
| **Investigate** | A multi-part question + as-of | A combined answer, with the plan, each sub-answer and its claims shown | Minutes to tens of minutes |
| **Report** | Ticker + as-of | A 10-section draft note, a model-change workbook (`.xlsx`) and an evidence and exception log | Target: under 30 min per company |

Ask and Research both answer questions. Ask retrieves what was reported; Research computes something new (an estimate, scenario or valuation) by running the model. Investigate answers a question too broad for one run: it plans sub-questions, runs each as Ask or Research, and combines their verified answers (§2.4).

When a comparison needs a KPI that some of the companies do not report (§1.5), Ask and Research say which companies lack it, and never fill the gap with an estimate. Where definitions differ between companies, they say so.

### 2.1 Ask mode: example question per category

| Category | Example question |
| --- | --- |
| Reported financials | What was Lyft's revenue and adjusted EBITDA in Q2 2026? |
| Operating KPIs | What was Airbnb's take rate in Q2 2026, and how does it compare with Booking's? |
| Guidance | What did DoorDash guide for Q3 2026 adjusted EBITDA? |
| Filing text | What new risk factors did Instacart add in its latest 10-K? |
| Cross-check data | How did NYC trips for Uber and Lyft compare in May 2026? |
| Our estimates | What is our 2027E adjusted EBITDA for Uber? *(cites a generated report)* |

### 2.2 Research mode: example question per category

| Category | Example question |
| --- | --- |
| Forecast | What do you expect for Expedia's Q3 2026 gross bookings, and how does that compare with guidance? |
| Scenario | If Uber's Mobility take rate falls 50 bps, what happens to 2027E EBITDA and the price target? |
| Valuation | What is a fair price for DoorDash on EV / EBITDA, and what does the DCF say? |
| Peer comparison | Which of the eight is cheapest on 2027E EV / (adjusted EBITDA - SBC)? |
| Variance review | Why did our Q2 2026 revenue estimate for Lyft miss? |
| Debates | What are the bull and bear arguments on Airbnb's growth? |

### 2.3 Report mode: example output

A report run hands the senior analyst **three artifacts in two files** (the log is a tab of the workbook). The split lets the senior read the investment case without the audit trail, and still drill into any number.

| Artifact | Reader's question | Contents |
| --- | --- | --- |
| **Research report** (the note) | What happened, what do we change, what do you decide? | 10 sections: results, drivers, estimates and financials; valuation, risks, catalysts and review controls |
| **Model-change workbook** (`.xlsx`) | Exactly what moved in the model? | Old vs. new estimates, assumptions, formula outputs, scenarios, valuation, integrity checks |
| **Evidence and exception log** | Where does this come from, and what is not settled? | One row per figure in the note with its source, plus every data gap and failed check. Delivered as the `Log` tab of the workbook |

*Illustrative. Every figure below is invented to show the format; real numbers come from the model. The invented figures reconcile with each other, except that the annual figures (FY2025A and FY2026E revenue and adj. EBITDA) do not match the quarterly run-rates that the Q2 and y/y figures imply.* The example workbook, [`EXAMPLE_UBER_Q2_2026_MODEL_CHANGE_WORKBOOK.xlsx`](EXAMPLE_UBER_Q2_2026_MODEL_CHANGE_WORKBOOK.xlsx), reproduces the figures in sections 2, 4, 6 and 7 of the note with live formulas.

**The research report**

```text
UBER TECHNOLOGIES (UBER)  ·  Earnings update  ·  Q2 2026 (Apr–Jun)
As of 2026-08-06  ·  Status: DRAFT for senior analyst review  ·  Prepared by: research agent
No rating or PT is approved until the responsible analyst confirms it.

1. EXECUTIVE SUMMARY
   Draft rating: Buy    Price: $80.00    12-month PT: $95.00    Return: +18.8%
   Market cap: $165B    EV: $168B        52-week: $60–$90       Next earnings: 2026-11-05 (est.)
   Rating / PT change: none vs. the prior version (v1)

   Takeaways
   - Beat: gross bookings, revenue, adjusted EBITDA and trips all exceeded our estimates;
     bookings beat the guidance midpoint by 2.3%.
   - Outlook: Q3 guidance implies bookings +6.5% and adj. EBITDA +8% on Q2; our Q3 forecast
     is anchored to the guidance midpoints.
   - Estimates: 2026E adj. EBITDA +3.5% and 2027E +3.2%, mostly the Q2 beat and Mobility growth.
   - Valuation: 17.3x 2027E EV/EBITDA vs. a peer median of 19x.
   Thesis (hypothesis): Mobility growth plus a falling cost share of bookings compound faster than the
   price implies. We have not shown the market is wrong; no consensus is available.

2. EARNINGS RESULTS
   2.1 Headline           Actual  Our est.  Var.   Guide mid  Var. vs mid  Y/Y
   Gross bookings ($B)    48.1    47.5      +1.3%  47.0       +2.3%        +19%
   Revenue ($B)           12.9    12.6      +2.4%  n/a        n/a          +17%
   Adj. EBITDA ($B)       2.08    2.0       +4.0%  2.0        +4.0%        +30%
   Trips (B)              3.4     3.3       +3.0%  n/a        n/a          +16%
   Our estimate: the model as of the day after the Q1 release. Variances use unrounded values.

   2.2 Guidance           Midpoint   Our forecast   Var. vs mid
   Q3 gross bookings ($B) 51.25      51.25          0.0%
   Q3 adj. EBITDA ($B)    2.25       2.25           0.0%

   2.3 Quality of the quarter
                          Q2 2026   Q2 2025   Change
   Trips (B)              3.4       2.93      +16%
   Bookings per trip ($)  14.15     13.79     +2.6%
   Take rate              26.8%     27.2%     -40 bps
   Adj. EBITDA ($B)       2.08      1.60      +30%
   FCF ($B)               2.3       1.7       +35%
   A high-quality beat: growth is volume-led (about 16 pts from trips, 3 pts from price), and FCF
   of 2.3 covers adj. EBITDA. The take rate fell 40 bps, so revenue grew slower than bookings;
   the release does not say why, so that stays open.

3. DRIVERS AND KEY DEBATES
   3.1 Can Mobility sustain high-teens growth?
   Bull: trips +16% with rising bookings per trip.
   Bear: growth fades as comps get harder; the default fade (x0.90 per quarter) takes it to
   about 11% for 2027E.
   Hypothesis: the default fade is conservative while trips grow +16%.
   Trigger: Mobility bookings growth below 14% for two quarters.
   Data gap: NYC TLC covers NYC only, so it is a directional check, not proof.

   3.2 Will the take rate keep falling?
   Bull: the drop is mix, not pricing.  Bear: incentives rise with competition.
   Hypothesis: the take rate holds near 26.8% for 2026–2027E.
   Trigger: a third quarter of y/y decline.
   Data gap: incentive spend is not disclosed.

4. ESTIMATE REVISIONS AND MODEL IMPACT
   4.1                        Old    New    Change   Source                  Confidence
   Q3 2026E revenue ($B)      13.4   13.6   +1.5%    Reported + assumption   Medium
   Q3 2026E adj. EBITDA ($B)  2.20   2.25   +2.3%    Reported + assumption   Medium
   2026E revenue ($B)         50.1   51.0   +1.8%    Reported + assumption   Medium
   2026E adj. EBITDA ($B)     8.6    8.9    +3.5%    Reported + assumption   Medium
   2027E adj. EBITDA ($B)     9.4    9.7    +3.2%    Reported + assumption   Low
   The same rows: model-change workbook, tab "Revisions".

   4.2 Integrity checks (layer 2)
   [pass] balance  [pass] quarters_sum  [pass] q4_ties  [pass] take_rate  [pass] segments_sum
   [pass] no_future_input  [pass] formula_only  [pass] wacc_gt_g  [pass] weights
   Original model preserved; proposed changes are shown as a separate version.

5. FINANCIAL SUMMARY     2024A   2025A   2026E   2027E
   Revenue ($B)          44.0    49.0    51.0    57.0
   Growth                18%     11%     4%      12%
   Gross bookings ($B)   ...
   Adj. EBITDA ($B)      6.5     8.0     8.9     9.7
   Adj. EBITDA margin    ...
   GAAP op. income ($B)  ...
   SBC ($B)              ...
   EPS ($)               ...
   FCF ($B)              ...
   Diluted shares (B)    ...
   Quarterly detail, segments and the assumption register: workbook.

6. VALUATION
   Comps (2027E EV/EBITDA): LYFT 13x · CART 19x · DASH 20x · median 19x · UBER now 17.3x.
   Scenario   PT     Multiple   DCF / share   Weight
   Bear       $70    $66        $76           25%
   Base       $94    $88        $103          50%
   Bull       $122   $118       $128          25%
   Bear and bull move the scenario drivers ±1σ and the multiple ±15% (16x and 22x).
   Method: 60% EV/EBITDA + 40% DCF; WACC 9.5%, β 1.2.
   Probability-weighted price target: $95; return +18.8% → Buy.
   The 19x target is the peer median, a premium to UBER's current 17.3x, backed by faster growth
   than LYFT; the DCF at $103 sits above the multiple value.

7. RISKS AND SENSITIVITY
   Risk                    2027E adj. EBITDA   PT impact
   Take rate -50 bps       -$1.1B              about -$10
   Mobility growth -1 pt   -$0.06B             about -$0.6
   Cost share +25 bps      -$0.5B              about -$5
   Probabilities beyond the scenario weights in section 6 are not assigned.

8. CATALYSTS
   2026-11-05: next earnings (estimated from the last release date; not announced). Watch the
   guided lines in section 2.2.
   Events from filings (S2): none found.

9. SOURCES, ASSUMPTIONS, DISCLOSURES
   Data as of 2026-08-06: 10-Q (filed ...), 8-K Ex. 99.1 (filed ...), Tiingo prices, FRED DGS10.
   Key assumptions: gb_g_mobility.g0 = 0.17; fade = 0.90; cost_to_gb.shift0 (guidance); ...
   Insiders (Form 4), Q2 2026: 1.2M shares sold ($95M) and 3.4M awarded.
   Full register: workbook tab "Assumptions". Source for each figure: evidence log.
   Exceptions: 3 (1 warning, 2 info); see the exception log (X-rows).
   Rating definitions: Buy ≥ +15% 12-month return, Sell ≤ -10%, Hold between.

10. SENIOR ANALYST REVIEW
   Status: DRAFT. Decisions needed:
   1. Approve or amend the reading of the quarter (section 2.3).
   2. Approve or reject the estimate changes (section 4).
   3. Resolve open issues X-01 (exception log).
   4. Approve the valuation method, target multiple and scenario weights (section 6).
   5. Confirm the rating and price target.
   6. Approve the note for distribution.
   Reviewer: ______  Approve rating ☐  Approve PT ☐  Approve estimates ☐
```

**The model-change workbook**

Example file: [`EXAMPLE_UBER_Q2_2026_MODEL_CHANGE_WORKBOOK.xlsx`](EXAMPLE_UBER_Q2_2026_MODEL_CHANGE_WORKBOOK.xlsx). Blue cells are inputs, black are formulas, green link to another tab; change an input and everything downstream recalculates. It is an excerpt: the Model tab shows Q2 KPIs, segments, guidance and an annual summary, not the full three statements.

| Tab | Contents |
| --- | --- |
| Model | Three statements by quarter and fiscal year: inputs are blue values with their source id as a cell note; every other cell is a formula |
| Assumptions | Every assumption, name and value, as blue cells the Model formulas reference |
| Summary | Rating, price, price target, return, market cap, EV, net debt, diluted shares, WACC, parity result |
| Scenarios | Bear, base and bull headline lines by year |
| Valuation | Peer multiples and median, cost of capital, each scenario's multiple value, DCF value and PT, the DCF grid |
| DCF | 10-year DCF with live formulas, terminal value share of EV, WACC x terminal-growth grid |
| Revisions | Old vs. new estimates, source and confidence (old value is the denominator) |
| Model (prior) | The prior version's lines and values as saved, or "initial draft (no prior run)" |
| Checks | Layer 2 results, one row per check; any FAIL blocks the note |
| Log | Evidence and exception rows (below) |

**The evidence and exception log**

| Part | Fields |
| --- | --- |
| Evidence | ID (`E-n`), figure, value, source id |
| Exceptions | ID (`X-n`), severity (info: a data gap or default; warning: an anchor missed, a failed LLM step, a dropped sentence, the time budget; error: a failed integrity, consistency or number-trace check), item |

Example exception rows:

| ID | Severity | Item |
| --- | --- | --- |
| X-01 | warning | Section 3.1: a sentence with an untraced number was dropped |
| X-02 | info | Freight adj. EBITDA missing in 2023Q4; left blank |
| X-03 | info | Working capital held at 0 in the forecast |

Note rules:
- Each section opens with its conclusion, then the evidence.
- Every number traces to the model or an evidence-log row.
- A gap says why: "n/a: not in free data" or "failed check: reason", never "pending".
- Never fill a gap with an estimate shown as fact; never resolve a conflict silently.
- Estimates read "2027E", actuals "2025A"; label periods once as "Q2 2026 (Apr–Jun)".
- A report takes its "old" estimates from the latest earlier report for the same ticker (the latest version with an earlier as-of date). Section 4 and the workbook Revisions tab compare against it, and Section 1 states the rating and PT change. If none exists, the run is flagged "initial draft (no prior run)" and Section 4 shows no old values.

**How a report is filled**

Report mode is a fixed pipeline, not an open agent loop. It uses the same tools and data as Ask (filings search, facts, KPIs) plus the model and valuation code, but it does not start Ask or Research runs. Code computes every number. The LLM interprets and writes.

| Section | Built by | Inputs |
| --- | --- | --- |
| 2.1, 2.2, 4, 5, 7, 9 | Code | Model, guidance, revisions, checks, sensitivities |
| 2.3 Quality of the quarter | Code table, plus an LLM paragraph | KPI and cash-flow facts |
| 3 Drivers and debates | Bounded research, one per scenario driver | Driver trend, management commentary, risk factors |
| 6 Valuation | Code, plus an LLM justification of the target multiple | Comps, DCF |
| 8 Catalysts | Code | Next earnings date (estimated), events found in filings |
| 1 Executive summary | Code header, plus LLM takeaways; written last | The finished sections |
| 10 Senior analyst review | Code; written last | Warning and error exceptions |

Requirements:

1. **Fixed fact set per section.** Each LLM paragraph gets only its section's facts (model cells, KPIs, debate evidence). The LLM never decides what data exists.
2. **Bounded research for debates and events.** One run finds the quarter's events, and one run per scenario driver writes its debate. Each uses the Ask toolset and a call budget, does not plan, split into sub-questions or start other runs, and returns a short conclusion plus the ids it cited.
3. **Number trace.** Every figure in LLM text must trace to the model or an evidence-log row. Research output becomes facts first. Untraced sentences get one repair turn, then are dropped and logged.
4. **Sections 1 and 10 are written last.** They summarize the finished sections. Section 1 carries the rating, price target and takeaways. Section 10 lists the decisions needed and the warning and error exceptions. Neither adds a new number or claim.
5. **Consistency check before export.** Code runs these checks:
   - the rating matches the return thresholds in §3.3
   - every number in Section 1 equals the same number in its section table
   - every debated driver appears in the Section 7 sensitivities
   - a guidance change in the events appears in 2.2
   - every warning and error exception is in Section 10
6. **Flag, don't rewrite.** A failed check becomes an exception row. Section 1's takeaways get one repair turn; other sections are never rewritten.
7. **Degrade, don't stop.** If a research run fails or hits its budget, the section reads "failed check: reason" and the run continues. A failed model-integrity or valuation check still stops the run.
8. **Time.** Target under 30 minutes per company. Each LLM step has a call and token budget, and the budgets sum to well under that target.

Not required in Report: a planner, multi-step decomposition of sections, or an agent that coordinates other agents. Planning lives in Investigate (§2.4).

### 2.4 Investigate mode: example question per category

| Category | Example question |
| --- | --- |
| Peer comparison with reasons | Which of Uber and Lyft has the better adjusted EBITDA margin trajectory, and what does each management say drives it? |
| Reported vs modeled | DoorDash beat its Q2 2026 GOV guidance; does our model expect that to continue, and what would a 1-point lower growth rate do to the price target? |
| Sector view | Across the four travel companies, whose take rate moved most over the last year, and does that company trade at a premium? |
| What changed | What changed for Expedia since its last release: guidance, KPIs and new risk factors? |

Requirements:

1. **Plan, then run.** One LLM call splits the question into at most 5 sub-questions, each marked Ask or Research. Each runs as a normal Ask or Research run, with its own budget and claim check.
2. **One replan.** After the sub-answers return, the planner may add up to 2 follow-up sub-questions once, for a gap or a surprise. It cannot remove or rerun one.
3. **Combine from verified answers only.** A final LLM call, with no tools, writes the answer from the sub-answers. It may cite only ids the sub-answers cited and may combine their numbers only through calculations the claim check reruns. It adds no new data.
4. **Same checks.** The final answer passes the claim check (every id resolves at the as-of). Sub-answers that failed or went unverified are shown as gaps, never filled.
5. **Shown work.** The output lists the plan, each sub-question with its mode and answer, and the final answer.
6. **Degrade, don't stop.** If the plan fails, the whole question runs as one Research run. If a sub-run fails, the answer names the gap.
7. **No Report runs.** Investigate starts Ask and Research runs only; it reads saved reports through Research's tools.

---

## 3. How it works

```text
Data pack  →  Model  →  Assumptions  →  Forecast  →  Valuation + rating  →  Note
(code+LLM)   (code)     (code defaults; (code)       (code)                 (LLM reasons and writes,
                         optional LLM                                          number-traced)
                         proposer, code
                         bounds)
```

Every number comes from deterministic code, and the rating is a threshold rule on the price-target return (§3.3). The LLM extracts KPIs, optionally proposes assumptions with a rationale and confidence (a switch, off by default; §7), reasons over filings text and tool results (drivers, risks, debates such as autonomous vehicles and take rate), and writes the note. Its conclusions reach the model only through the proposer's bounded assumptions, which code clips.

### 3.1 Model (per company, quarterly, three statements)

Operating KPIs drive revenue, revenue drives costs and profit, profit drives cash flow and balance sheet.

```text
Gross bookings = volume x price     (orders x AOV; room nights x ADR; rides x fare)
Revenue        = Gross bookings x take rate
EPS            = Net income / diluted shares
FCF            = Operating cash flow - capex
Assets         = Liabilities + equity       (must balance, or the model fails)
```

- **Timing (simplification):** every company is modeled as Uber or Instacart is: an order executes at once and the take rate is applied in the same quarter. Travel customers actually prepay, so revenue lags bookings (deferred merchant bookings). That lag is not modeled; it sits inside the take rate. Known limitation: the balance sheet has no deferred-bookings line, forecast working capital change is 0, and cash flow misses the prepayment float.
- **BKNG, EXPE, ABNB:** room nights (ABNB: nights and experiences) x gross bookings per night.
- **Uber:** gross bookings grow by segment (Mobility, Delivery, Freight), each x its own take rate; adjusted EBITDA = revenue - costs, with costs forecast as a fixed share of total gross bookings, so a take-rate change moves EBITDA.
- **DoorDash, Lyft, Instacart:** orders or rides x value per unit, x one take rate. Instacart's transaction and advertising revenue are reported but not modeled separately.
- **TripAdvisor:** no volume KPI reported in enough quarters to model (§1.5), so revenue grows on one faded rate and adjusted EBITDA is a margin on revenue.
- Take rate is always computed from reported revenue and reported gross bookings.
- Forecast columns are formulas only; assumptions sit in one input block; quarters sum to years; SBC is shown separately; the balance sheet is condensed (cash, total assets, liabilities, equity).

### 3.2 Forecast

| Rule | Detail |
| --- | --- |
| Per driver, not per total | First forecast quarter: `Driver(q) = Driver(q-4) x (1 + g)`. Later quarters: the driver's last full year grown at the faded rate, x the quarter's 3-year average seasonal share |
| Growth fade | `g(t+1) = g(t) x d`, default `d = 0.90` |
| Anchor | The first forecast quarter is solved to the guidance midpoint; where a company guides only the full year (ABNB's EBITDA margin), that year is the anchor |
| Scenarios | Bear / base / bull (25 / 50 / 25%) by varying the 2–3 key drivers |
| Outside inputs | BTS, NYC TLC, FRED: nudge the first growth rate of a mapped driver |
| Horizon | Quarterly through Q4 of the as-of year + 1 (Q4 2027E at a 2026 as-of); annual columns to match |

| Symbol | Meaning |
| --- | --- |
| `Driver(q)` | Forecast value of one operating driver (trips, bookings per trip, take rate) in quarter q |
| `q-4` | The same quarter one year earlier |
| `g` | Year-over-year growth rate of the driver |
| `g(t)`, `g(t+1)` | The growth rate in quarter t and in the next quarter |
| `d` | Fade factor: each quarter's growth is `d` times the previous quarter's (default 0.90). Example: 16.0% → 14.4% → 13.0% → 11.7% |

### 3.3 Valuation and rating

| Item | Default |
| --- | --- |
| Multiple method | EV / adjusted EBITDA on the as-of year + 1 (2027E at a 2026 as-of). EV / (EBITDA - SBC), P/E and FCF yield are also computed, for peer comparison. Computed by the agent from filings and prices |
| DCF | 10 years (the modeled years, then faded), SBC treated as a cost, terminal growth 3.0%, beta from a regression of monthly returns since 2023 (the data window), one ERP for all eight |
| Price target | `0.6 x multiple PT + 0.4 x DCF PT` per scenario; the price target is the probability-weighted value (25/50/25), which sets the return and rating; bear, base and bull also shown |
| Rating (absolute; the note's rating) | Buy: 12-month return of +15% or more. Sell: -10% or less. Hold: between |
| Rating (relative; a coverage ranking only) | Rank the eight by expected return: top third Buy, bottom third Sell, the rest Hold (2 / 4 / 2) |

Every DCF includes a price-per-share grid against WACC and terminal growth.

---

## 4. Out of scope

- Ad platforms and e-commerce companies.
- Estimates or ratings on airlines and hotel operators.
- Real-time operation and publishing ratings to outside readers.

---

## 5. Evaluation: five layers

| Layer | Test | Pass bar |
| --- | --- | --- |
| 1. Extraction | 337 hand-checked values from 32 releases (4 quarters x 8 companies): one label per value each release prints, 7–14 per release, since the companies report different fields (§1.5) | 98% exact match; 100% on revenue, EPS and main KPI |
| 2. Model integrity | Automated checks: balance sheet balances, quarters sum to years, Q4 matches 10-K, take rate recomputes, segments sum to totals, no input dated after as-of, forecast cells are formulas, WACC > terminal growth, weights sum to 1 | 100% pass; any failure blocks forecasting |
| 3. Forecast accuracy | Walk-forward backtest (Section 6) | Lower MAPE than seasonal-naive on revenue and main KPI; bias near zero |
| 4. Valuation | Re-run with identical inputs; WACC sensitivity sweep | Identical output; every target multiple inside the peer range or flagged |
| 5. Note quality | Number trace, all 10 sections present, rubric review, seeded errors | 100% of figures traced; rubric average of at least 4 of 5 |

Ask and Research each get a gold question set across the eight companies (79 Ask and 72 Research questions), with pass bars set before the first run.

Investigate gets its own gold set of 10–15 multi-part questions. The same questions also go to a single Research run as the baseline. Pass bar: Investigate scores higher than the baseline on correct-and-verified answers, and 100% of its ids resolve. A miss is reported as a miss.

## 6. Backtest

- **Point-in-time rule:** at each as-of date, use only filings with a filing date before it; the as-of is the day after the release's EDGAR acceptance.
- **Walk-forward:** for each quarter from the Q4 2024 releases onward, set the as-of date just after the earnings release, forecast the next quarter, move forward one quarter. 6 forecasts per company, Instacart included: 48 per metric.
- **Metrics:** MAPE, bias (positive means forecasts too high), and guidance call (does revenue land above or below the guidance midpoint).
- **Baselines:** seasonal naive (same quarter last year x latest YoY growth), guidance midpoint, and a plain LLM with no tools.
- **Reporting:** medians and ranges by company, plus a paired Wilcoxon test.
- **Long run:** from each Q4 2024 release, all four quarters of 2025, to test the fade.

**Ablations** (same backtest, two versions): fixed vs fitted decay; guidance midpoint vs high end; with vs without outside inputs (BTS, NYC TLC, FRED); LLM proposer vs defaults (a switch, `proposer.method`). LLM vs rule-based extraction is scored on layer 1, not the backtest.

**What to publish:** a results table for the five layers, backtest chart by company, one full example note. Also state the limits: small sample, no paid data, NYC-only trips, possible leakage.

---

## 7. Pre-build checks and decisions (all resolved)

- [x] Each ticker, listing status, and that all eight file 10-K and 10-Q (`tenk companies verify`)
- [x] Fiscal year-ends (all December) and KPI definitions in the latest release (§1.5)
- [x] Earnings release dates per backtest quarter, and which companies give numeric guidance (TripAdvisor gives none: its guidance baseline is "n/a")
- [x] One company end to end on EDGAR: company-facts API, filing dates, rate limit and User-Agent
- [x] Each free dataset is available and permitted. Stooq, TSA and Google Trends dropped; Tiingo is internal use only (§1.4)
- [x] Defaults signed off in `research.yaml`: scenario weights 25/50/25, valuation weights 60/40, rating thresholds (Buy +15%, Sell -10%), the single ERP
- [x] Guidance anchor `mid`, though `high` scored better in the backtest (p = 0.011); `high` stays an ablation
- [x] Proposer `defaults` (LLM proposer off), since the LLM proposer did not beat it in the backtest; `llm` stays a switch and an ablation
