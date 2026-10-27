# Junior Sell-Side Equity Research Agent: Spec

A portfolio project: one agent that covers eight US travel and mobility companies using only free, public data. It answers questions about them, runs the analyst loop on demand (model, forecast, value), and writes a draft sell-side note. A human reviews every rating and price target. All output is for education and testing, not investment advice.

**Every run takes an as-of date.** The agent sees only data published on or before it. This is what makes backtesting possible (Section 6).

---

## 1. Inputs: what the system consumes

### 1.1 Companies

| Sector | Companies (ticker) |
| --- | --- |
| Travel | Booking (BKNG), Expedia (EXPE), Airbnb (ABNB), TripAdvisor (TRIP) |
| Mobility and delivery | Uber (UBER), Lyft (LYFT), DoorDash (DASH), Instacart (CART) |

All have a December fiscal year-end. Peers for comps are the other companies in the same sector. Instacart IPO'd in September 2023: it has no 10-Q or earnings release before Q3 2023, and its earlier periods come from its S-1.

### 1.2 Data sources

Window: **Q1 2023 through the latest quarter filed by the as-of date**. Today (2026-10-27) the data runs Q1 2023 to Q2 2026 (14 quarters); Q3 2026 results are not yet included.

| Source | What | Period | Used for |
| --- | --- | --- | --- |
| **10-K** (EDGAR) | Full text and financial statements | FY2023, FY2024, FY2025 (+ earlier years via XBRL) | Annual financials, risks, business description, Q4 derivation |
| **10-Q** (EDGAR) | Full text and financial statements | Q1 2023 to Q2 2026 | Quarterly financials, MD&A |
| **8-K Item 2.02, Ex. 99.1** | Earnings release and shareholder letter | Q1 2023 to Q2 2026 (14 per company) | KPIs, non-GAAP reconciliations, next-quarter guidance |
| **XBRL company facts** (EDGAR API) | Structured financial values, each with its filing date | All periods above | Core numbers, point-in-time history |
| **DEF 14A, Form 4** | Proxy, insider transactions | 2023 to as-of | Dilution, buybacks, SBC plans |
| **Daily prices** (Stooq) | Close and volume | 2023 to as-of | Market cap, EV, beta, backtest prices |
| **FRED** | 10-year Treasury, CPI, consumer sentiment, USD index, restaurant sales | 2023 to as-of | Discount rate, demand and FX inputs |
| **Damodaran** | Implied equity risk premium | Latest before as-of | DCF |
| **TSA, BTS** | Daily US air passengers, airline traffic | 2023 to as-of | Travel cross-check |
| **NYC TLC** | Monthly trip records with a license number per company (~2 months lag, NYC only) | 2023 to as-of | Uber vs Lyft trip cross-check |
| **Google Trends** | Search interest (weak, noisy) | 2023 to as-of | Optional early signal |

Each number carries its source document, period, filing date and retrieval date.

### 1.3 Not available (free-data limits)

| Missing | Handling |
| --- | --- |
| Consensus estimates (paid) | Benchmark against management guidance and baselines; optionally hand-record a dated figure |
| Call transcripts | Use releases, shareholder letters and 10-Q text |
| Paid alternative data (hotel occupancy, app usage, card spend) | TSA, BTS, NYC TLC as directional checks, with coverage limits stated |

### 1.4 Core KPIs extracted each quarter

| Company | KPIs |
| --- | --- |
| Booking | Room nights, gross bookings, revenue, take rate, ADR, adjusted EBITDA |
| Expedia | Gross bookings by segment, room nights, revenue, take rate, adjusted EBITDA |
| Airbnb | Nights and experiences booked, gross booking value, ADR, revenue, take rate, adjusted EBITDA, FCF |
| TripAdvisor | Revenue by segment (Tripadvisor, Viator, TheFork), Viator booking value, adjusted EBITDA |
| Uber | Gross bookings by segment (Mobility, Delivery, Freight), trips, MAPCs, revenue, take rate, adjusted EBITDA, FCF |
| Lyft | Gross bookings, rides, active riders, revenue, adjusted EBITDA, FCF |
| DoorDash | Total orders, marketplace GOV, net revenue margin, adjusted EBITDA |
| Instacart | GTV, orders, AOV, transaction and advertising revenue, adjusted EBITDA |

### 1.5 Collection rules

