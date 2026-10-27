# V4 technical design: from a 10-K Q&A agent to a junior sell-side analyst

**Status:** Draft, nothing built. Settled items move to [DECISIONS.md](DECISIONS.md) as they are built.
**Updated:** 2026-10-27 · **Spec:** [JUNIOR_SELL_SIDE_EQUITY_RESEARCH_AGENT_SPEC.md](JUNIOR_SELL_SIDE_EQUITY_RESEARCH_AGENT_SPEC.md). "Spec §n" is the spec's section; a bare "§n" is a section of this doc.
**Not to be confused with** the retired V4 in `archive/` (D-034).

The spec says *what* to build; this doc says *how*, and how it plugs into V0–V3.

---

## 1. Scope: what changes from V3

| | V0–V3 | V4 |
|---|---|---|
| **Job** | Cited answers about 10-Ks | Three modes: **Ask**, **Research**, **Report** (§2) |
| **Companies** | 8 megacaps | BKNG, EXPE, ABNB, TRIP · UBER, LYFT, DASH, CART |
| **Sources** | 10-K HTML, XBRL company facts, EDGAR metadata | + 10-Q, 8-K 2.02 Ex. 99.1, DEF 14A, Form 4, Stooq, FRED, Damodaran, TSA, BTS, NYC TLC, Google Trends |
| **Time** | FY2023–2025, static | Q1 2023 → latest filed; **every run has an as-of date** |
| **Numbers** | Reported, or calculator over reported | + deterministic three-statement model, forecasts, valuation |
| **LLM role** | Finds, calculates, cites | Extracts KPIs, proposes assumptions, writes prose. **Never outputs a forecast number** |
| **D-001** | No prices, forecasts, advice | Allowed; ratings are drafts labeled "education and testing, not investment advice". A new decision entry supersedes D-001 in V4.0a |

**Reused as is or extended**

| Piece | Where | V4 use |
|---|---|---|
| EDGAR client, SEC cache, offline mode | `acquisition/edgar.py`, `agent/sec.py` | Any form; same cache-then-offline pattern for every new source |
| Parse → parent-child chunks → hybrid search | `preprocess/`, `chunking/`, `retrieval/` | 10-Qs and releases; form-aware Items; as-of filter |
| Claim check | `generation/verify.py` | Becomes a resolver over all source kinds (§10) |
| Agent loop, budget, repair turn, "tools never raise" | `agent/loop.py`, `agent/tools.py` | Ask and Research as is; also the bounded sub-agents in Report mode |
| `Model.complete` (vLLM / Claude) | `models.py` | Unchanged |
| Gold sets, judge, reports | `evaluation/` | New gold sets and metrics (MAPE, bias, guidance call) |

The old gold sets don't transfer (different companies); the pattern does.

---

## 2. Architecture

```text
 EDGAR, Stooq, FRED, Damodaran,   build (offline):  fetch → raw cache → chunks + vectors (text)
 TSA, BTS, TLC, Trends                                              → facts, quarters (XBRL, inline XBRL)
                                                                    → KPIs, guidance (LLM extraction)
                                                                    → prices, macro, cross-checks
              one SQLite file; every row carries the date it became public
                                  │   AsOf(date) filter inside Store (§3)
        ┌─────────────────────────┼──────────────────────────┐
       Ask                    Research                    Report
  agent + read tools     agent + read + model tools    fixed step pipeline; LLM only in bounded steps
```

| Mode | Control | New numbers? | Cites |
|---|---|---|---|
| **Ask** | Agent picks tools (V3) | Calculator over reported figures only | chunks, facts, KPIs, prices, macro, **saved** report values |
| **Research** | Agent picks tools | Yes, only by calling model tools (code) | the above + `model:` refs to its own run |
| **Report** | Code runs the steps; LLM calls are leaf tasks | Only the model engine | everything, via the evidence log |

Ask vs Research is a tool-set difference (`Toolbox(mode=...)`), not a second agent. Report is not an agent loop (§9).

---

## 3. The as-of rule

The central invariant: every leak is a backtest bug.

**`AsOf(D)` = the world at the market open of D (US/Eastern).**

