# V4 technical design: a junior sell-side research agent

> **What this is:** the contract of the V4 build, as built. The [spec](V4_SPEC.md) says what to build; this doc says how the system is shaped to do it: components, interfaces, rules, and what happens when things fail. Gaps against the spec are listed once, in §13.1.

| For | Read |
|---|---|
| Full interface schemas | [V4_INTERFACES.md](V4_INTERFACES.md) |
| How each block works, and its tests | [stages/V4_*.md](stages/), linked from each section |
| One run followed end to end | [V4_WORKED_EXAMPLE.md](stages/V4_WORKED_EXAMPLE.md) |
| Model equations | [V4_FINANCIAL_MODELING.md](stages/V4_FINANCIAL_MODELING.md) |
| Results | [V4_RESULTS.md](V4_RESULTS.md) |

**How to read it.** Top down: §1–§2 give the system, its components and its interfaces. §3–§12 take one block each, in dependency order (§7, the Toolbox, is the agent's view of §3–§6 and §11). Each has three parts: **contract, rules, failure handling and limits.** §12 is evaluation; §13 is reliability and gaps. "Spec §n" is a section of the spec; a bare "§n" is a section of this doc.

*Updated 2026-11-03.*

---

## 1. Overview

### 1.1 Goals

One agent covers eight US travel and mobility companies from free public data, in three modes (spec §2):

| Mode | In | Out |
|---|---|---|
| **Ask** | A question + as-of | A short cited answer from filed data |
| **Research** | A question + as-of | An answer computed by the model, assumptions shown |
| **Report** | Ticker + as-of | A 10-section draft note, a model-change workbook, an evidence and exception log |

- **Companies:** BKNG, EXPE, ABNB, TRIP (travel) · UBER, LYFT, DASH, CART (mobility and delivery).
- **Every run takes an as-of date** and sees only data published before it. This makes the walk-forward backtest possible.
- **V4 reuses V0–V3's pipeline** (parsing, chunking, retrieval, agent loop, claim check). V3 stays runnable as a regression check.

### 1.2 Principles

| Principle | Means | Where |
|---|---|---|
| **As-of everywhere** | No read sees data published on or after the as-of | §2.5 |
| **Code computes every number** | The LLM extracts, proposes bounded assumptions and writes prose; it never outputs a forecast or valuation number | §2.5 |
| **Everything is cited** | Every figure carries an id that resolves to its source | §10 |
| **Flag, don't hide** | A gap, conflict or failed check becomes an exception row, never an estimate shown as fact | §10.2 |
| **Degrade, don't stop** | A failed LLM step leaves "failed check: reason"; only an integrity failure stops a run | §13.3 |
| **Artifacts, not transcripts** | Steps hand over JSON files, not chat history | §9.1 |
| **One definition, two evaluators** | The model is defined once; Python and the workbook evaluate it and must agree | §6.2 |
| **Knobs in config** | Every default lives in a pinned YAML file | Appendix A |

### 1.3 Glossary

| Term | Meaning |
|---|---|
| **as-of (D)** | The run date: the world as seen at the market open of D (US/Eastern) |
| **release** | An 8-K Item 2.02 exhibit 99.x (earnings release or shareholder letter). Form key `ER` |
| **period key** | `2026Q2`, `FY2025` |
| **data pack** | Every model input with its citable id and filing date (step S1) |
| **driver** | A model input the forecast grows or holds: volume, price per unit, segment bookings, take rate, cost share |
| **assumption** | A named forecast knob, such as `gb_g_mobility.g0` or `fade` |
| **anchor** | A guidance figure the forecast is solved to hit |
| **model book** | A session's models at one as-of, with their `model:` ids |
| **version** | A saved Report run (`UBER:v2`), with its parent |
| **tool set** | The named list of tool groups a run may call (§7.1) |
| **fact (F-n)** | A pre-formatted figure the note may print, with its source id |
| **E-n / X-n** | Evidence and exception rows of a report's log |
| **layers 1–5** | The spec's evaluation layers: extraction, model integrity, forecast, valuation, note |

---

## 2. System architecture

### 2.1 Component map

<p align="center">
  <img src="img/v4-components.svg" alt="V4 component map. External sources feed Acquisition, which writes a raw cache. Text processing and Facts and extraction build the Store, one SQLite file read only through an AsOf filter. Retrieval and the Model engine read the Store. The Agent runtime (loop, toolbox, sub-agents) reaches Retrieval, the Store and the Model engine through tools. Ask and Research are agent runs; the Report pipeline runs fixed steps, calls the Model engine and bounded sub-agents, and its artifacts pass Verification and Export into a note, workbook and log. Saved versions go back to the Store. Evaluation reads gold sets and every layer's outputs. Components that call the LLM are marked." width="100%">
</p>

Boxes are components (§2.2); numbered arrows are interfaces (§2.3). Pink marks a component that calls the LLM.

### 2.2 Components

| Component | Does | LLM | § |
|---|---|:-:|---|
| **Acquisition** | Fetches filings, XBRL, prices, macro, ERP, cross-checks and Form 4 into a raw cache | | §3 |
| **Text processing** | Turns filings into Items, chunks and vectors (by rules) | | §3 |
| **Facts** | Loads XBRL facts; derives quarters on read | | §3 |
| **Store** | One SQLite file; every read filtered by the run's as-of | | §3 |
| **Retrieval** | Hybrid search over chunks | | §4 |
| **Extraction** | KPI and guidance rows from releases, each checked against a quote | ✓ | §5 |
| **Model engine** | Model, checks, forecast, scenarios, valuation | | §6 |
| **Toolbox** | The tools the agent calls: its view of the blocks above | | §7 |
| **Agent runtime** | The tool-calling loop, prompts and budgets: Ask, Research, sub-agents | ✓ | §8 |
| **Report pipeline** | Steps S1–S11 per ticker and as-of; the note; versions | ✓ | §9 |
| **Verification** | Ids, claim check, number trace, consistency check | | §10 |
| **Export** | Note, PDF, workbook, parity check, review status | | §11 |
| **Evaluation** | Gold sets, scorers, backtest, note eval | ✓ judge | §12 |

Code paths: Appendix C.

### 2.3 Interface catalog

One row per boundary. Each is stated in its section; the full schema is in [V4_INTERFACES.md](V4_INTERFACES.md) under the same number.

| # | Interface | Between | Contract in | Schema |
|---|---|---|---|---|
| 1 | **Raw cache** | Acquisition → builders | §3.1 | [§1](V4_INTERFACES.md#1-raw-cache) |
| 2 | **Store** | everything → SQLite | §3.1 | [§2](V4_INTERFACES.md#2-store) |
| 3 | **Search** | Toolbox → Retrieval | §4.1 | [§3](V4_INTERFACES.md#3-search) |
| 4 | **Tool calls** | agent ↔ Toolbox | §7 | [§4](V4_INTERFACES.md#4-tool-calls) |
| 5 | **LLM calls** | code ↔ model | §8.1 | [§5](V4_INTERFACES.md#5-llm-calls) |
| 6 | **Extraction rows** | extractor → Store | §5.1 | [§6](V4_INTERFACES.md#6-extraction-rows) |
| 7 | **Templates** | config → Model engine | §6.1 | [§7](V4_INTERFACES.md#7-templates) |
| 8 | **Model and valuation files** | Model engine → Report, tools, Export | §6.1 | [§8](V4_INTERFACES.md#8-model-and-valuation-files) |
| 9 | **Pipeline artifacts** | step → step | §9.1 | [§9](V4_INTERFACES.md#9-pipeline-artifacts) |
| 10 | **Reference ids** | everywhere | §10.1 | [§10](V4_INTERFACES.md#10-reference-ids) |
| 11 | **Fact list** | Report code → note writer | §10.1 | [§11](V4_INTERFACES.md#11-fact-list-and-log) |
| 12 | **Outputs** | system → reviewer | §11.1 | [§12](V4_INTERFACES.md#12-outputs) |
| 13 | **Gold and results** | Evaluation ↔ files | §12.1 | [§13](V4_INTERFACES.md#13-gold-and-results) |
| – | **Config**, **CLI** | user → system | Appendix A, B | – |

### 2.4 Data flow and control flow

<p align="center">
  <img src="img/v4-architecture.svg" alt="V4 data and control flow. Sources are fetched into a raw cache and built offline along four lanes (text, structured facts, KPI and guidance extraction, market data) into one SQLite file read only through the AsOf filter. Ask and Research are agent runs whose answers pass a claim check that resolves every cited id. Report is a fixed pipeline of eleven steps, S1 to S11, with the LLM only in S2, S4, S9 and S10; it writes a note, a workbook and a saved model version that Ask and Research can cite. Evaluation grades extraction, model integrity, forecast, valuation and the note." width="100%">
</p>

**Build, offline:** `tenk fetch` fills the raw cache. `tenk build` then runs four lanes into the store: text, facts, KPIs (LLM extraction) and market data. New filings are appended without a rebuild.

**Run, at one as-of:**

| Mode | Who decides the steps | New numbers come from |
|---|---|---|
| **Ask** | The agent, with tool set `ask` | A calculator over reported figures |
| **Research** | The agent, with tool set `research` (adds model tools) | Model tools only |
| **Report** | Code: fixed steps S1–S11; LLM calls are leaf tasks | The Model engine only |

- Ask and Research are one agent with two tool sets.
- Report is not an agent loop and never starts an Ask or Research run (spec §2.3). Only its sub-agents (S2, S4, S9) call tools.
- Ask and Research read a saved report back through `research_report` and `model:…:v<n>` ids.

### 2.5 Cross-cutting rules

**The as-of rule.** `AsOf(D)` is the world at the market open of D (US/Eastern). Data is visible only if it became public **strictly before** D:

| Data | Visible when |
|---|---|
| Filings, their chunks, facts, KPIs, guidance | `filing_date < D` |
| Prices | `date < D` (as-of 2026-08-06 sees the 08-05 close) |
| FRED / ALFRED | observation `< D`; revised series (CPI, UMCSENT, RSFSDP) use the vintage known before D; DGS10 counts as published the day after its date |
| Damodaran ERP | three days after the month starts |
| BTS, NYC TLC | publish date (BTS: month end + 90 days, estimated) |
| Our saved versions | `as_of < D` |

- Strictly before D (spec §1.2).
- `asof.py` is the only code that compares dates.
- A `Store` is bound to one `AsOf` when opened; every read filters through it.
- No tool can widen the view: `research_report`'s `as_of` only narrows it.
- `.latest()` takes, per value, the newest filing visible at D; a restatement counts only once filed (spec §1.6).

**The no-LLM-numbers rule.** LLM output reaches the numbers only three ways:

1. Extracted rows that code checks against a verbatim quote (§5.2).
2. Bounded assumption proposals that code clips (§6.2).
3. Prose in which every number is a marked fact (§10.2).

Tables are always written by code (spec §3). This keeps valuation reruns byte-identical.

**Trace.** Every number in an answer or note carries an id (§10). Agent runs keep their tool calls; `report.json` records each step's time, LLM calls and tokens.

---

## 3. Data layer

How it works: [V4_DATA.md](stages/V4_DATA.md). Schemas: [V4_INTERFACES.md §1–2](V4_INTERFACES.md#1-raw-cache).

### 3.1 Contract

Fetch every source into a raw cache, build one SQLite file from it, and serve every read through the run's as-of.

Code: companies, EDGAR filings, market data, cross-checks and Form 4 (`acquisition/`); text processing (`preprocess/`, `chunking/`); facts (`facts/xbrl.py`, `quarters.py`); the store (`store.py`, `asof.py`).

| Interface | Contract |
|---|---|
| **Raw cache** (1) | One folder per filing (`raw/filings/<T>/<period>-<form>/`); `manifest.json` lists each filing's form, period, filing date, acceptance time and retrieval time |
| **`companies.yaml`** | Per company: CIK (written by `tenk companies verify`), sector (peers = same sector), template, main KPI |
| **Store** (2) | Tables `filings`, `chunks`, `facts`, `kpis`, `guidance`, `market`, `insider_tx`, `model_versions`. **Every row carries the date it became public** |
| **Store reads** (2) | `Store(path, asof)`: every read method filters by that as-of |
| **Ids** | Chunks `UBER-2026Q2-ER-EX99-005`; facts `xbrl:<T>:<taxonomy>:<concept>:<accession>:<start>/<end>`; derived quarters `q:<T>:<metric>:<period>` |

### 3.2 Rules

- One filing per form and period; amendments are ignored.
- Releases are 8-K Item 2.02, every `EX-99.*` exhibit (a release can carry two, as BKNG's did on 2026-08-04).
- A release's period is the last quarter end before it was filed, checked against the 10-Q (releases carry no XBRL period).
- DEF 14A: the first proxy each year, keyed to the prior fiscal year; later ones are special meetings (spec §1.2).
- Fetch cache-first; builds run offline.
- Filings are split into Items by rules, not the LLM.
- Inline XBRL is read for every company-specific and segment fact; company facts has neither.
- Quarters are derived on read, never stored: Q4 = FY − 9M; Q2 and Q3 from year-to-date when needed (spec §1.6).
- A metric uses one XBRL concept per fiscal year.
- A year recast by a later filing reads `restated`, logged, not `fail` (spec §1.6).
- EPS and share counts: direct facts only; Q4 EPS from the release.
- Sources that block scripts are dropped, never worked around (Stooq, TSA, Google Trends).

### 3.3 Failure handling and limits

| Case | Handling |
|---|---|
| Source unreachable | The cached copy is used |
| A company's fetch fails | Logged; the others continue |
| A release's period disagrees with its 10-Q, or two filings claim one period | Fetch error, flagged |
| Missing concept or quarter | Blank; logged when the model reads it |
| Item structure not found | Reported; the filing keeps the Items found |
| Slide-image releases (ABNB; CART to Q4 2025; BKNG from Q4 2024) | Text from the hidden text layer; no tables |
| Tiingo free plan | 1,000 requests a day, 500 symbols a month |
| NYC TLC | ~540 MB per month; aggregated at fetch |

---

## 4. Retrieval

How it works: [V4_RETRIEVAL.md](stages/V4_RETRIEVAL.md) and [V1_RETRIEVAL.md](stages/V1_RETRIEVAL.md). Schema: [V4_INTERFACES.md §3](V4_INTERFACES.md#3-search).

### 4.1 Contract

Find the chunks that answer a query, one search per company and filing, under the run's as-of. No LLM.

Code (`retrieval/`): decomposer, searcher, index (Qwen3-Embedding-0.6B, FTS5), reranker (bge-reranker-v2-m3).

| Interface | Contract |
|---|---|
| **Search** (3) | `search_filings(query, companies?, periods?, forms?, k = 8)` → the searches it ran, and up to k chunks with id, company, form, period, filing date and text |

### 4.2 Rules

- Companies come from the arguments, else the query; no period = the latest filing of each form at D.
- One sub-question per company and filing, naming only that company.
- Keyword and dense search, fused, then reranked.
- The model sees parent chunks: a whole subsection or table, not a fragment.
- Results are merged round-robin until k.

### 4.3 Failure handling and limits

| Case | Handling |
|---|---|
| No company, or an unknown one | Error listing the corpus |
| Bad period key | Error |
| No filing for a slot at that as-of | Fewer chunks, not an error |
| GPU held by vLLM | Embedder and reranker run on CPU |
| Size | 8 chunks per call, each up to 1,280 tokens |

---

## 5. Extraction (layer 1)

How it works: [V4_EXTRACTION.md](stages/V4_EXTRACTION.md). Schemas: [V4_INTERFACES.md §6](V4_INTERFACES.md#6-extraction-rows).

### 5.1 Contract

Read KPIs and guidance from earnings releases into checked, quoted rows.

Code: LLM extractor (`facts/extract.py`), rules extractor (`facts/rules.py`, an ablation), registry (`facts/kpis.yaml`), loader (`tenk build --stage kpis`).

| Interface | Contract |
|---|---|
| **Registry** | Per company: each KPI's key, unit, segments and labels (from spec §1.5) |
| **Reply** | KPI and guidance rows, each with value, unit, source chunk id and a verbatim quote |
| **Extraction rows** (6) | Stored in `kpis` and `guidance` with period, filing date and definition version. Ids `kpi:<T>:<metric>[.<segment>]:<period>`, `guide:<T>:<metric>:<period>` |

### 5.2 Rules

- Input is only that release's chunks; no other filing can leak in.
- Code keeps a row only if the chunk id was given, the quote is verbatim, the value is in the quote, and the metric is in the registry. The LLM proposes; code decides what is stored.
- One repair turn, then an exception row; never a guessed value (spec §2.3).
- GAAP diluted EPS only.
- A recast gives earlier periods a new definition version; readers take the newest (spec §1.6).
- Guidance keeps its basis (level, growth or margin); the forecast converts it.
- Where a KPI is also an XBRL fact, the filing wins and the gap is logged (spec §1.6). **Not built** (§13.1).

### 5.3 Failure handling and limits

| Case | Handling |
|---|---|
| Row fails after repair | Exception row; never stored |
| Release with no valid rows | Its KPIs are missing; the model logs the gap |
| No numeric guidance (TRIP) | Guidance baseline reads "n/a" |
| Input size | ≤ 12k tokens per release; long releases send matching chunks first |

---

## 6. Model engine and valuation

How it works: [V4_MODEL.md](stages/V4_MODEL.md). Equations: [V4_FINANCIAL_MODELING.md](stages/V4_FINANCIAL_MODELING.md). Schemas: [V4_INTERFACES.md §7–8](V4_INTERFACES.md#7-templates).

### 6.1 Contract

Build a three-statement model from the data pack, forecast it, and value it. All deterministic code.

Code (`research/`): data pack (S1), DSL and graph, templates, history (S3), layer-2 checks, forecast and scenarios (S5), outside inputs, proposer (S4), valuation (S6–S7), model book.

| Interface | Contract |
|---|---|
| **Templates** (7) | YAML lines: inputs (history from a source id), formulas and forecast expressions, plus drivers, scenarios and anchors |
| **DSL** | Arithmetic, `x[-4]`, `fy()`, `fyp()`, `min`, `max`, `if_`, `first`, `@assumption`. Anything else is rejected |
| **`model.json`** (8) | Every cell (line × period) with value and kind. **Input cells carry their source id and filing date** |
| **`valuation.json`** (8) | Market cap, EV, cost of capital, peers, PT per scenario, weighted PT, return, rating, DCF grid, `inputs_hash` |
| **`research.yaml`** | Every forecast and valuation knob (Appendix A) |

### 6.2 Rules and algorithms

**Model** (spec §3.1)

| Template | Companies | Revenue build |
|---|---|---|
| `marketplace` | BKNG, EXPE, ABNB | Room nights × bookings per night = GB; revenue = GB × take rate |
| `segments` | UBER | Per segment: GB × its take rate; costs a share of total GB |
| `orders` | DASH, LYFT, CART | Orders or rides × value per unit; revenue = that × take rate |
| `trip` | TRIP | Revenue on one faded rate; EBITDA a margin on revenue |

- One definition (templates + DSL), two evaluators; Python is the source of truth.
- Condensed statements, shared by all templates.
- Tax at the trailing rate, bounded 10–30%.
- Every input cell carries its source and filing date.
- Missing inputs stay blank, never 0.
- Non-operating items forecast at 0.
- Travel prepayment is not modeled (spec §3.1).

**Forecast** (spec §3.2)

```text
first forecast quarter   Driver(q) = Driver(q-4) × (1 + g), anchored to the guidance midpoint
later quarters           Driver(q) = last FY × (1 + g) × seasonal share of q (3-year mean)
fade                     g(t+1) = g(t) × 0.90
ratios                   same quarter last year + a shift held for 4 quarters, then 0;
                         bounded to the 8-quarter range ± a band (take rate 50 bps, margins 200 bps)
```

- Forecast per driver, never per total (spec §3.2).
- A ratio's shift holds until lapped, then 0; a one-off change is not compounded.
- Ratios stay within the 8-quarter range ± a band.
- Each guide anchors its own driver, upstream first (bookings → revenue → EBITDA); later anchors see earlier ones.
- Anchor at the midpoint, not the high end (signed off before the backtest, spec §7).
- An anchor the bounds stop is logged, never forced.
- Scenarios move each driver by ±1σ of its history.
- Outside data (BTS, NYC TLC, FRED) nudges one starting growth rate per company, weighted by fit; guidance overrides it (spec §3.2).

**Proposer (S4).** Off by default: it did not beat the defaults in the backtest (spec §7). When on:
- It may move only growth starts, ratio shifts and the fade, never an anchored assumption.
- Code clips each proposal to the history's range.
- A proposal is kept only if one of its evidence ids resolves.

**Valuation and rating** (spec §3.3)

| Item | Rule |
|---|---|
| Market cap | Last close before D × latest diluted shares |
| EV | Market cap + debt − cash and short-term investments (leases, stakes, customer funds excluded and logged) |
| Peers | Same-sector companies, default assumptions, same as-of |
| Multiple PT | Peer-median 2027E EV/EBITDA × our 2027E adj. EBITDA → per share; bear/bull ∓/± 15%; a target outside the peer range is flagged |
| DCF | Unlevered FCF = adj. EBITDA − SBC − tax − capex; 10 years, faded; Gordon terminal at 3.0% |
| Cost of capital | Ke = DGS10 + β × ERP; β from monthly returns vs SPY since 2023; WACC = Ke when debt is under 10% of capital |
| Price target | 0.6 × multiple + 0.4 × DCF per scenario, weighted 25/50/25; no roll-forward |
| Rating | Buy ≥ +15%, Sell ≤ −10%, else Hold; relative ranking only with `--all` |
| Determinism | Identical inputs → byte-identical `valuation.json` (layer 4) |

**Layer-2 checks.** Run after S3 and S5; the valuation checks after S7. Any FAIL stops the run.

| Check | Passes when |
|---|---|
| `balance` | Assets = liabilities + equity within $1M, and = the reported total |
| `quarters_sum` | Q1–Q4 sum to FY for every flow line (recast years skipped and listed) |
| `q4_ties` | Derived Q4 + 9M = the 10-K's FY |
| `take_rate` | Recomputes from reported revenue / GB |
| `segments_sum` | Segments sum to totals |
| `no_future_input` | Every input was filed before the as-of |
| `formula_only` | Forecast cells are formulas |
| `wacc_gt_g`, `weights` | WACC > g; weights sum to 1 |

### 6.3 Failure handling and limits

| Case | Handling |
|---|---|
| Layer-2 or valuation check FAIL | The run stops, naming the check |
| Missing input | Blank, logged |
| Volume KPI not disclosed | Template falls back one level up, logged |
| Anchor out of bounds | Logged, not forced |
| Fewer than 3 years for seasonal shares | Fewer years, logged |
| DCF value ≤ 0 | Left out of the PT, logged (TRIP) |
| Fewer than 24 monthly returns (CART's early as-ofs) | β = 1, logged |
| Proposer fails | Defaults |
| Outside series too short or wrong-signed | Not used; logged |
| Scope | Horizon 2027Q4 (DCF years beyond); condensed statements; no deferred bookings; three peers per sector |

---

## 7. Toolbox

How it works: [V4_AGENT.md](stages/V4_AGENT.md#tools). Arguments and result schemas: [V4_INTERFACES.md §4](V4_INTERFACES.md#4-tool-calls).

### 7.1 Contract

A tool is a function the agent calls with JSON arguments; it returns JSON with citable ids. The **Toolbox** (`agent/tools.py`) holds the schemas, runs the calls, and sees exactly what its store's as-of sees. Each tool is a thin front for one block (§3–§6, §11); the Toolbox adds no logic of its own.

| Caller | Uses |
|---|---|
| Ask | tool set `ask` |
| Research | tool set `research` |
| Report sub-agents (S2 events, S4 proposer, S9 debates) | tool set `ask` |
| Report code steps, builders, evaluation | their blocks directly, no tools |
| A person | `tenk tool NAME key=value …` (`--list` prints the schemas) |

| Group | Tools | `ask` | `research` |
|---|---|:-:|:-:|
| text | `search_filings` | ✓ | ✓ |
| calc | `calculator` | ✓ | ✓ |
| facts | `financials`, `company_kpis`, `guidance`, `filings` | ✓ | ✓ |
| market | `market_data`, `crosscheck` | ✓ | ✓ |
| reports | `research_report` | ✓ | ✓ |
| model | `model_view`, `run_scenario`, `compare_peers`, `variance_review` | | ✓ |

V3's sets `text` and `full` remain for the V3 corpus.

### 7.2 Catalog

| Group | Tool | Returns | Backed by |
|---|---|---|---|
| text | `search_filings` | Chunks for a query, per company and filing | Retrieval (§4) |
| calc | `calculator` | A result with its formula and inputs | `agent/tools.py` |
| facts | `financials` | Reported and derived quarterly figures (`q:`, `xbrl:` ids) | Store facts (§3) |
| facts | `company_kpis` | KPI rows with their quotes (`kpi:` ids) | Store KPIs (§5) |
| facts | `guidance` | Guidance ranges, with the actual once reported (`guide:` ids) | Store guidance (§5) |
| facts | `filings` | The filings visible at the as-of | Store (§3) |
| market | `market_data` | Prices and macro series (`px:`, `macro:` ids) | Store market (§3) |
| market | `crosscheck` | NYC TLC or BTS monthly series (`cc:` ids) | Store market (§3) |
| reports | `research_report` | A saved version's PT, rating, estimates and **review status** | Saved versions (§11) |
| model | `model_view` | Model lines and assumptions (`model:` ids) | Model book (§6) |
| model | `run_scenario` | Base vs scenario lines, PT and rating after changing assumptions | Model book (§6) |
| model | `compare_peers` | One metric across companies, with each one's assumptions | Model book (§6) |
| model | `variance_review` | Our saved estimate vs the actual, with a revenue bridge | Saved version + Store |

### 7.3 Rules

- Every tool is bound to the run's as-of (§2.5).
- Every value in a result carries an id that resolves (§10.1), so the claim check can verify what the model cites.
- Errors are results: `{"error": …}` plus the valid choices.
- Tools come in groups; no run gets every tool.
- Results are capped at 40 rows and 16,000 characters.
- Ask cites estimates only through `research_report`; Research computes only through model tools, assumptions listed (spec §2).
- `run_scenario` writes a session-only `scratch-n` run, clipped to the proposer's bounds (§6.2). Only Report creates versions.
- Peers are built with default assumptions (no LLM), once per ticker and as-of.

### 7.4 Failure handling and limits

| Case | Handling |
|---|---|
| Unknown tool, argument, metric, line or assumption | Error with the valid choices; the model retries |
| Unknown company, or a search naming none | Error listing the corpus |
| Nothing visible at the as-of | Empty rows (or `report: null`), not an error |
| A model can't be built | Error naming the failing check (§6.3) |
| Result over the caps | Truncated, with `"omitted": n` |
| First `compare_peers` call | Slow: peers are built on first use |

---

## 8. Agent runtime

How it works: [V4_AGENT.md](stages/V4_AGENT.md). Reply schemas: [V4_INTERFACES.md §5](V4_INTERFACES.md#5-llm-calls).

### 8.1 Contract

Run the LLM: the Ask and Research loop, Report's sub-agents, and every single-shot call. Each reply is one JSON object.

Code: the loop (`agent/loop.py`, LangGraph), prompts (`prompts/*.md`), sub-agents (`research/subagents.py`, `propose.py`), the model book (`research/book.py`), and one model interface (`models.py`: vLLM or Claude).

**LLM calls** (5):

| Call | Tools | Budget |
|---|---|---|
| Ask / Research answer | `ask` / `research` | 20 calls, 20k context |
| KPI extraction | – | 1 call + 1 repair per release |
| S2 events | `ask` | 8 calls, 16k |
| S4 proposer | `ask` | 10 calls, 16k |
| S9 debate, per driver | `ask` | 8 calls, 16k |
| S10 prose slot | – | 1.5k tokens out, 1 repair |
| Judge (evaluation) | – | gpt-oss-20b, a second pass |

### 8.2 Rules

```text
question → model → tool calls → tools → model → … → JSON answer → claim check (§10.2)
```

- The first turn must be a tool call; no answers from memory.
- A repeated identical call errors and still counts.
- After the call budget or a full context: one turn with tools off to answer.
- An unparseable answer gets one retry.
- Every answer passes the claim check before it is shown.
- Sub-agents return JSON; an item is kept only if one evidence id resolves at D.
- A debate's evidence values become note facts, so its numbers can be traced.
- State lives in files; each call gets a purpose-built view; detail is fetched by id.

### 8.3 Failure handling and limits

| Case | Handling |
|---|---|
| Tool error | Returned to the model (§7.4) |
| Budget spent or context full | Forced answer turn, tools off |
| Answer still not JSON | Recorded as unparsed; no claims |
| Sub-agent returns nothing usable | Its Report step degrades (§9.3) |
| vLLM unreachable | The call raises; a report resumes with `--resume` |
| Determinism | Temperature 0 |
| Judge | Can't share the GPU with the 27B model, so it runs afterwards |

---

## 9. Report pipeline

How it works, and the note's ten sections: [V4_REPORT.md](stages/V4_REPORT.md). Artifact schemas: [V4_INTERFACES.md §9](V4_INTERFACES.md#9-pipeline-artifacts).

### 9.1 Contract

Run S1–S11 for one ticker and as-of, write the note, and save a version.

Code: runner (`research/pipeline.py`), sections, facts and writer (`research/note/`), trace and consistency (§10).

| Step | Kind | Reads | Writes |
|---|---|---|---|
| S1 data pack | code | Store | Every model input with its source (in memory) |
| S2 events | sub-agent | Store, search | `events.json` |
| S3 model | code | data pack, template | History model; layer-2 checks |
| S4 propose | sub-agent (off by default) | driver table, Store | Assumption overrides |
| S5 forecast | code | model, guidance, `research.yaml` | `model.json`; layer-2 again |
| S6 peers | code | Store, per peer | Peer models (model book) |
| S7 value | code | model, peers, market | `valuation.json`; valuation checks |
| S8 revise | code | the parent's `model.json` | `revisions.json` |
| S9 debates | sub-agent per scenario driver | model, Store, search | `debates.json`; section 7 sensitivities |
| S10 write | code + LLM | all artifacts | `facts.json`, `note.json`; consistency check |
| S11 export | code | all artifacts | Note, PDF, workbook, `exceptions.json`, `report.json`; the version row |

Every artifact is a JSON file in the report folder. `report.json` records the version, parent, rating, PT, counts, check results and each step's time and tokens.

### 9.2 Rules

- Fixed steps; LLM calls are leaf tasks; no Ask or Research runs (spec §2.3).
- Parent = the latest version with an earlier as-of; none → "initial draft (no prior run)".
- Revisions cover revenue, adj. EBITDA and EPS (next quarter, last two FY columns).
- `--resume` reuses saved LLM outputs; code steps always rerun.
- `--no-llm` skips every LLM step.
- Sections 1 and 10 are written last (spec §2.3).
- Section 7 has a sensitivity row for every debated driver.
- Each LLM step has a budget; a run over 30 minutes logs a warning.

**Consistency check** (before export; spec §2.3). Code checks that:
- the rating matches the return thresholds;
- section 1's numbers equal their section tables;
- every debated driver is in section 7;
- a guidance change in the events appears in 2.2;
- every open issue is in section 10.

A failure becomes an `error` exception. The takeaways get one repair; other sections are never rewritten.

### 9.3 Failure handling and limits

| Case | Handling |
|---|---|
| Layer-2 or valuation FAIL | The run stops; nothing downstream runs on unchecked numbers |
| LLM step fails or hits its budget | Section reads "failed check: reason"; exception row; the run finishes |
| Untraced number after repair | Sentence dropped, logged (§10.2) |
| Consistency failure | Exception row; takeaways repaired once |
| Over 30 minutes | Warning exception |
| LibreOffice or Chrome missing | Export fails; no version saved |
| Runtime | One ticker per run; pin to the E-cores |

---

## 10. Verification and number trace

How it works: [V4_VERIFICATION.md](stages/V4_VERIFICATION.md). Full id grammar: [V4_INTERFACES.md §10–11](V4_INTERFACES.md#10-reference-ids).

### 10.1 Contract

Make every number checkable: resolve ids, check answer claims, trace every number in the note, keep the evidence and exception log.

Code: id grammar and resolver (`research/refs.py`), claim check (`generation/verify.py`), fact list, number trace and consistency check (`research/note/`).

| Interface | Contract |
|---|---|
| **Reference ids** (10) | One grammar for every kind: chunk, accession, `xbrl:`, `q:`, `kpi:`, `guide:`, `px:`, `macro:`, `cc:`, `model:`, `E-n`, `X-n`. Data ids resolve under an as-of to a value and unit |
| **`model:` ids** | `model:<T>:<as_of>:<run>:<line>.<period>`; run = `base`, `scratch-n`, `val-…`, `var-<period>` or a saved `v<n>` |
| **Fact list** (11) | `F-n` facts: label, formatted value, unit, source id. The writer uses them verbatim, marked `{F-n}` |
| **Log** | `E-n` rows from the facts; `X-n` rows (severity `info`, `warning` or `error`) from every step |

### 10.2 Rules

**Claim status** (Ask and Research answers):

| Status | When |
|---|---|
| **verified** | The value matches a cited, resolved value within its written precision |
| **calculated** | Its calculation reruns from resolved inputs |
| **cited** | No number, valid ids |
| **unverified** | Anything else; shown with the reason |

**Note trace:**
- Code-written tables must appear verbatim.
- Every number in prose is a marked fact **of its own section** or an allowed token (dates, "Q2", "10-K").
- No "pending" or "TBD"; gaps read "n/a: …" or "failed check: …"; estimates carry "E".
- One repair turn; a sentence still untraced is dropped and logged (spec §2.3).
- Every gap, conflict or failed check becomes an `X-n` row.

### 10.3 Failure handling and limits

| Case | Handling |
|---|---|
| Cited id doesn't resolve | Claim unverified, "unknown id" |
| Value doesn't match | Unverified, with the reason |
| Untraced number in prose | One repair; then dropped, `error` logged |
| `model:` ids | Need the session's model book, or the saved version's files |
| `ixbrl:`, `E-`, `X-` ids | Parse only, not resolved (§13.1) |

---

## 11. Outputs and review

How it works: [V4_OUTPUTS.md](stages/V4_OUTPUTS.md). Folder and tabs: [V4_INTERFACES.md §12](V4_INTERFACES.md#12-outputs).

### 11.1 Contract

Write the note and workbook a reviewer reads, check the workbook against the model, and record the review.

Code: workbook and parity (`research/export/workbook.py`), other tabs and PDF (`report.py`), renderer (headless Chrome), review (`tenk research approve`).

| Interface | Contract |
|---|---|
| **Report folder** (12) | `output/reports/<TICKER>/<as_of>/`: note (`.md`, `.pdf`), workbook, artifacts; saved as a version with its parent |
| **Workbook** | Tabs: Model, Assumptions, Summary, Scenarios, Valuation, DCF, Revisions, Model (prior), Checks, Log. Inputs blue, formulas black, links green |
| **Review** | `tenk research approve TICKER:vN --by INITIALS` marks a version reviewed |

### 11.2 Rules

- Parity: LibreOffice recalculates; every formula cell equals `model.json`.
- Every version is a draft until approved; an amendment is a new version (spec §2.3).
- Ask and Research show the status beside a cited estimate, so a draft is never presented as approved.
- Market data stays in `data/` (Tiingo licence).

### 11.3 Failure handling and limits

| Case | Handling |
|---|---|
| Parity mismatch | Recorded in `report.json`; **the run does not stop** (§13.1) |
| LibreOffice or Chrome missing | Export raises; no version saved |
| Approving an unknown version | Nothing happens, no error (§13.1) |
| Parity time | Up to 300 s |

---

## 12. Evaluation

How it works, including the backtest loop: [V4_EVALUATION.md](stages/V4_EVALUATION.md). Gold rows: [V4_INTERFACES.md §13](V4_INTERFACES.md#13-gold-and-results).

### 12.1 Contract

Grade each layer against bars frozen before the code they grade.

Code: extraction scorer, backtest, note eval (`evaluation/`); Ask and Research runners (`experiments/v4/`).

| Interface | Contract |
|---|---|
| **Gold** (13) | `evaluation/gold/`: extraction (337 labels), Ask (79 questions), Research (72). Every row records who verified it |
| **Results** (13) | `evaluation/results/backtest-<label>/`; Ask and Research runs in `output/generation/<label>/` |

### 12.2 Rules: layers and bars

| Layer | Test | Pass bar |
|---|---|---|
| 1 Extraction | 337 labels | ≥ 98% exact; 100% on revenue, EPS, main KPI |
| 2 Model integrity | §6.2 checks on every model | 100%; any FAIL stops |
| 3 Forecast | Walk-forward backtest, 48 forecasts per metric | MAPE below seasonal naive on revenue and main KPI; bias near 0 |
| 4 Valuation | Rerun on identical inputs; WACC sweep | Identical output; targets inside the peer range or flagged |
| 5 Note | Trace, ten sections, judge rubric, seeded errors | 100% traced; rubric ≥ 4/5 |
| Ask | 79 questions | ≥ 85% correct and verified; 100% of ids resolve |
| Research | 72 questions | ≥ 90% with a verifying `model:` id and stated assumptions |

- Bars are frozen before the code they grade; a miss is reported as a miss.
- A gold change after a run is logged, old score beside the new.
- Backtest as-of = the day after a release's EDGAR acceptance (works for releases before the open and after the close).
- Actuals as first reported.
- Baselines: seasonal naive, guidance midpoint, an LLM with no tools (spec §6); the last also measures memorization.
- The judge is from another model family; no model grades itself.

### 12.3 Failure handling and limits

| Case | Handling |
|---|---|
| Actual not reported yet | Point skipped |
| An input filed on or after D | The backtest stops (leakage probe) |
| Judge skips a criterion | Missing from the mean |
| Sample | 48 forecasts per metric |
| Held-out quarters | None: the LLM's training cutoff is unpublished |
| Human review | Partial (§13.1) |

---

## 13. Reliability and gaps

### 13.1 Not built

**Report and note**

| Spec asks | Today |
|---|---|
| Bounded research runs for sections 2.3, 4.2, 6, 7, 8 (spec §2.3 req. 2) | 2.3 and 6 are single LLM calls without tools; 4.2, 7 and 8 have no LLM text. Only debates use tools |
| Debates with the Research toolset (spec §2.3 req. 2) | Debates use `ask`: they can't run a scenario |
| `initiation` report type (spec §2) | `update` only |
| No-parent flag "initial draft (no prior run)" | Reads "(no prior version)" |
| Section 10 repaired on a failed consistency check (req. 6) | Section 10 is code-written; only the takeaways are repaired |
| Section 3.1 driver table | Not built; section 3 has the debates only |
| Section 7: model driver and evidence columns; unquantified risks | Risk, EBITDA change and PT impact for scenario drivers only |
| Consensus column ("n/a") and a "no consensus" exception | Absent |
| Secondary multiples in section 6 (spec §3.3) | Computed in `valuation.json`; not shown |
| Outside data shown as directional checks (spec §3.2) | Used only as forecast inputs |

**Forecast and valuation**

| Spec asks | Today |
|---|---|
| Variance flag: > 2% (revenue, bookings) or > 5% (EBITDA) from the guide midpoint needs a reason (spec §3.2) | Unused; no Flag column in 2.2 |
| Later quarters by seasonal share only (spec §3.2) | A full-year-only guide anchors the FY column (ABNB) |
| DCF with 3 modeled years, then 7 faded (spec §3.3) | Quarters end at 2027Q4; 2028 on is faded |
| Relative rating: top 3 Buy, middle 2 Hold, bottom 3 Sell (spec §3.3) | Thirds: 2 / 4 / 2 |
| Release vs filing gap logged ("filing wins", spec §1.6) | No comparison of release KPIs with XBRL |
| Exception when a non-operating item exceeds 10% of NI (design) | Forecast 0 only |

**Workbook and log**

| Spec asks | Today |
|---|---|
| Summary tab: old vs new, $ and % change, source | Valuation headline; old vs new is on Revisions |
| Assumptions tab: basis, confidence, variables affected, last updated | Name and value only |
| Scenarios tab: weights, weighted value, sensitivities | Values only |
| Bridge and rating on the Valuation tab | On Summary |
| Model (prior) tab with live formulas | The parent's values |
| Revision type and source of change as separate columns; every line | One `type` column; three lines |
| Evidence fields: period, filing date, location, retrieval date, type, cell | Id, kind, label, value, source |
| Exception fields: treatment, status | Id, severity, item |
| Open exceptions carried from the parent version (design) | Not built |
| Thin peer set exception, fewer than 4 peers (spec X-04) | Not logged |

**Checks and evaluation**

| Asked | Today |
|---|---|
| `ixbrl:`, `E-`, `X-` ids resolved by the claim check (§10.1) | Parse only |
| Parity mismatch as a layer-2 FAIL (§11.2) | Recorded; the run continues |
| `tenk research approve` errors on an unknown version (§11.3) | Silently matches nothing |
| Held-out quarters reported separately (spec §6) | Not reported: the LLM's cutoff is unpublished |
| Human review of the rubric sample and the Ask and Research gold | Not done |

Working list with effort estimates: [tmp_inconsistencies.md](tmp_inconsistencies.md).

### 13.2 Known problems

| Problem | Handling |
|---|---|
| **LLM memory leaking into the backtest** | The LLM never outputs forecast numbers; the no-tools baseline (56% MAPE) shows no memorized actuals |
| **Data that changes after the fact** (restatements, recast segments, revisions) | Dated rows, `.latest()`, definition versions, ALFRED vintages, `restated` checks |
| **Structural breaks** (UBER's 2026 UK agent change) | Enters as the last y/y change and holds four quarters, lifting UBER's 2027E margin |
| **Non-operating swings** | Forecast 0; history shows reported EPS |
| **Instacart starts at Q3 2023** | Seasonal shares from fewer years, logged |
| **Thin peer sets** (3 per sector) | The sector median drives ratings; stated in V4_RESULTS |
| **Negative DCF** (TRIP) | Left out; the PT rests on the multiple |
| **Release time vs filing date** | Uses the filing date; backtest as-of = the day after acceptance |
| **Crash-prone CPU, shared GPU** | `--resume`, E-core pinning, CPU embedder |
| **Determinism with an LLM** | Valuation is code; temperature 0; `--resume` replays LLM steps |
| **Licensing** | Tiingo internal use only; Stooq, TSA, Google Trends dropped |
| **Filing-text recall is unmeasured** | A missed acquisition or regulatory event is silent ([V4_AGENT.md](stages/V4_AGENT.md#filing-text-what-reaches-the-note)) |

### 13.3 Error policy

- **Tools never raise.** A bad call returns `{"error": …}` for the model to read, so one bad argument doesn't end a run.
- **Report code steps stop the run** on a failed integrity or valuation check.
- **LLM steps degrade:** the section reads "failed check: reason" and the run continues (spec §2.3 req. 7).
- **Builders never guess:** every gap becomes an exception row.

Per-block cases: §3.3, §4.3, §5.3, §6.3, §7.4, §8.3, §9.3, §10.3, §11.3, §12.3.

### 13.4 System limits

| Limit | Value |
|---|---|
| LLM context | 32k tokens (Qwen3.6-27B-FP8 on vLLM) |
| GPU | One 46 GB L40S, shared by vLLM and the embedder; the judge runs as a second pass |
| CPU | Python crashes on the P-cores: heavy runs under `taskset -c 16-31` |
| SEC | One request per 0.12 s (SEC allows 10/s) |
| Licences | Tiingo is internal use only: market values never leave `data/` |
| Report time | Under 30 minutes per company |
| Coverage | December fiscal years; filings from 2023-01-01; CART from Q3 2023 |

---

## Appendix A. Config

| File | Holds | Pinned |
|---|---|---|
| `config.yaml` (`TENK_CONFIG`) | Retrieval, generation and agent knobs | Per experiment |
| `companies.yaml` (`TENK_COMPANIES`) | Coverage, data folder, window; per company CIK, sector, template, main KPI | Per experiment |
| `research.yaml` | Forecast, scenarios, proposer, LLM budgets, valuation | Per experiment |
| `facts/kpis.yaml`, `facts/metrics.yaml` | KPI registry; XBRL concepts per metric | With the code |
| `research/model/templates/*.yaml` | Model templates | With the code |

**Key defaults:**
- **Agent:** 20 calls, 20k context, 16,000-character tool results.
- **Retrieval:** 40 candidates per search, RRF k = 60, 8 chunks.
- **Forecast:** horizon 2027Q4; fade 0.9 (bounds 0.75–1.0); guidance midpoint; ratio bands 50 bps (take rate) and 200 bps (margins); tax 10–30%; outside inputs on (weight 0.5, at least 6 quarters).
- **Scenarios:** weights 25/50/25; shocks ±1σ.
- **Valuation:** 60/40 multiple/DCF; target year 2027; scenario multiples ±15%; terminal growth 3% (grid 2.5–3.5%); 10 DCF years; debt-weight threshold 10%; Buy +15%, Sell −10%.

**Credentials.** Each user types in their own with `tenk setup` (`credentials.yaml`, gitignored). Environment variables take precedence. A missing one stops the command with an error naming it. `data/` and `output/` are never committed.

## Appendix B. CLI

| Command | Does |
|---|---|
| `tenk companies verify` | Resolve CIKs, year-ends, filing counts |
| `tenk fetch [--forms …] [--market]` | Fetch filings; `--market`: prices, macro, ERP |
| `tenk build --stage chunk,load,append,embed,pages,facts,market,kpis` | Build stages |
| `tenk research report TICKER… --as-of D [--no-llm] [--resume]` | Report mode |
| `tenk research approve TICKER:vN --by INITIALS` | Mark a version reviewed |
| `tenk setup` | Type in your credentials |
| `tenk tool NAME key=value …` | Run one tool by hand (`--list`: schemas) |
| `python -m tenk_agent.research.{model.history,forecast,valuation} TICKER… AS_OF` | One stage alone (`valuation --all`: relative ratings) |
| `python -m tenk_agent.evaluation.backtest [--label] [--proposer] [--long]` | Backtest and ablations |
| `python -m tenk_agent.evaluation.note_eval FOLDER…` | Layer 5 |
| `experiments/v4/ask_run.py`, `research_run.py` | Ask and Research gold runs |

## Appendix C. Code layout

```text
src/tenk_agent/
  asof.py corpus.py store.py models.py cli.py
  acquisition/   edgar.py filings.py companies.py market.py crosscheck.py insiders.py
  preprocess/    forms.py rules.py blocks.py structure.py validation.py
  chunking/ retrieval/ generation/                      (V0–V2, shared)
  facts/         xbrl.py quarters.py extract.py rules.py kpis.yaml metrics.yaml
  research/      refs.py book.py datapack.py subagents.py propose.py forecast.py outside.py valuation.py pipeline.py
                 model/   dsl.py graph.py history.py checks.py templates/
                 note/    facts.py sections.py write.py trace.py consistency.py
                 export/  workbook.py report.py
  agent/         tools.py loop.py sec.py
  evaluation/    extraction.py backtest.py note_eval.py render.py (+ V0–V3)
  prompts/       ask.md research.md extract.md propose.md events.md debates.md section.md judge.md
docs/
  V4_INTERFACES.md   full interface schemas (§2.3)
  stages/V4_*.md     how each block works (companions to §3–§12)
```