- The filing wins when sources disagree; log the gap.
- Restate history when a KPI definition or segment structure changes, and note it.
- Keep raw data separate from adjusted figures.
- Q4 = full year minus (Q1 + Q2 + Q3), reconciled to the 10-K.
- Send an identifying User-Agent to EDGAR and respect its rate limit (about 10 requests per second; verify).

---

## 2. Outputs: three product modes

| Mode | Input | Output | Runs |
| --- | --- | --- | --- |
| **Ask** | A question + as-of | Short cited answer from filed data | Seconds to minutes |
| **Research** | A question + as-of | Analytical answer from the model, forecast or valuation, with assumptions shown | Minutes |
| **Report** | Ticker + as-of + type (`update` or `initiation`) | A 10-section draft note, a model-change workbook (`.xlsx`) and an evidence and exception log | Target: under 30 min per company |

Ask and Research both answer questions. Ask retrieves what was reported; Research computes something new (an estimate, scenario or valuation) by running the model.

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

A report run hands the senior analyst **three artifacts**. The split lets the senior read the investment case without the audit trail, and still drill into any number.

| Artifact | Reader's question | Contents |
| --- | --- | --- |
| **Research report** (the note) | What happened, what do we change, what do you decide? | 10 sections. Sections 1–5 are the core automated deliverable. Sections 6–10 add valuation, risk, catalysts and review controls |
| **Model-change workbook** (`.xlsx`) | Exactly what moved in the model? | Old vs. new estimates, drivers, formula outputs, revision bridge, scenarios, valuation, integrity checks |
| **Evidence and exception log** | Where does this come from, and what is not settled? | One row per material figure or claim with its source, plus every discrepancy, data gap and failed check. Delivered as the `Log` tab of the workbook |

*Illustrative. Every figure below is invented to show the format; real numbers come from the model. The invented figures reconcile with each other, except that the FY2026E revenue ($51.0B) is below what Q2's run-rate implies.* The example workbook, [`EXAMPLE_UBER_Q2_2026_MODEL_CHANGE_WORKBOOK.xlsx`](EXAMPLE_UBER_Q2_2026_MODEL_CHANGE_WORKBOOK.xlsx), reproduces the figures in sections 2, 4, 6 and 7 of the note with live formulas.

**The research report**