| Data | Visible when | Column |
|---|---|---|
| Filings (any form), their chunks, facts, KPIs | `filing_date < D` | `filed` |
| Stooq price | `date < D` (as-of 2026-08-06 uses the 08-05 close, as in the spec's example) | `date` |
| FRED | observation `date < D` and, for revised series (CPI, sentiment, food-service sales), ALFRED `realtime_start < D`; DGS10 and the USD index are not revised | `date`, `realtime_start` |
| Damodaran ERP | latest file published `< D` | `published` |
| TSA, BTS, TLC, Trends | publish date `< D` (TLC lags ~2 months, BTS ~3): store publish date, not data month | `published` |
| Our reports, model versions | `as_of < D` | `as_of` |

The spec's header says "on or before", §6 says "accepted before". We use **strictly before**: it matches the example and is safest for backtests. It is one function, so flipping it is a one-line change.

**Enforcement**
- `asof.py` holds `AsOf` with `.sees(d)`, `.sql(alias, column)`, `.latest(rows)`. Nothing else compares dates.
- Every `Store` read of dated data **requires** `asof` (no default; missing = `TypeError`). `Store.search(..., asof=)` adds `filed < ?` to `_filters`.
- `Toolbox(asof=...)` is built per run. No `ToolSpec` has an `as_of` property, so the model cannot widen its view.
- **Restatements:** `.latest()` returns, per (concept, period), the value from the newest filing visible at D. An as-of before a restatement sees the original.
- Layer-2 check `no_future_input` (§5.4) asserts it on every run.
- LLM *memory* leakage is separate (§12).

---

## 4. Data layer

### 4.1 Companies move to config

`corpus.py`'s hard-coded tuple becomes `companies.yaml` (pinned per experiment, like `config.yaml`). `corpus.py` keeps its API (`COMPANIES`, `company_by_ticker`, `resolve_company`) but reads the file.

```yaml
UBER:
  name: Uber Technologies, Inc.
  cik: null              # filled by `tenk companies verify` from SEC company_tickers.json, never typed
  aliases: [Uber, Uber Eats, Uber Freight]
  sector: mobility       # peers = same sector
  ipo: null              # CART: 2023-09-19, earlier periods from the S-1
  template: segments     # §5.3
  main_kpi: gross_bookings
  segments: [mobility, delivery, freight]
  scenario_drivers: [gb_growth.mobility, gb_growth.delivery, take_rate.mobility]
```

`tenk companies verify` covers spec §8 items 1–2: CIKs, listing status, December FYE (submissions `fiscalYearEnd`), 10-K/10-Q counts in the window.

### 4.2 Acquisition

| Source | Module | How | Raw cache |
|---|---|---|---|
| 10-K, 10-Q | `acquisition/edgar.py` | One filing per (form, period); amendments ignored. `(fy, fp)` per accession from any company-facts row with that `accn` (generalizes `fiscal_years_by_accession`; keeps D-004) | `raw/filings/<T>/<period>-<form>/` |
| 8-K releases | same | Submissions rows with `form=="8-K"` and `"2.02" in items`; the filing index `<acc>-index.htm` gives every `EX-99.*` (99.1 release; 99.2 letter where used). Period from the release text, cross-checked against the next 10-Q; mismatch = fetch error | `.../<period>-ER/` |
| S-1 (CART) | same | 2023 S-1/424B4; pre-IPO quarters, extracted like a release | `raw/filings/CART/S1/` |
| DEF 14A | same | Latest per year; chunked text only | |
| Form 4 | `acquisition/insiders.py` | XML → `insider_tx` (V4.7; dilution context only) | |
| XBRL company facts | `acquisition/edgar.py` | Unchanged; refreshed each fetch, facts keep their `filed` date | `raw/edgar/<T>/companyfacts.json` |
| Prices | `acquisition/prices.py` | Stooq CSV per ticker + S&P 500 (for beta); split-adjusted | `raw/prices/` |
| Macro, ERP | `acquisition/macro.py` | ALFRED (`FRED_API_KEY`, free): DGS10, CPIAUCSL, UMCSENT, DTWEXBGS, RSFSDP; Damodaran monthly implied ERP | `raw/macro/` |
| Cross-checks | `acquisition/crosscheck.py` | TSA, BTS T-100, NYC TLC by license (Uber/Lyft codes to verify), Trends. TLC Parquet is large: aggregate to trips per month per license at fetch (V4.7) | `raw/crosscheck/` |

Every raw file gets `retrieved_at` in `raw/manifest.json` (spec §1.2).

### 4.3 Store changes (same SQLite file, D-005)

The corpus is rebuilt, so `load()` creates the new schema; `MIGRATIONS` is not extended.

```sql
filings(accession_no PK, ticker, form, fiscal_year, fiscal_period, period_end_date,
        filing_date, accepted_at, source_url, retrieved_at)   -- drops UNIQUE(ticker, fiscal_year)
chunks, children  += form, fiscal_period, filed               -- filed = filing_date, for the as-of filter
INDEX chunks_filter (ticker, filed, form, fiscal_year, item)
```

**Chunk ids** keep the V0 shape with a period key: `<TICKER>-<period>[-<doc>]-<item>-<nnn>`: `UBER-FY2025-7-003` (10-K, unchanged) · `UBER-2026Q2-I.2-014` (10-Q Part I Item 2) · `UBER-2026Q2-ER-EX99-005` (release).

| New table | Holds | As-of column | Filled by |
|---|---|---|---|
| `facts` | ticker, taxonomy, concept, dims (JSON; `{}` = consolidated), period start/end, value, unit, accession, form, fy, fp | `filed` | `facts/xbrl.py`, `facts/ixbrl.py` |
| `quarters` | metric, fy, quarter, value, unit, derivation (`direct`/`ytd_diff`/`fy_minus_9m`), input fact ids | `filed` (max of inputs) | `facts/quarters.py` |
| `kpis` | metric, segment, period, value, unit, `definition_version`, source chunk, quote, method (`llm`/`rules`) | `filed` | `facts/extract.py` |
| `guidance` | metric, target period, low, high, basis (`level`/`growth_yoy`/`margin`), source chunk, quote, issued-in | `filed` | `facts/extract.py` |
| `prices`, `macro`, `crosscheck` | per source (§4.2) | `date` / `realtime_start` / `published` | acquisition |
| `model_versions` | id, ticker, as_of, parent_id, inputs_hash, status (`draft`/`reviewed`), path | `as_of` | `research/pipeline.py` |
| `estimates` | version, metric, period, scenario, value (lets Ask cite "our 2027E" by SQL) | via version | pipeline |
| `exceptions` | version, `X-` id, severity, item, treatment, status | via version | every step |

### 4.4 Text pipeline over new forms

V0 assumes a 10-K (`blocks.py` asserts `DocumentType == "10-K"`; `validation.ITEMS` is the 10-K order; V6 puts statements in Item 8).

- **`preprocess/forms.py` (new):** a `FormSpec` per form (Items in order, required Items, where statements live, key format). Validator rules V2, V3, V6 read it.
  - **10-K:** unchanged.
  - **10-Q:** Items repeat per Part, so keys are `I.1`, `I.2`, …, `II.1A`; `item_heading` labels gain a `part` field; statements live in `I.1`.
  - **Release (EX-99):** no Items. Rule path only: blocks → `rule_based` chunker with one pseudo-Item `EX99`, tables whole (D-003). The LLM structure agent is added only if the V4.0b pilot shows bad headings.
- **`retrieval/index.py`:** `ITEM_NAMES` per form; `chunk_header` stops hard-coding "10-K" ("Uber Q2 2026 10-Q, Part I Item 2 MD&A").
- **`retrieval/decompose.py`:** slots become (ticker, filing); "Q2 2026" resolves to period keys; `FISCAL_YEARS` goes. No period named = latest filing of each form visible at as-of.

### 4.5 Structured facts

Three XBRL facts shape the design (verified in V4.0a):

1. **Company facts has no dimensional facts**, so segment revenue, segment adjusted EBITDA and geography are missing. They are in the filing's inline XBRL (`ix:nonFraction` whose context carries `xbrldi:explicitMember`). `facts/ixbrl.py` parses the HTML we already download; `blocks.py` already unwraps iXBRL.
2. **10-Q cash flows are year-to-date.** `facts/quarters.py` builds quarters by duration length: ~90 days direct; ~180 / ~270 days by difference (Q2 = H1 − Q1, Q3 = 9M − H1); Q4 = FY − 9M (spec §1.5). Balance-sheet items are instants and need no derivation.
3. **Operating KPIs are mostly untagged** (gross bookings, trips, room nights, GOV, GTV, MAPCs): they come from release text (§4.6).

**Citable ids.** `xbrl:<T>:<tax>:<concept>:<accession>:<end>` is ambiguous for 10-Qs (3-month and 6-month facts share an end date). Part 6 becomes `<start>/<end>` for durations and stays `<end>` for instants: still six colon-parts, one change in `SecData.xbrl_fact`. Dimensional facts get `ixbrl:<accession>:<fact id>`.

### 4.6 KPI and guidance extraction (layer 1)

**Registry** `facts/kpis.yaml`: per company, each KPI's key, unit, segments, label patterns, definition version. Seeded from spec §1.4, checked against the latest 10-K and release.

```yaml
UBER:
  gross_bookings: {unit: USD millions, segments: [total, mobility, delivery, freight], labels: ["Gross Bookings"]}
  trips:          {unit: count millions, labels: ["Trips"]}
```

**Extractor** `facts/extract.py`, one schema-constrained LLM call per release (10-Q MD&A as fallback):
1. Input: release chunks (tables first) + that company's registry entries.
2. Output: KPI rows `{metric, segment, period, value, unit, chunk_id, quote}` and guidance rows `{metric, target_period, low, high, basis, chunk_id, quote}`.
3. **Code validates:** the quote occurs verbatim in the chunk, and `verify.find_value` finds the value in the quote (unit-aware). Failure = one repair turn, then an exception row (data gap); never a stored value.
4. **Reconciliation:** where a KPI also exists as a fact (revenue, iXBRL segment revenue), the filing wins (spec §1.5) and the gap is an exception: `information` under the stated rounding, `warning` above it.
5. **Restatements:** a recast release yields rows for prior periods with a new `definition_version`; `quarters` and the model read the newest, the log keeps the old.

Guidance comes in dollars, growth rates, margins, or not at all (TRIP). `basis` keeps the original form; `forecast.anchor()` converts growth to a level via the year-ago actual. No numeric guidance = guidance baseline "n/a: no numeric guidance" (§13).

---

## 5. The model engine

### 5.1 One definition, two evaluators

The spec wants a code model (determinism, tests) **and** a workbook with live forecast formulas. Two implementations would drift, so both come from one definition:

```text
companies.yaml + templates/*.yaml ─► ModelGraph (nodes × periods)
                                       ├─ evaluate()  Python  → model.json   (source of truth)
                                       └─ to_excel()          → Model tab with formulas
    parity test: LibreOffice recalculates the xlsx; every formula cell equals Python's value
```

**Node DSL** (`research/model/dsl.py`), parsed with Python `ast`:

| Construct | Example | Excel |
|---|---|---|
| Arithmetic | `gross_bookings * take_rate` | `=C12*C30` |
| Quarter lag | `trips[-4] * (1 + trips_g)` | cell 4 columns left |
| Fiscal-year sum | `fy(revenue)` | `=SUM(C10:F10)` |
| Functions | `min`, `max`, `if_(c, a, b)` | `MIN`, `MAX`, `IF` |
| Assumption | `@fade` | link to Assumptions tab |

Node kinds follow the spec's colors: `input` (history, blue, with provenance), `assumption` (blue, only in the Assumptions block), `formula` (black). History = inputs; forecast columns = formulas only.

### 5.2 Statements (condensed)

Full GAAP detail stays in the data pack; a fixed condensed layout lets one engine serve all eight.

| Statement | Lines |
|---|---|
| Operating build | Per template: volume, price, gross bookings, take rate, revenue by segment |
| Income | Revenue · adj. EBITDA (margin on GB or revenue, by segment where reported) · SBC · D&A · other adjustments (0) · **GAAP operating income** = adj. EBITDA − SBC − D&A − other · interest and other (non-operating gains 0) · tax (trailing effective rate, bounded 10–30%) · NI · diluted shares (trailing-4Q average quarterly change) · EPS |
| Cash flow | NI + D&A + SBC + Δ working capital = OCF · capex (% revenue) · **FCF = OCF − capex** · buybacks (trailing-4Q $) · debt (flat unless 10-K maturities) |
| Balance (~15 lines) | cash (plug) · ST investments · receivables · other current · PP&E · goodwill + intangibles · other · payables · accrued · deferred revenue / merchant bookings · debt · leases · other · equity |

Working-capital lines hold their same-quarter-last-year ratio to GB or revenue, so travel prepayment seasonality carries over. Equity rolls with NI + SBC − buybacks. History must balance from XBRL (the "other" lines absorb the remainder); the forecast balances by construction.

### 5.3 Templates (YAML node definitions, data not code)

| Template | Companies | Revenue build |
|---|---|---|
| `marketplace` | BKNG, EXPE, ABNB | GB = room nights (ABNB: nights and experiences) × ADR; revenue = GB × take rate; deferred merchant bookings follow GB |
| `segments` | UBER | Per segment (Mobility, Delivery, Freight): GB (trips × GB per trip where disclosed) × take rate; adj. EBITDA = margin × GB; corporate cost % revenue; totals are sums |
| `orders` | DASH, LYFT, CART | DASH: orders × AOV = GOV; revenue = GOV × net revenue margin. LYFT: rides × GB per ride; revenue = GB × take rate. CART: orders × AOV = GTV; transaction and ads revenue each as % GTV |
| `trip` | TRIP | Viator: booking value × take rate. Tripadvisor brand: traffic × revenue per visitor if disclosed, else revenue growth. TheFork: revenue growth |

If a volume KPI isn't disclosed, the template falls back to a growth driver one level up and logs an exception.

### 5.4 Layer-2 integrity checks (`research/model/checks.py`)

Run after building the model and again after forecasting. **Any FAIL stops the run** (spec §5).

| Check | Rule |
|---|---|
| `balance` | assets = liabilities + equity, every period, within $1M |
| `quarters_sum` | Q1–Q4 = FY for every flow line |
| `q4_ties` | derived Q4 + 9M = 10-K FY, within reported precision |
| `take_rate` | recomputes from reported revenue / reported GB |
| `segments_sum` | segment GB and EBITDA sum to reported totals |
| `no_future_input` | every input has `filed < as_of` |
| `formula_only` | forecast cells are formulas |
| `wacc_gt_g` · `weights` · `bridge_ties` | WACC > terminal growth · scenario weights sum to 1 · revision bridge sums to Summary deltas |

---

## 6. Forecast, valuation, rating

All knobs live in `research.yaml` (D-019: pinned per experiment, none hard-coded).

### 6.1 Forecast (`research/forecast.py`)

Per driver, never per total:

```text
Driver(q) = Driver(q-4) × (1 + g(q))      seasonality comes from q-4
g(q+1)    = g(q) × d                       d = 0.90 per quarter (fixed; "fitted" is an ablation)
g(first)  = y/y growth implied by the guidance midpoint (current quarter), else last reported y/y
```

- **Anchor:** guided metrics start at the midpoint (`guidance_point: mid`; `high` is an ablation). Drivers are solved so the guided total is hit: GB growth from a GB guide, then margin from an EBITDA guide.
- **Seasonal share** (3-year mean share of each quarter) spreads **full-year-only guidance** into quarters and flags a quarter whose share drifts over 2 points. It does not replace the q-4 rule.
- **Variance flag:** vs guidance midpoint, over 2% (revenue) or 5% (adj. EBITDA) needs a written reason (from the proposer's rationale); none = exception.
- **Horizon:** quarterly to Q4 2027E; annual 2026E–2028E; DCF years 4–10 faded.
- **Scenarios:** bear/base/bull (25/50/25) vary the 2–3 `scenario_drivers`. Default shock ±1σ of the driver's y/y change over history; the proposer may move it within [0.5σ, 2σ].
- **Outside data** (TSA, TLC, FRED) is context for the proposer and note; as a forecast input it is the "with vs without cross-checks" ablation.

### 6.2 Valuation (`research/valuation.py`)

| Item | Rule |
|---|---|
| Market cap, EV | Last close before as-of × latest diluted weighted-average shares; EV = market cap + debt − cash and ST investments. Leases, equity stakes (Uber's) and customer funds not counted, listed as exceptions |
| Multiple PT | Target multiple × 2027E adj. EBITDA → EV − net debt → per share. Target = peer median (same sector, our own estimates at the same as-of). Also shown: less SBC, P/E, FCF yield |
| DCF | UFCF = adj. EBITDA − SBC − cash tax on EBIT − capex − ΔNWC. Years 1–3 from the model; 4–10 grow at g×d per year; Gordon terminal at 3.0%. Ke = DGS10 + β × ERP (β: 5-year monthly vs S&P 500; one ERP for all eight). WACC = Ke if debt / (debt + market cap) < 10%, else weighted with Kd = interest / average debt. Grid: price vs WACC ±1 pt × g 2.5–3.5%; terminal-value share of EV shown |
| Price target | 0.6 × multiple PT + 0.4 × DCF PT, per scenario; probability-weighted 25/50/25. No 12-month roll-forward |
| Scenario multiples | Base = peer median; bear/bull ±15% (inferred from the example: 19x → 16x / 22x); outside the peer range = flagged |
| Rating, absolute | Return = PT / price − 1: Buy ≥ +15%, Sell ≤ −10%, else Hold |
| Rating, relative | `--all` ranks the eight: top third Buy, bottom third Sell. A single run shows "n/a (run --all)" |

**Peers need their own models at the same as-of.** S6 (§9) builds each peer's data pack, model and forecast with defaults (no LLM), cached per (ticker, as_of, inputs hash), so `--all` builds each model once.

---

## 7. Tools

### 7.1 Catalog

All tools are bound to the run's `AsOf`, never raise, and return citable ids. V3 had 4; V4 has 13, grouped so no single context gets all of them.

| Group | Tool | Returns | Ask | Research | Report sub-agents |
|---|---|---|:-:|:-:|---|
| text | `search_filings` (query, companies, periods, forms, k) | chunks with id, form, period, filed | ✓ | ✓ | events, debates |
| facts | `financials` (company, metric or concept, periods) | values from `quarters`/`facts` with id and derivation | ✓ | ✓ | proposer |
| | `company_kpis` | KPI rows with source chunk ids | ✓ | ✓ | proposer, debates |
| | `guidance` | low/high/basis, issued-in, prior guide vs actual | ✓ | ✓ | proposer |
| | `edgar_filings` | filing metadata (as V3), as-of filtered | ✓ | ✓ | events |
| market | `market_data` | prices, market cap, EV, macro, ERP | ✓ | ✓ | proposer |
| | `crosscheck` | TSA/BTS/TLC/Trends aggregates with coverage notes | ✓ | ✓ | proposer, debates |
| calc | `calculator` | as V3, reported figures only | ✓ | ✓ | |
| reports | `research_report` (company, as_of ≤ run's, section or metric) | saved estimates, PT, rating, status (`draft`/`reviewed`), ids | ✓ | ✓ | |
| model | `model_view` | history + forecast lines, ratios, seasonal shares, assumption register | | ✓ | proposer |
| | `run_scenario` (overrides `{driver: value or delta}`) | changed lines, PT, rating vs base; a `model:` id | | ✓ | |
| | `compare_peers` (metric, companies) | table across the eight at this as-of | | ✓ | |
| | `variance_review` (company, period) | prior estimate vs actual by driver, with the bridge | | ✓ | |

`financials` replaces V3's `xbrl_facts`: any period, as-of filtered, metric names instead of concept lookup (`concept` still accepted).

### 7.2 Rules for the new tools

- **Small results:** a compact table plus ids; detail by `detail=true` or by id.
- **Scratch runs are not versions:** `run_scenario` writes a session-local run and returns `model:<T>:<as_of>:scratch-<n>:<line>.<period>`; only Report creates `model_versions`.
- **Validate first:** overrides are checked against the driver list and bounds; an unknown driver returns the valid ones.
- **Measure selection:** gold questions record `expected_tools`; reports give tool-choice precision/recall per mode. If accuracy drops as tools grow, add a routing step (question class → tool groups), tested like D-037.
- **Prompts:** `agent.md` loses the "decline forecasts" lines and gains `{as_of}`, an as-of-filtered coverage table and per-mode rules (Ask: estimates only via `research_report`; Research: every computed number from a model tool, assumptions listed). New prompts with schemas: `extract.md`, `propose.md`, `events.md`, `debates.md`, `section.md`.

### 7.3 The assumption proposer: the LLM's only influence on numbers

A bounded sub-agent per company per run. For each key driver it returns:

```json
{"driver": "take_rate.mobility", "value": 0.268, "basis": "analyst-imposed", "confidence": "medium",
 "rationale": "Held flat: Q2 fell 40 bps y/y on mix; the release gives no reason to extrapolate.",
 "evidence": ["UBER-2026Q2-ER-EX99-005", "kpi:UBER:take_rate.mobility:2026Q2"]}
```

Code bounds every driver (take rate within the 8-quarter min/max ± 50 bps; `d` in [0.75, 1.0]; growth anchored to guidance where it exists). Out-of-bounds = clipped and logged. Evidence ids must resolve (§10). `research.proposer: llm | defaults` is the ablation switch.

---

## 8. Context management

Qwen3.6-27B serves a 32k window (I-003). One report touches 14 quarters, several documents and ~9 tool-using steps. The approach: **artifacts, not transcripts.**

| Principle | Mechanism |
|---|---|
| State lives in files | Each step writes a typed, versioned artifact (`datapack.json`, `model.json`, `assumptions.json`, …); the next step reads it, not the previous chat |
| Purpose-built views | `research/views.py` renders a token-budgeted view per LLM call: the proposer sees a driver table (12 quarters × drivers, ~1.5k tokens), not the data pack |
| Detail by id | Views carry ids; tools fetch the chunk, fact or KPI behind one. Nothing is preloaded "just in case" |
| Bounded sub-agents | Each agentic step is a fresh `Agent` with its own tool groups, `max_calls`, `max_context`; it returns JSON and its transcript goes to the trace only |
| Numbers never pass through prose | The note writer gets pre-formatted facts with ids (§10.2) and never computes |
| Budgets are config | `research.budgets.<step>: {max_calls, max_context, max_tokens}`; the trace records tokens per step |

**Budgets (32k window)**

| Call | Input target | Contents |
|---|---|---|
| KPI extraction, per release | ≤ 12k | release tables + one company's registry |
| Event log, per quarter | ≤ 16k, ≤ 8 calls | prior event summary (≤ 1k) + search results |
| Proposer, per company | ≤ 16k, ≤ 10 calls | driver view (≤ 2k) + guidance + event summary + tool results |
| Debate, per scenario driver | ≤ 16k, ≤ 8 calls | driver evidence + searches |
| Section writer, per section | ≤ 8k | that section's fact list, tables as text, relevant events/debates |
| Ask / Research question | 20k (`agent.max_context`) | as V3 |

Over budget, `views.py` drops the oldest quarters first, then low-priority columns, and says so in the view header.

**Memory across runs** (the analyst who remembers last quarter), all as-of safe: a run reads only versions with `as_of < D`.
- **Prior model version** (`parent_id`): old estimates, assumptions and rationale → note section 4, Summary and Bridge tabs.
- **Rolling event log** `events.json` per company: each run appends ≤ 300 tokens per event (segment or definition changes, M&A, buybacks, guidance habits); the next run reads these, not old documents.
- **Open exceptions** from the parent version are re-checked and resolved or carried forward.

In a backtest each step's "memory" is what that past run wrote.

---

## 9. Long-horizon tasks: Report mode as a resumable pipeline

A report is ~20–40 LLM calls and minutes of compute per company; `--all` is eight plus ranking; a backtest is ~6 as-ofs × 8 companies. Free-roaming loops don't survive that, so Report mode is a **step graph with checkpoints**, and agent behavior is confined to leaf steps.

```text
S1  datapack  code       facts, quarters, KPIs, guidance, prices, macro, filings        → datapack.json
S2  events    sub-agent  what changed this quarter (search, KPIs)                        → events.json
S3  model     code       history + layer-2 checks (FAIL stops)                           → model.json, checks.json
S4  propose   sub-agent  bounded assumptions with rationale (§7.3)                       → assumptions.json
S5  forecast  code       drivers, anchor, scenarios, layer-2 checks again                → estimates.json
S6  peers     code       S1, S3, S5 per peer, defaults, cached                            → peers/<T>/
S7  value     code       comps, DCF, grid, PTs, ratings, sensitivities                   → valuation.json
S8  revise    code       vs parent version: Summary, bridge, types, confidence           → revisions.json
S9  debates   sub-agents one per scenario driver: bull, bear, hypothesis, trigger, gap   → debates.json
S10 write     LLM        ten sections from facts + views; number trace; one repair       → note.json
S11 export    code       note.md/.pdf, workbook.xlsx (+ Log), parity check               → outputs
```

**Runner (`research/pipeline.py`)**
- **Checkpoints:** each step writes its artifact and `step.json` (inputs hash, status, seconds, tokens). A step whose inputs hash matches is skipped, so `--resume` continues after a crash (I-001) and `--from S7` reruns from valuation.
- **Inputs hash:** sha256 of upstream artifact hashes + relevant knobs + prompt file hashes + model name. It doubles as the layer-4 determinism key.
- **Failure policy:** a code-step failure stops the run with the failing check. An LLM-step failure (bad JSON after repair, budget hit) degrades: defaults replace proposals, a section reads "failed check: reason" (spec note rules), an exception row is written, and the run finishes so the reviewer sees what failed.
- **Parallelism:** peers and `--all` companies run in a process pool under `taskset -c 16-31` (I-001); vLLM batches concurrent calls. The embedder runs on CPU (`retrieval.device: cpu`, I-002) and loads only if a sub-agent searches.
- **Trace:** `trace.jsonl` per run (every LLM and tool call: prompt hash, tokens, seconds, output); `run.json` sums time and tokens. Target < 30 min per company (spec §2).
- **Backtest = the same runner in a loop** (§13.2), stopped after S8 (`report.type: none`).

**Research questions that need long work** ("which of the eight is cheapest on 2027E…") call `compare_peers`, which runs S1–S7 per company (cached): slow first, fast after. V4 runs it synchronously with a longer per-tool timeout (`research.tool_timeout_s`); async is parked.

---

## 10. Number trace and the evidence log

### 10.1 One reference grammar

`research/refs.py` parses, formats and **resolves** any id under an `AsOf`; `verify.py` calls it instead of its three special cases.

| Kind | Id | Resolves to |
|---|---|---|
| chunk | `UBER-2026Q2-ER-EX99-005` | chunk text (as now) |
| XBRL / inline XBRL | `xbrl:UBER:us-gaap:Revenues:<acc>:2026-04-01/2026-06-30` · `ixbrl:<acc>:<fact id>` | value, unit (, dims) |
| derived quarter | `q:UBER:revenue:2025Q4` | value + derivation + input ids |
| KPI, guidance | `kpi:UBER:gross_bookings.mobility:2026Q2` · `guide:UBER:adj_ebitda:2026Q3` | row + source chunk |
| price, macro | `px:UBER:2026-08-05` · `macro:DGS10:2026-08-05` | value |
| model | `model:UBER:2026-08-06:v3:adj_ebitda.FY2027` (or `scratch-n`) | output + formula + inputs |
| evidence, exception | `E-12` · `X-03` (within a report) | log row |

A claim is **verified** when its value matches the resolved value within its written precision (existing `_close` rules) and **calculated** when its calculation reruns from resolved inputs. Model refs verify against `model.json`, so Research answers keep V3's guarantee.

### 10.2 Constrained note writing

Each section call gets a **fact list**, pre-formatted by code (rounding, "2027E" / "2025A", "Q2 2026 (Apr–Jun)" once):
`{"id": "F-07", "text": "+2.4%", "value": 0.0238, "label": "Revenue vs our estimate", "ref": "model:…"}`.
The writer must use facts verbatim and mark them `{F-07}`. `research/trace.py` then:
1. extracts every number token (`verify.numbers_in`);
2. requires each to be a marked fact or an allow-listed non-financial token (dates, section numbers, "Q2", "10-K");
3. lints the spec's note rules: no "pending"/"TBD"; gaps read "n/a: …" or "failed check: …"; any `E`-period number carries its "E";
4. on failure, one repair turn with the violations; then the section becomes "failed check: number trace" plus an exception row.

**Tables** (2.1, 2.2, 4.1, 4.2, 5, 6, 7) are rendered by code from artifacts and never pass through the LLM. Layer-5 "100% traced" therefore means: tables by construction, prose by `trace.py`.

### 10.3 Evidence and exception log

- **Evidence rows** come from provenance: every model input, fact the note uses, and assumption. Fields (spec §2.3): ID, figure, value, source document, period, filing date, location (chunk heading path + printed page, or table caption), retrieved date, type (`reported`/`derived`/`analyst-imposed`), workbook cell.
- **Exception rows** come from every step via one `log_exception(severity, item, treatment)`: reconciliation gaps, missing KPIs, clipped proposals, fewer than 4 peers, no consensus (always), estimated earnings date, failed sections. Unresolved rows carry forward (§8).

---

## 11. Outputs

Directory: `output/research/<TICKER>/<as_of>/v<n>/`, indexed in `model_versions`.

| File | Built by | Notes |
|---|---|---|
| `note.md`, `note.pdf` | `research/note/` render, then LibreOffice headless (already installed, used by `evaluation/render.py`) | `update` (3–5 pages) or `initiation` (+ business overview, industry and competition, model appendix; same ten sections) |
| `workbook.xlsx` | `research/export/workbook.py` (openpyxl, new dependency) | Tabs: README, Summary, Bridge, Assumptions, Model, Model (prior), Scenarios, Valuation, DCF, Checks, Log; blue/black/green styles; layout of `docs/EXAMPLE_UBER_Q2_2026_MODEL_CHANGE_WORKBOOK.xlsx` |
| `model.json`, `estimates.json`, `valuation.json`, `revisions.json`, `assumptions.json`, `events.json`, `debates.json`, `checks.json`, `evidence.json` | steps | Machine-readable; Ask and Research cite these |
| `trace.jsonl`, `run.json` | runner | Time, tokens |

- **Parity test:** after export, `soffice --headless --convert-to xlsx` recalculates; every formula cell is compared with `model.json` (1e-9 relative). A mismatch is a layer-2 FAIL.
- **Review:** reports are written `draft`; `tenk report approve` sets `reviewed` (no number edits; amendments are a new version). Ask and Research show the status beside any cited estimate.
- **Note build order (spec §2.3):** sections 1–5 first, 6–10 once the model and extraction tests pass.

---

## 12. New technical problems

| Problem | Why new | Handling |
|---|---|---|
| **LLM memory leaks into the backtest** | Qwen may know reported 2025 figures; the as-of filter can't hide weights | The LLM never outputs forecast numbers; proposals are bounded and guidance-anchored. Report **clean quarters** separately: results released after the checkpoint (2026-04-21) = Q1 and Q2 2026, ~2 per company (Q3 2026 isn't out). Before V4.4, probe the model with no tools for Q3–Q4 2025 figures. The no-tools LLM baseline measures the leak directly |
| **Point-in-time data that changes** | Restatements, recast segments, FRED revisions, KPI redefinitions | Dated rows, `.latest()` under `AsOf`, `definition_version`, ALFRED vintages, restated history with an exception row |
| **Segments missing from company facts** | V3 relied on that API | Inline XBRL parser; LLM fallback from release tables (§4.5) |
| **Quarterly derivation** | V3 was annual | `quarters.py` + `q4_ties` check (§4.5) |
| **10-Q Items repeat per Part** | V0 validator assumes once-each 10-K Items | `FormSpec`, `I.n`/`II.n` keys (§4.4) |
| **Non-operating swings** (Uber's stake revaluations, valuation-allowance releases) | Distort GAAP EPS | Forecast 0; history shows reported EPS; exception when any of the last 4 quarters exceeds 10% of NI |
| **Instacart starts at Q3 2023** | Walk-forward needs 4 lags | S-1 quarters as data; seasonal share uses what exists (≥ 2 years) with an exception; ~4 backtest points (spec §6) |
| **Two evaluators of one model** | Workbook must be live | Single DSL, LibreOffice parity test (§5.1, §11) |
| **Peers at the same as-of** | Comps and relative rating depend on other models | S6 with caching; `--all` for relative rating |
| **Revision bridge attribution** | Order-dependent | Sequential substitution in a fixed order: (1) actuals, (2) guidance, (3) each assumption in register order; interactions go to the later step, documented in the Bridge tab. Type: *timing* if the next-year effect is < 20% of this year's, *recurring* if > 80%, else *mixed*. Confidence by horizon: actuals High; current quarter and FY, FY+1 Medium; FY+2 Low |
| **Long runs, crash-prone CPU, shared GPU** | Minutes × 8 × backtest | Checkpointed steps, `--resume`, `taskset`, CPU embedder (§9) |
| **Determinism with an LLM in the loop** | Layer 4 needs identical output | Valuation is code on saved artifacts; same inputs hash = same output. LLM steps at temperature 0, cached by prompt hash, so a rerun replays them |
| **Release time vs filing date** | After-close acceptance (after 17:30 ET) gets the next day's EDGAR date | Use `filing_date` (conservative); backtest as-of = day after `accepted_at` (ET) |
| **Units and scales** | Releases mix $B, $M, %, bps | Registry fixes units; values normalized; `verify.SCALES` gains `bps`, `count billions` |
| **Data licensing** | TSA, BTS, TLC, Stooq, Trends terms | Checked in V4.0a (spec §8); anything not permitted is dropped and listed in the write-up's limits |

---

## 13. Evaluation and backtest

### 13.1 Layers

Pass bars frozen 2026-10-27 from spec §5; a miss is reported as a miss.

| Layer | Test | Code | Pass bar |
|---|---|---|---|
| 1. Extraction | ~384 hand labels (12 fields × 4 quarters × 8 companies); BKNG and UBER first (~96), labeled **before** the extractor exists (D-006). `evaluation/gold/extraction-gold.jsonl`: value, unit, source quote | `evaluation/extraction.py` | ≥ 98% exact; 100% on revenue, EPS, main KPI |
| 2. Model integrity | §5.4 checks on every built model + unit tests on synthetic companies | `tests/test_model_*.py`, in-pipeline | 100%; any FAIL blocks forecasting |
| 3. Forecast | Walk-forward (§13.2) | `evaluation/backtest.py` | MAPE below seasonal-naive on revenue and main KPI; bias near zero, reported with sign |
| 4. Valuation | Identical inputs → byte-identical `valuation.json`; monotonicity (higher WACC → lower PT) | `tests/test_valuation.py` | Identical; every target multiple inside the peer range or flagged |
| 5. Note | Number trace, ten sections present, rubric (judge gpt-oss-20b + human sample), **seeded errors** (corrupt a fact or table; trace/lint must catch it) | `evaluation/note_eval.py` | 100% traced; rubric ≥ 4/5 |
| Ask, Research | ~80 gold questions over the eight and the spec §2.1/§2.2 categories; Research golds check the `model:` ref and stated assumptions, not a fixed number. Gold format gains `period`, `form`, `as_of` | existing scoring + `model:` grading | Ask ≥ 85% correct and verified, 100% of cited claims resolve · Research ≥ 90% with a `model:` ref and stated assumptions, 100% of computed numbers from model tools |

### 13.2 Backtest (`evaluation/backtest.py`)

```text
for company in the eight:
  for release from Q4 2024 onward:                         (~6 per company, ~4 for CART)
     D = day after the release's accepted_at (ET)
     run S1–S8 at D  →  forecast next quarter
     score vs the actual (read at a later as-of) and vs baselines
```

- **Baselines:** seasonal naive (same quarter last year × latest y/y growth); guidance midpoint (n/a without numeric guidance); plain LLM with no tools.
- **Metrics:** MAPE; bias (positive = too high); guidance call (above or below midpoint, and did we call it). Medians and ranges by company; paired Wilcoxon per baseline; clean quarters separate.
- **Ablations** (spec §6): extraction LLM vs rules; fixed vs fitted `d`; guidance midpoint vs high end; with vs without cross-checks. Added here: proposer `llm` vs `defaults`; Qwen vs Claude on the LLM steps. The rules-based extractor is a label-pattern table-row matcher over the registry (`extract.method: llm | rules`).
- **Long-horizon variant:** one run as of early February 2025 forecasting all of 2025.
- **Output:** `evaluation/results/backtest-<label>/`: `report.md`, per-forecast CSV, chart by company.

---

## 14. Code layout, config, CLI

```text
src/tenk_agent/
  asof.py                  NEW  AsOf
  corpus.py                CHG  reads companies.yaml
  store.py                 CHG  new columns and tables; dated reads take asof
  acquisition/             CHG  edgar.py (any form, 8-K exhibits, accepted_at, S-1) · NEW prices.py macro.py
                                crosscheck.py insiders.py (V4.7)
  preprocess/forms.py      NEW  FormSpec; validation.py reads it
  facts/                   NEW  xbrl.py ixbrl.py quarters.py extract.py kpis.yaml
  research/                NEW  refs.py datapack.py events.py debates.py propose.py forecast.py
                                valuation.py revisions.py views.py pipeline.py
                                model/ (dsl.py graph.py templates/*.yaml checks.py)
                                note/ (sections.py trace.py render.py)
                                export/ (workbook.py pdf.py)
  agent/                   CHG  tools.py (groups, Toolbox(asof, mode)) · loop.py (sub-agent prompt, groups, budgets)
  generation/verify.py     CHG  resolves ids through research/refs.py
  evaluation/              NEW  extraction.py backtest.py note_eval.py
  prompts/                 NEW  extract.md propose.md events.md debates.md section.md (+ schemas)
```

**Config.** `config.yaml` keeps its per-stage sections and gains per-step `budgets`. `companies.yaml` (§4.1) and `research.yaml` (every §6 knob, proposer bounds, step budgets, report type) are pinned per experiment (D-019). New dependency: `openpyxl`. New secret: `FRED_API_KEY` (`SEC_USER_AGENT` exists).

| Command | Does |
|---|---|
| `tenk companies verify` | Resolve CIKs, FYE, filing counts; write them to `companies.yaml` |
| `tenk fetch [--forms ...] [--prices] [--macro] [--crosscheck]` | Acquisition |
| `tenk build --stage chunk,load,embed,facts,quarters,extract` | Text and structured build |
| `tenk tool NAME k=v --as-of D` | One tool by hand |
| `tenk agent "..." --as-of D [--mode ask\|research]` | Ask / Research |
| `tenk report TICKER --as-of D --type update\|initiation [--all] [--from S7] [--resume]` | Report |
| `tenk report approve TICKER --as-of D --by INITIALS` | Mark reviewed |
| `tenk backtest --companies ... --from DATE --to DATE [--label]` | Walk-forward |
| `tenk eval extraction` · `tenk eval note` | Layers 1 and 5 |

### 14.1 Migration from V3

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
- The V3 agent gold rerun (vLLM, ~78 questions) runs at M4, M5 and at the end of each V4 phase that touches shared code.
- `xbrl_facts` stays in `mode="v3"`; V4 modes get `financials` (§7.1).
- When V4 no longer needs it, V3 retirement is its own decision entry, not a side effect.

---

## 15. Build order and test gates

Each phase passes its gate before the next (spec §7). Pilot BKNG + UBER (UBER is the widest template), then all eight.

| Phase | Builds | Gate |
|---|---|---|
| **V4.0a Pre-flight** (spec §8) | `companies verify`; one company end to end on EDGAR (company facts, dates, rate limit); confirm the three §4.5 XBRL facts; release dates and numeric-guidance survey; dataset terms; D-001 successor entry | Findings written into this doc; `companies.yaml` filled |
| **V4.0b Data** | Migration M1–M6 (§14.1), then `facts`/`ixbrl`/`quarters`, prices, macro, as-of search | V3 tests and V3 agent gold green; unit tests incl. a leak test (as-of before a filing → zero rows from it); Q4 ties on the pilot |
| **V4.1 Extraction** | Pilot hand labels first; extractor, registry, guidance | Layer 1 on the pilot, then all eight |
| **V4.2 Ask** | Tool groups, `refs.py`, prompts, ask gold set | Ask gold bars |
| **V4.3 Model** | DSL, graph, templates (`segments`, `marketplace` first), checks, workbook Model tab + parity test | Layer 2 on the pilot |
| **V4.4 Forecast + backtest** | Forecast, proposer (defaults first, then LLM), S1–S5 with checkpoints, backtest harness, baselines, leakage probe | Layer 3 on the pilot, then all eight |
| **V4.5 Valuation + Research** | S6–S8, model tools, research gold set | Layer 4; Research gold bars |
| **V4.6 Report** | S2 events, S9 debates, S10 note (sections 1–5, then 6–10), trace and lint, S11 export, review status | Layer 5 |
| **V4.7 Scale and write-up** | Cross-checks, Form 4, ablations, `--all` ratings, results table and limits (spec §6) | Results table complete |

---

## 16. Decisions taken here (veto any)

Each becomes a DECISIONS.md entry when built. Rows 1–11 fill gaps in the spec; the rest are design choices.

| # | Item | Decision |
|---|---|---|
| 1 | As-of boundary | Strictly before D (§3); spec header says "on or before" |
| 2 | Seasonality | q-4 rule is primary; seasonal share spreads full-year guidance and flags drift (spec lists both without combining them) |
| 3 | Fade | `d` per quarter (spec example "×0.90 per quarter"); per year in DCF years 4–10 |
| 4 | Scenario multiples, shocks | Base ±15% (from the example); ±1σ driver shock, proposer within [0.5σ, 2σ] |
| 5 | WACC | All-equity below 10% debt weight, else weighted (the example treats UBER as all-equity) |
| 6 | EV | Debt − cash and ST investments; stakes and leases not counted, flagged |
| 7 | 12-month PT | Blended value, no roll-forward |
| 8 | Non-operating items | Forecast 0, flagged |
| 9 | No numeric guidance | Growth guidance → level; none → trend + fade, guidance baseline n/a |
| 10 | Relative rating | Only with `--all` |
| 11 | Consensus | None; an optional hand-recorded dated figure later (spec §1.3) |
| 12 | Model source of truth | Python from one DSL; xlsx generated with formulas; parity test |
| 13 | Statement detail | Condensed (§5.2); full detail in the data pack |
| 14 | Release preprocessing | Rules only, no structure agent unless the pilot shows bad headings |
| 15 | PDF | LibreOffice headless (already used); no new dependency |
| 16 | Review | `draft` → `reviewed` via CLI; the human decides, the agent doesn't |
| 17 | Clean backtest quarters | Q1–Q2 2026 (§12) |

**Settled earlier:** replace the corpus (V0–V3 results stay documented; code stays generic over the company list) · V3 stays runnable from `companies-v3.yaml` as the shared-code regression check (§14.1) · Ask/Research pass bars (§13.1) · the LLM proposes assumptions, code computes every number · Qwen3.6-27B local by default, Claude optional for comparison · both backtest designs (walk-forward main; one longer-horizon run) · both ratings, no consensus · CLI only, output under `output/` · Ask cites saved reports for estimates · Stooq for prices · one SQLite file (D-005) · ~80 gold questions.

**Parking lot:** earnings-day diff job (new release vs our estimate → draft section 2) · tool routing by question class · airline read-throughs (spec §4 excludes airline estimates, not their data) · async model tools for `--all`-sized Research questions.