```text
UBER TECHNOLOGIES (UBER)  ·  Earnings update  ·  Q2 2026 (Apr–Jun)
As of 2026-08-06  ·  Status: DRAFT for senior analyst review  ·  Prepared by: research agent
For education and testing. Not investment advice. No rating or PT is approved until the
responsible analyst confirms it.

1. EXECUTIVE SUMMARY
   Draft rating: Buy    Price: $80.00    12-month PT: $95.00    Return: +18.8%
   Market cap: $165B    EV: $168B        52-week: $60–$90       Next earnings: 2026-11-05 (est.)
   Rating / PT change: initial draft (no prior run)

   Takeaways
   - Beat: gross bookings, revenue, adjusted EBITDA and trips all exceeded our estimates;
     bookings beat the top of the guidance range.
   - Outlook: Q3 guidance implies bookings +6.5% and adj. EBITDA +7% on Q2. Our Q3 sits
     slightly above the midpoint because we assume the take rate holds at 26.8%.
   - Estimates: 2027E adj. EBITDA +3.2%, 2028E +3.6%, mostly Mobility growth and Delivery margin.
   - Valuation: 17.3x 2027E EV/EBITDA vs. a peer median of 19x; our 19x target needs justification.
   Thesis (hypothesis): Mobility growth plus Delivery margin gains compound faster than the
   price implies. We have not shown the market is wrong; no consensus is available.
   Decisions needed: items 1–6 in Section 10.

2. EARNINGS RESULTS
   2.1 Headline           Actual  Our est.  Var.   Guide mid  Var. vs mid  Y/Y    Consensus
   Gross bookings ($B)    48.1    47.5      +1.3%  47.0       +2.3%        +19%   n/a
   Revenue ($B)           12.9    12.6      +2.4%  n/a        n/a          +17%   n/a
   Adj. EBITDA ($B)       2.1     2.0       +5.0%  2.0        +5.0%        +31%   n/a
   Trips (B)              3.4     3.3       +3.0%  n/a        n/a          +16%   n/a
   Consensus: no free source (see log X-03). Variances use unrounded model values.

   2.2 Guidance           Q3 guidance       Midpoint   Our forecast   Var. vs mid   Flag
   Gross bookings ($B)    50.5–52.0         51.25      51.8           +1.1%         ok (<2%)
   Adj. EBITDA ($B)       2.2–2.3           2.25       2.28           +1.3%         ok (<5%)
   Prior-quarter guide was bookings 46.0–48.0 and EBITDA 1.9–2.1; both landed at or above the top.

   2.3 Quality of the quarter
   Conclusion: a high-quality beat. Growth is volume-led, cash backs the EBITDA, and nothing
   one-off is visible. Two items stay open.
                          Q2 2026   Q2 2025   Change
   Trips (B)              3.4       2.93      +16%
   Bookings per trip ($)  14.15     13.79     +2.6%
   Take rate              26.8%     27.2%     -40 bps
   Bookings mix ($B)      Mobility 24.5 (+17%) · Delivery 21.8 (+21%) · Freight 1.8 (+20%)
   Adj. EBITDA ($B)       Mobility 1.50 · Delivery 0.80 (3.7% of bookings, +40 bps y/y) ·
                          Freight 0.02 · Corporate -0.22
   Cash: operating cash flow 2.4, capex 0.1, FCF 2.3 (110% of adj. EBITDA); SBC 0.45.
   - Growth: about 16 pts from trips, 3 pts from price. Take rate fell 40 bps, so revenue
     grew slower than bookings.
   - Margin: gains come from Delivery segment margin (recurring operating leverage per the
     shareholder letter). We found no one-off item in the GAAP-to-adjusted reconciliation.
   - Open: (1) why the take rate fell (mix or incentives); the release does not say.
     (2) Delivery incentive spend is not disclosed, so margin durability is untested.

3. DRIVERS AND KEY DEBATES
   3.1 Driver              Latest evidence                          Model variable
   Mobility growth         Bookings +17% y/y, trips +16%            Mobility trips, bookings per trip
   Delivery profitability  3.7% of bookings, +40 bps y/y            Delivery EBITDA margin
   Take rate               26.8%, -40 bps y/y                       Take rate by segment
   Operating leverage      Revenue +17% vs. EBITDA +31%             Opex growth
   Autonomous vehicles     Partnerships named in 10-Q; terms not    Scenario only (see 3.3)
                           disclosed

   3.2 Can Mobility sustain high-teens growth?
   Bull: trips +16% with rising bookings per trip.  Bear: growth fades as comps get harder;
   our default fade (x0.90 per quarter) already takes it to about 13% by Q4 2027E.
   Hypothesis: 15% for 2027E. Sensitivity: each 1 pt of Mobility bookings growth moves
   2027E EBITDA by about $0.06B (0.6%).
   Trigger: Mobility bookings growth below 14% for two quarters challenges the thesis.
   Data gap: NYC TLC covers NYC only, so it is a directional check, not proof.

   3.3 Will autonomous vehicles pressure take rate?
   Bull: AVs widen the market and Uber keeps the demand layer.  Bear: AV operators keep more
   of each fare. Evidence is qualitative: partnership and risk-factor text only.
   Hypothesis: no effect on 2026–2027E; a long-term risk. Modeled as a take-rate scenario
   (-75 bps in Bear), not as a forecast. Trigger: a disclosed AV revenue-share term, or AV
   share of trips above a level the filings start to report.

4. ESTIMATE REVISIONS AND MODEL IMPACT
   4.1                        Old    New    Change   Source of change       Type          Confidence
   Q3 2026E adj. EBITDA ($B)  2.20   2.28   +3.6%    Guidance + assumption  Recurring     Medium
   2026E revenue ($B)         50.1   51.0   +1.8%    Reported + assumption  Mixed         Medium
   2026E adj. EBITDA ($B)     8.6    8.9    +3.5%    Reported + assumption  Mixed         Medium
   2027E adj. EBITDA ($B)     9.4    9.7    +3.2%    Analyst assumption     Recurring     Medium
   2028E adj. EBITDA ($B)     11.0   11.4   +3.6%    Analyst assumption     Recurring     Low

   4.2 Bridge ($B)            Q2 beat   Q3 Delivery   Q4 Delivery   Total
   2026E adj. EBITDA          +0.10     +0.08         +0.12         +0.30  (8.6 → 8.9)
                              Mobility growth   Delivery margin   Opex leverage   Total
   2027E adj. EBITDA          +0.15             +0.10             +0.05           +0.30  (9.4 → 9.7)
   Full driver bridge for all years: model-change workbook, tab "Bridge".

   4.3 Integrity checks (layer 2)
   [pass] Balance sheet balances        [pass] Quarters sum to years    [pass] Q4 ties to 10-K
   [pass] Take rate recomputes          [pass] No input dated after as-of
   [flag] Q2 adj. EBITDA 2.1 (release) vs. 2.08 (10-Q segment note): rounding; filing wins (X-01)
   Original model preserved; proposed changes are shown as a separate version.

5. FINANCIAL SUMMARY     2024A   2025A   2026E   2027E   2028E
   Revenue ($B)          44.0    49.0    51.0    57.0    62.5
   Growth                18%     11%     4%      12%     10%
   Gross bookings ($B)   ...
   Adj. EBITDA ($B)      6.5     8.0     8.9     9.7     11.4
   Adj. EBITDA margin    ...
   GAAP op. income ($B)  ...
   SBC ($B)              ...
   EPS ($)               ...
   FCF ($B)              ...
   Diluted shares (B)    ...
   Segment bookings and EBITDA, GAAP-to-adjusted bridge and quarterly detail: workbook.

6. VALUATION
   Method: 60% EV/EBITDA ($88) + 40% DCF ($106) = $95.
   Comps (2027E EV/EBITDA): LYFT 13x · CART 19x · DASH 20x · median 19x · UBER now 17.3x.
   Only three peers, so the range is thin (X-04).
   Target multiple 19x = peer median, and a premium to UBER's current 17.3x. Justification:
   faster growth than LYFT, wider margin gains than CART. Reviewer to confirm.
   EV to equity: EV $168B - net debt $3B = $165B market cap; 2.06B diluted shares.
   DCF: 10 years (3 modeled, 7 faded), WACC and terminal growth in the workbook; terminal value
   share of EV shown. Grid: price per share vs. WACC x terminal growth.
   Scenario   PT     Key assumptions                                   Weight
   Bear       $70    Take rate -75 bps vs. base; Mobility growth 12%; 16x   25%
   Base       $95    Table 4.1 estimates; 19x                           50%
   Bull       $120   Mobility growth 20%; Delivery margin +100 bps; 22x     25%
   Probability-weighted: $95 (0.25 x 70 + 0.5 x 95 + 0.25 x 120). Return +18.8% → Buy (≥ +15%).

7. RISKS AND SENSITIVITY
   Risk                    Model driver             2027E adj. EBITDA   PT impact    Evidence
   Take-rate compression   Revenue / bookings       -50 bps → -$0.7B    about -$7    Current: -40 bps y/y
   Slower Mobility growth  Mobility trips, price    -1 pt → -$0.06B     about -$0.6  Hypothetical
   Delivery margin         Delivery EBITDA margin   -50 bps → -$0.5B    about -$5    Hypothetical
   Regulation              Pricing, cost            Not quantified      n/a          New 10-Q risk factor
   AV competition          Long-term take rate      Scenario only       n/a          Qualitative only
   Probabilities beyond the 25/50/25 scenario weights are not assigned.

8. CATALYSTS
   2026-11-05  Q3 earnings (estimated from prior pattern; not yet announced)
               Watch bookings, Mobility growth, Delivery margin, take rate, Q4 guidance
   Investor day and regulatory decisions: listed only if a filing mentions them; none found.

9. SOURCES, ASSUMPTIONS, DISCLOSURES
   Data as of 2026-08-06: 10-Q (filed ...), 8-K Ex. 99.1 (filed ...), Stooq prices, FRED DGS10.
   Key assumptions (analyst-imposed): take rate 26.8% flat; growth fade 0.90; terminal growth 3%.
   Full register: workbook tab "Assumptions". Source for each figure: evidence log.
   Open issues: 5 (1 warning, 1 data gap, 3 information); see log X-01 to X-05.
   Rating definitions: Buy ≥ +15% 12-month return, Sell ≤ -10%, Hold between.
   Draft for education and testing, not investment advice.

10. SENIOR ANALYST REVIEW
   Decisions
   1. Approve or amend the reading of the quarter (Section 2.3).
   2. Approve or reject the estimate changes (Section 4.1).
   3. Resolve open issues X-01 to X-05.
   4. Approve the valuation method, 19x multiple and scenario weights.
   5. Confirm the rating and PT.
   6. Approve the note for distribution.
   Prepared by the agent: fact pack, surprise analysis, proposed estimates, integrity results,
   draft commentary, issue list.
   Needs analyst judgment: thesis, forecast assumptions, valuation method, rating, PT, publication.
   Status: PENDING senior review.
```

**The model-change workbook**

Example file: [`EXAMPLE_UBER_Q2_2026_MODEL_CHANGE_WORKBOOK.xlsx`](EXAMPLE_UBER_Q2_2026_MODEL_CHANGE_WORKBOOK.xlsx). Blue cells are inputs, black are formulas, green link to another tab; change an input and everything downstream recalculates. It is an excerpt: the Model tab shows Q2 KPIs, segments, guidance and an annual summary, not the full three statements, and the prior-version tab is replaced by typed-in old estimates.

| Tab | Contents |
| --- | --- |
| Summary | Old vs. new estimates, change in $ and %, source of change, type, confidence (old value is the denominator) |
| Bridge | Each revision split by driver and source type (reported, guidance, analyst assumption), tied back to Summary |
| Assumptions | Every assumption with value, basis (reported, derived or analyst-imposed), confidence, variables affected, last updated |
| Model | Three statements by quarter, formulas only; the prior version kept as a separate tab |
| Scenarios | Bear, base, bull, weights, probability-weighted value, one-at-a-time sensitivities |
| Valuation | Peers, EV-to-equity bridge, blended price target, rating |
| DCF | 10-year DCF, terminal value share of EV, WACC x terminal-growth grid |
| Checks | Layer 2 results, one row per check; any FAIL blocks the note |
| Log | Evidence and exception rows (below) |

**The evidence and exception log**

| Part | Fields |
| --- | --- |
| Evidence | ID, figure or claim, value, source document, period, filing date, page or table, retrieval date, type (reported, derived, analyst-imposed) |
| Exceptions | ID, severity (warning, data gap, information), description, treatment, status (open or resolved) |

Example exception rows:

| ID | Severity | Item | Treatment |
| --- | --- | --- | --- |
| X-01 | Warning | Q2 adj. EBITDA 2.1 (release) vs. 2.08 (10-Q) | Rounding; filing wins (§1.5) |
| X-02 | Data gap | AV economics not disclosed | Scenario only, not a forecast |
| X-03 | Information | No consensus source | Benchmark against guidance and baselines |
| X-04 | Information | Three comps only | Flag thin range |
| X-05 | Information | Next earnings date not announced | Mark "est." |

Note rules:
- Each section opens with its conclusion, then the evidence.
- Every number traces to the model or an evidence-log row.
- A gap says why: "n/a: not in free data" or "failed check: reason", never "pending".
- Never fill a gap with an estimate shown as fact; never resolve a conflict silently.
- Estimates read "2027E", actuals "2025A"; label periods once as "Q2 2026 (Apr–Jun)".
- `update` is 3–5 pages after each quarter; `initiation` adds a business overview, industry and competition, and a model appendix, with the same ten sections.
- Build sections 1–5 first; sections 6–10 follow once the model and extraction tests pass.

---

## 3. How it works

```text
Data pack  →  Model  →  Forecast  →  Valuation + rating  →  Note
(code+LLM)   (code)     (code)        (code)                 (LLM, number-traced)
```

Forecast numbers come from deterministic code. The LLM extracts KPIs, proposes assumptions with a rationale, and writes text.

### 3.1 Model (per company, quarterly, three statements)

Operating KPIs drive revenue, revenue drives costs and profit, profit drives cash flow and balance sheet.

```text
Gross bookings = volume x price     (users x orders x AOV; room nights x ADR; trips x fare)
Revenue        = Gross bookings x take rate
EPS            = Net income / diluted shares
FCF            = Operating cash flow - capex
Assets         = Liabilities + equity       (must balance, or the model fails)
```

- **Travel:** customers prepay, so model deferred merchant bookings; revenue lags bookings.
- **Uber:** model gross bookings and take rate per segment (Mobility, Delivery, Freight) and sum.
- **TripAdvisor:** Viator uses the bookings equations; the Tripadvisor brand is traffic x revenue per visitor.
- **Instacart:** transaction fee and advertising each as a share of GTV.
- Take rate is always computed from reported revenue and reported gross bookings.
- Forecast columns are formulas only; assumptions sit in one input block; quarters sum to years; SBC is shown separately.

### 3.2 Forecast

| Rule | Detail |
| --- | --- |
| Per driver, not per total | `Driver(q) = Driver(q-4) x (1 + g)`, so seasonality is automatic |
| Growth fade | `g(t+1) = g(t) x d`, default `d = 0.90` |
| Anchor | Current quarter starts at the guidance midpoint; later quarters use the 3-year average seasonal share |
| Scenarios | Bear / base / bull (25 / 50 / 25%) by varying the 2–3 key drivers |
| Variance flag | Versus guidance midpoint: over 2% on revenue or 5% on adjusted EBITDA needs a written reason |
| Outside inputs | TSA, BTS, NYC TLC, FRED, Google Trends |
| Horizon | Quarterly through Q4 2027E; annual 2026E to 2028E |

### 3.3 Valuation and rating

| Item | Default |
| --- | --- |
| Multiple method | EV / adjusted EBITDA on 2027E (also shown less SBC); P/E and FCF yield secondary. Computed by the agent from filings and prices |
| DCF | 10 years (3 modeled, 7 faded), SBC treated as a cost, terminal growth 3.0%, beta from 5-year regression, one ERP for all eight |
| Price target | `0.6 x multiple PT + 0.4 x DCF PT`; bear, base, bull and probability-weighted values also shown |
| Rating (absolute) | Buy: 12-month return of +15% or more. Sell: -10% or less. Hold: between |
| Rating (relative) | Rank the eight by expected return: top third Buy, bottom third Sell |

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
| 1. Extraction | ~384 hand-labeled values (12 fields x 4 quarters x 8 companies) | 98% exact match; 100% on revenue, EPS and main KPI |
| 2. Model integrity | Automated checks: balance sheet balances, quarters sum to years, Q4 matches 10-K, take rate recomputes, no input dated after as-of | 100% pass; any failure blocks forecasting |
| 3. Forecast accuracy | Walk-forward backtest (Section 6) | Lower MAPE than seasonal-naive on revenue and main KPI; bias near zero |
| 4. Valuation | Re-run with identical inputs; sensitivity checks | Identical output; every target multiple inside the peer range or flagged |
| 5. Note quality | Number trace, all 10 sections present, rubric review, seeded errors | 100% of figures traced; rubric average of at least 4 of 5 |

Ask and Research modes also get a gold question set (about 80 questions across the eight companies), with pass bars set before the first run.

## 6. Backtest

- **Point-in-time rule:** at each as-of date, use only filings EDGAR had accepted before it.
- **Walk-forward:** for each quarter from the Q4 2024 releases onward, set the as-of date just after the earnings release, run the full pipeline, forecast next quarter, move forward one quarter. About 6 forecasts per company (about 4 for Instacart), roughly 45 per metric.
- **Metrics:** MAPE, bias (positive means forecasts too high), and guidance call (does revenue land above or below the guidance midpoint).
- **Baselines:** seasonal naive (same quarter last year x latest YoY growth), guidance midpoint, and a plain LLM with no tools.
- **Held-out set:** report quarters released after the LLM's training cutoff separately.
- **Reporting:** medians and ranges by company, plus a paired Wilcoxon test.

**Ablations** (same backtest, two versions): LLM vs rule-based extraction; fixed vs fitted decay; guidance midpoint vs high end; with vs without free cross-checks.

**What to publish:** a results table for the five layers, backtest chart by company, one full example note. Also state the limits: small sample, no paid data, NYC-only trips, possible leakage.

---

## 7. Build order

Each step passes its tests before the next.

1. Extraction from filings and releases (layer 1)
2. Model and integrity tests (layer 2)
3. Forecasting and backtest (layer 3)
4. Valuation, rating, note (layers 4, 5)
5. Results table and write-up

Start with a pilot of one company per sector (BKNG and UBER) before scaling to eight.

## 8. To verify before building

- [ ] Each ticker, listing status, and that all eight file 10-K and 10-Q
- [ ] Fiscal year-ends and exact KPI definitions in the latest 10-K and release
- [ ] Earnings release dates per backtest quarter, and which companies give numeric guidance (TripAdvisor especially)
- [ ] One company end to end on EDGAR: company-facts API, filing dates, current rate and User-Agent rules
- [ ] Each free dataset is available and permitted for this use (TSA, BTS, NYC TLC, FRED, Stooq)
- [ ] Defaults: scenario weights, 60/40 valuation weights, rating thresholds, pass bars, the single ERP
