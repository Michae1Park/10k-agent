# V4 technical design: a junior sell-side research agent

> **What this is:** the build contract for V4. The [spec](V4_SPEC.md) says what to build and why; this doc says how: components, interfaces, rules, algorithms, and what happens when things fail. Tunable values live in config (Appendix A); results and gaps in [V4_RESULTS.md](V4_RESULTS.md).

| For | Read |
|---|---|
| Full interface schemas | [V4_INTERFACES.md](V4_INTERFACES.md) |
| How each block works, and its tests | [stages/V4_*.md](stages/), linked from each section |
| One run followed end to end | [V4_WORKED_EXAMPLE.md](stages/V4_WORKED_EXAMPLE.md) |
| Model equations | [V4_FINANCIAL_MODELING.md](stages/V4_FINANCIAL_MODELING.md) |
| Results | [V4_RESULTS.md](V4_RESULTS.md) |

**How to read it.** Top down: §1 is the scope; §2 gives the components, interfaces and cross-cutting rules. §3–§12 take one block each, data first; §5, the Toolbox, is the agent's view of the blocks in §3, §4, §6, §7 and §11, so it refers ahead to §6 and §7. Each has three parts: **contract, rules, failure handling and limits.** §12 is evaluation; §13 is reliability. "Spec §n" is a section of the spec; a bare "§n" is a section of this doc.

*Updated 2026-11-03.*

---

## 1. Scope

The system serves four entry points, each at an as-of date, over eight companies (spec §2):

| Mode | In | Out |
|---|---|---|
| **Ask** | A question + as-of | A short cited answer from filed data |
| **Research** | A question + as-of | An answer computed by the model, assumptions shown |
| **Investigate** | A multi-part question + as-of | A plan of Ask and Research sub-runs and one combined, claim-checked answer |
| **Report** | Ticker + as-of | A 10-section draft note, a model-change workbook, an evidence and exception log |

- **Companies:** BKNG, EXPE, ABNB, TRIP (travel) · UBER, LYFT, DASH, CART (mobility and delivery).
- **Reuse:** V4 builds on V0–V3's parsing, chunking, retrieval, agent loop and claim check; V3 stays runnable as a regression check.
- **Terms:** Appendix D.

---

## 2. System architecture

### 2.1 Component map

<p align="center">
  <img src="img/v4-components.svg" alt="V4 component map. External sources feed Acquisition, which writes a raw cache. Text processing and Facts build the Store, one SQLite file read only through an AsOf filter. Retrieval and the Model engine read the Store. The Agent runtime (loop, prompts, sub-agents) calls the Toolbox, which reaches Retrieval, the Store and the Model engine. Ask and Research are two modes of the Agent runtime; the Report pipeline runs fixed steps, calls the Model engine and bounded sub-agents, and its artifacts pass Verification and Export into a note, workbook and log. Saved versions go back to the Store. Evaluation reads gold sets and every layer's outputs. Components that call the LLM are marked." width="100%">
</p>

Green, pink and yellow boxes and the Evaluation panel are components (§2.2); purple boxes are data, blue ones are outside the system, grey Config is files. Numbered arrows are interfaces (§2.3). Pink marks a component that calls the LLM.

### 2.2 Components

| Component | Does | LLM | § |
|---|---|:-:|---|
| **Acquisition** | Fetches filings, XBRL, prices, macro, ERP, cross-checks and Form 4 into a raw cache | | §3 |
| **Text processing** | Turns filings into Items, chunks and vectors (by rules) | | §3 |
| **Facts** | Loads XBRL facts; derives quarters on read | | §3 |
| **Store** | One SQLite file; every read filtered by the run's as-of | | §3 |
| **Extraction** | KPI and guidance rows from releases, each checked against a quote | ✓ | §4 |
| **Toolbox** | The tools the agent calls: its view of the other blocks | | §5 |
| **Retrieval** | Hybrid search over chunks | | §6 |
| **Model engine** | Model, checks, forecast, scenarios, valuation | | §7 |
| **Agent runtime** | The tool-calling loop, prompts and budgets: Ask, Research, sub-agents; the Investigate planner | ✓ | §8 |
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
| 3 | **Search** | Toolbox → Retrieval | §6.1 | [§3](V4_INTERFACES.md#3-search) |
| 4 | **Tool calls** | agent ↔ Toolbox | §5 | [§4](V4_INTERFACES.md#4-tool-calls) |
| 5 | **LLM calls** | code ↔ model | §8.1 | [§5](V4_INTERFACES.md#5-llm-calls) |
| 6 | **Extraction rows** | extractor → Store | §4.1 | [§6](V4_INTERFACES.md#6-extraction-rows) |
| 7 | **Templates** | config → Model engine | §7.1 | [§7](V4_INTERFACES.md#7-templates) |
| 8 | **Model and valuation files** | Model engine → Report, tools, Export | §7.1 | [§8](V4_INTERFACES.md#8-model-and-valuation-files) |
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
| **Research** | The agent, with tool set `research` (adds model tools) | Model tools; the calculator only combines their results |
| **Investigate** | The planner: sub-questions, each an Ask or Research run, one replan | The sub-runs; the combiner only cites and calculates over them |
| **Report** | Code: fixed steps S1–S11; LLM calls are leaf tasks | The Model engine only |

- Ask and Research are one agent with two tool sets.
- Investigate is the only mode that plans: it runs Ask and Research as sub-runs and combines their checked answers (§8.4). It never starts a Report.
- **Report steps S1–S11** (full table in §9.1): S1 data pack · S2 events · S3 history · S4 propose · S5 forecast · S6 peers · S7 value · S8 revise · S9 debates · S10 write · S11 export. The Model engine (§7) implements S1 and S3–S7; S4 is its optional LLM step.
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

1. Extracted rows that code checks against a verbatim quote (§4.2).
2. Bounded assumption proposals that code clips (§7.2).
3. Prose in which every number is a marked fact (§10.2).

Tables are always written by code (spec §3). This keeps valuation reruns byte-identical.

**Degrade, don't stop.** A failed LLM step leaves "failed check: reason" and the run continues; only an integrity or valuation failure stops a run (§13.1).

**Artifacts, not transcripts.** Steps hand over JSON files (§9.1), never chat history; each LLM call gets a purpose-built view and fetches detail by id.

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

**Build lanes** (`tenk fetch`, then `tenk build`):

| Source | Fetch | Build → table |
|---|---|---|
| EDGAR submissions + filing index | 10-K, 10-Q, 8-K Item 2.02 (`EX-99.*`), DEF 14A per company; one folder per filing | `filings` |
| Filing HTML | – | Rule labels (Item and part headings, headings, tables, body) → Items → parent-child chunks (parents never cross a subsection; each table its own chunk) → FTS5 + embeddings over children → `chunks` |
| EDGAR companyfacts | JSON per company | Consolidated us-gaap, dei, srt facts with their filing → `facts` |
| Inline XBRL (filing HTML) | – | `ix:nonFraction` facts with dimensions or company concepts → `facts` |
| Releases | release chunks (from the Filing HTML lane) | LLM extraction (§4) → `kpis`, `guidance` |
| Tiingo, FRED/ALFRED, Damodaran | Daily closes (tickers, SPY); macro with every vintage; monthly ERP | `market`, each row with its publish date (§2.5) |
| BTS, NYC TLC | Monthly series; TLC trip files aggregated per license at fetch | `market` (`cc:` series) |
| Form 4 | Insider filings | `insider_tx` |

**Quarters on read** (`facts/quarters.py`): per metric, the concepts from `metrics.yaml`; per fiscal year, the first concept with facts. Facts are bucketed by span (quarter, H1, 9M, FY; year-to-date spans start 1 January), each the newest copy visible at D. Then Q1 direct; Q2 and Q3 direct, else YTD − prior YTD; Q4 = FY − 9M; instants at the quarter end. Each row keeps its input fact ids and the latest filing date among them.

**Rules:**

- One filing per form and period; amendments are ignored.
- Releases are 8-K Item 2.02, every `EX-99.*` exhibit (a release can carry more than one).
- A release's period is the last quarter end before it was filed, checked against the 10-Q (releases carry no XBRL period).
- DEF 14A: the first proxy each year, keyed to the prior fiscal year; later ones are special meetings (spec §1.2).
- Fetch cache-first; builds run offline.
- Filings are split into Items by rules, not the LLM.
- Inline XBRL is read for every company-specific and segment fact; company facts has neither.
- Quarters are derived on read, never stored: Q4 = FY − 9M; Q2 and Q3 from year-to-date when needed (spec §1.6).
- A metric uses one XBRL concept per fiscal year.
- A year recast by a later filing reads `restated`, logged, not `fail` (spec §1.6).
- EPS and share counts: direct facts only; Q4 EPS from the release.
- Sources that block scripts are dropped, never worked around.

### 3.3 Failure handling and limits

| Case | Handling |
|---|---|
| Source unreachable | The cached copy is used |
| A company's fetch fails | Logged; the others continue |
| A release's period disagrees with its 10-Q, or two filings claim one period | Fetch error, flagged |
| Missing concept or quarter | Blank; logged when the model reads it |
| Item structure not found | Reported; the filing keeps the Items found |
| Image-only release | Text from the hidden text layer; no tables |
| Provider rate or size limits | Throttled and cached; large series aggregated at fetch |

---

## 4. Extraction (layer 1)

How it works: [V4_EXTRACTION.md](stages/V4_EXTRACTION.md). Schemas: [V4_INTERFACES.md §6](V4_INTERFACES.md#6-extraction-rows).

### 4.1 Contract

Read KPIs and guidance from earnings releases into checked, quoted rows.

Extraction is a second pass that runs after chunking: it reads the release chunks the data layer (§3) already built, not the raw filing. Only releases are read: they are the one source that carries every company's operating KPIs (bookings, volume, segments) and next-quarter guidance, while figures 10-Ks and 10-Qs tag come from XBRL (spec §1.5).

Code: LLM extractor (`facts/extract.py`), rules extractor (`facts/rules.py`, an ablation), registry (`facts/kpis.yaml`), loader (`tenk build --stage kpis`).

| Interface | Contract |
|---|---|
| **Registry** | Per company: each KPI's key, unit, segments and labels (from spec §1.5) |
| **Reply** | KPI and guidance rows, each with value, unit, source chunk id and a verbatim quote |
| **Extraction rows** (6) | Stored in `kpis` and `guidance` with period, filing date and definition version. Ids `kpi:<T>:<metric>[.<segment>]:<period>`, `guide:<T>:<metric>:<period>` |

### 4.2 Rules

**Flow** (per release, `facts/extract.py`):

```text
release chunks → order: tables first, then chunks naming the most registry labels → cut to the input cap
  → one LLM call (extract.md): chunks with ids + the company's registry
  → rows {metric, segment, period, value, unit, chunk_id, quote} and guides {…, low, high, basis}
  → code validates each row: chunk id given · quote verbatim (whitespace normalized)
                              · value found in the quote, unit-aware · metric and segment registered
  → failed rows → one repair turn with the reasons and the chunk text around the number
  → still failing → exception rows; passing rows → kpis / guidance with filing date and definition version
```

**Rules:**

- Input is only that release's chunks; no other filing can leak in.
- Code keeps a row only if the chunk id was given, the quote is verbatim, the value is in the quote, and the metric is in the registry. The LLM proposes; code decides what is stored.
- One repair turn, then an exception row; never a guessed value (spec §2.3).
- GAAP diluted EPS only.
- A recast gives earlier periods a new definition version; readers take the newest (spec §1.6).
- Guidance keeps its basis (level, growth or margin); the forecast converts it.
- Where a KPI is also an XBRL fact, the filing wins and the gap is logged (spec §1.6). Not built yet.

### 4.3 Failure handling and limits

| Case | Handling |
|---|---|
| Row fails after repair | Exception row; never stored |
| Release with no valid rows | Its KPIs are missing; the model logs the gap |
| No numeric guidance | Guidance baseline reads "n/a" |
| Input size | Capped per release; long releases send matching chunks first |

---

## 5. Toolbox

How it works: [V4_AGENT.md](stages/V4_AGENT.md#tools). Arguments and result schemas: [V4_INTERFACES.md §4](V4_INTERFACES.md#4-tool-calls).

### 5.1 Contract

A tool is a function the agent calls with JSON arguments; it returns JSON with citable ids. The **Toolbox** (`agent/tools.py`) holds the schemas, runs the calls, and sees exactly what its store's as-of sees. Each tool is a thin front for one block (§3, §4, §6, §7, §11); the Toolbox adds no logic of its own.

| Caller | Uses |
|---|---|
| Ask | tool set `ask` |
| Research | tool set `research` |
| Investigate | its sub-runs use `ask` or `research`; the planner and combiner call no tools |
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

### 5.2 Catalog

Every tool shares the same handling:

- **Company:** a ticker or name, resolved against the corpus.
- **Periods:** the periods asked for, else the latest eight, in order (FY after Q4).
- **Caps:** a row cap (Appendix A), the latest kept, with `"omitted": n`.
- **Values:** ratios shown in percent.
- **Arguments:** object and array arguments written as JSON strings are parsed.

Result schemas: [V4_INTERFACES.md §4](V4_INTERFACES.md#4-tool-calls). The model tools compute through the Model engine (§7); their equations are in [V4_FINANCIAL_MODELING.md](stages/V4_FINANCIAL_MODELING.md) (scenarios §4, valuation §5).

#### text · `search_filings`

Searches filing text (10-K, 10-Q, releases) for the passages that answer a query, for one or more companies.

| | |
|---|---|
| **Consumes** | `chunks`, `children`, FTS5 and vectors (Store) |
| **Does** | Resolves companies (`["all"]` = every company with a filing), checks the period keys, and runs Retrieval (§6.2) |
| **Returns** | The sub-searches run; chunks with id, company, form, period, filing date, item and text |
| **Errors** | Unknown company (lists the corpus); no company given or named; bad period key |

#### calc · `calculator`

Does arithmetic on numbers the agent already holds, so every derived figure comes from code and can be rerun by the claim check.

| | |
|---|---|
| **Consumes** | Only its inputs |
| **Does** | A named op (`pct_change`, `cagr`, `ratio`, `percent_of`, `difference`, `sum`) or an expression over named inputs (`+ − × ÷ **`, `abs`, `round`, `min`, `max`, `sqrt`). The expression is parsed to a syntax tree; there is no `eval` |
| **Returns** | Result, formula, inputs: the same calculator reruns the claim check (§10.2) |
| **Errors** | Unknown op, missing inputs, bad expression, non-finite result |

#### facts · `financials`

Returns reported quarterly and annual figures (revenue, net income, balance-sheet items) with their derivation.

| | |
|---|---|
| **Consumes** | `facts`; `facts/metrics.yaml` (each metric's concepts and kind, flow or instant) |
| **Does** | Quarters derived on read (§3.2). A flow uses the direct quarter fact; otherwise Q2 or Q3 = YTD − prior YTD (`ytd_diff`) and Q4 = FY − 9M (`fy_minus_9m`). An instant uses the quarter-end fact. Each row keeps its input ids and `filed` = the latest input; it is flagged `restated` if a later filing recast it |
| **Returns** | Rows: id (`q:` or `xbrl:`), period, value, unit, derivation, `restated?` |
| **Errors** | Unknown metric (lists the metrics) |

#### facts · `company_kpis`

Returns a company's operating KPIs (bookings, volume, segments) as extracted from earnings releases, each with its quote.

| | |
|---|---|
| **Consumes** | `kpis` |
| **Does** | Per metric, segment and period, the latest-filed row (the newest definition version); optional segment filter |
| **Returns** | Rows: `kpi:` id, metric, segment, period, value, unit, chunk id, quote |
| **Errors** | No such KPI (lists the reported ones) |

#### facts · `guidance`

Returns management's guidance for a metric, each guide paired with the actual once it is reported.

| | |
|---|---|
| **Consumes** | `guidance`; `kpis` for actuals |
| **Does** | Every guide, oldest issue first, so an update follows the guide it replaces. A `level` guide gets the actual: its `kpi:` id for the target period, resolved at D |
| **Returns** | Rows: `guide:` id, period, low, high, unit, basis, issued in, quote, `actual?`, `actual_id?` |
| **Errors** | No guidance for the metric (lists the guided ones) |

#### facts · `filings`

Lists a company's filings visible at the as-of, with form, period and filing date.

| | |
|---|---|
| **Consumes** | `filings` |
| **Does** | The company's filings (optionally one form), by filing date; the latest, up to the row cap |
| **Returns** | Accession (the id), form, period, period end, filing date; total count |

#### market · `market_data`

Returns a company's closing prices or a macro series (rates, CPI and so on).

| | |
|---|---|
| **Consumes** | `market` (`px:<T>:close`, `px:SPY:close`, `macro:<SERIES>`) |
| **Does** | A company's close or a macro series, not both. `start`: every point from then; `dates`: the last point on or before each; neither: the latest point |
| **Returns** | Rows: `px:` or `macro:` id, date, value; the series unit |
| **Errors** | Both or neither of company and series; no data for the series |

#### market · `crosscheck`

Returns outside demand data (NYC TLC trips for Uber and Lyft, BTS airline passengers) to sanity-check company-reported figures.

| | |
|---|---|
| **Consumes** | `market` (`cc:` series) |
| **Does** | `tlc`: NYC trips for Uber (`HV0003`) and Lyft (`HV0005`) only; `bts`: airline passengers, total, domestic and international. Window by month from `start` to `end`, else the latest year |
| **Returns** | Rows: `cc:` id, series, month, value; coverage note; unit (trips or passengers) |
| **Errors** | TLC for another company; a series other than `tlc` or `bts` |

#### reports · `research_report`

Returns the latest saved Report version visible at the as-of: its estimates, price target and rating, and whether it is a draft or reviewed.

| | |
|---|---|
| **Consumes** | `model_versions`; the saved version's `model.json` and `valuation.json` (§11) |
| **Does** | The latest version with `as_of` < D (and ≤ the `as_of` argument, which only narrows). Defaults: the headline lines and the forecast FY columns. Each value is marked `estimate` or `actual`. Valuation gives price, PT, return and rating. The status note says draft or reviewed |
| **Returns** | Version, as-of, status, parent, note, valuation and estimates with `model:<T>:<as_of>:v<n>:…` ids; visible versions. None → `report: null` with the visible versions |
| **Errors** | Unknown lines (listed with the valid ones; the rest still returned) |

#### model · `model_view`

Shows the base forecast model for a company: its lines, forecast and the assumptions behind it.

| | |
|---|---|
| **Consumes** | The model book's base forecast for the company (§7), built on first use |
| **Does** | Defaults: the driver build and headline lines; recent history, the near forecast and the FY columns. Each assumption is listed with its default and its source: the anchoring guide's id, else `default` |
| **Returns** | Rows of values per period, assumptions, the model's exceptions, and the `model:<T>:<as_of>:base:…` id format |
| **Errors** | Unknown lines (lists the lines) |

#### model · `run_scenario`

Reruns the forecast and valuation with changed assumptions and compares the result with the base case.

| | |
|---|---|
| **Consumes** | The base forecast: its model, data pack and assumptions |
| **Does** | Changes = `overrides` plus base + `deltas`. Only proposable drivers (growth starts, ratio shifts) and `fade` are allowed. Each change is clipped to the proposer's bounds (§7.2). The model is rebuilt on the same data pack with the changed assumptions, saved as `scratch-n`, and valued as §7.2 with the same peers |
| **Returns** | Headline lines for the last FY columns, base vs scenario; PT and rating, base vs scenario; the clipped drivers; ids for both runs |
| **Errors** | Unknown driver (lists the allowed ones); no change given |

#### model · `compare_peers`

Ranks companies side by side on a valuation field or model line.

| | |
|---|---|
| **Consumes** | Each company's base forecast and valuation, built on first use (the first call is slow) |
| **Does** | `metric` is a valuation field (`ev_to_ebitda`, `ev_to_ebitda_less_sbc`, `pe`, `fcf_yield`, `price_target`, `return`, `rating`, `ev`, `market_cap`) or a model line at `period` (default the target year). Companies default to all. Rows are sorted ascending, non-numbers last |
| **Returns** | Rows: company, value, unit, `model:` id, its main growth and shift assumptions with their source |
| **Errors** | Unknown company; unknown metric (lists lines and valuation fields) |

#### model · `variance_review`

Compares the forecast made before a release with the reported actual, and splits the revenue gap into its causes.

| | |
|---|---|
| **Consumes** | The model book at D (actuals) and at an earlier as-of (the estimate) |
| **Does** | `period` must be reported at D. The estimate is the base forecast made the day after the previous quarter's release (the backtest's as-of). Each main line gives estimate vs actual and `variance_pct` = actual / estimate − 1. The revenue bridge splits the variance: gross value (GB₁ − GB₀) × TR₀ plus take rate GB₁ × (TR₁ − TR₀). Where the template has them, gross value is split again into volume (V₁ − V₀) × P₀ × TR₀ and value per unit V₁ × (P₁ − P₀) × TR₀ |
| **Returns** | Rows with `estimate_id` and `actual_id`; the bridge in USD millions with `var-<period>` ids; the estimate's assumptions |
| **Errors** | Period not yet reported |

### 5.3 Rules

- Every tool is bound to the run's as-of (§2.5).
- Every value in a result carries an id that resolves (§10.1), so the claim check can verify what the model cites.
- Errors are results: `{"error": …}` plus the valid choices.
- Tools come in groups; no run gets every tool.
- Results are capped in rows and characters (§5.2, Appendix A).
- Ask cites estimates only through `research_report`; Research's new numbers come only from model tools (the calculator only combines their results), assumptions listed (spec §2).
- `run_scenario` writes a session-only `scratch-n` run, clipped to the proposer's bounds (§7.2). Only Report creates versions.
- Model tools read the model book: each company's base forecast with default assumptions (proposer off, no LLM), built once per ticker and as-of. Peers come from the same book.

### 5.4 Failure handling and limits

| Case | Handling |
|---|---|
| Unknown tool, argument, metric, line or assumption | Error with the valid choices; the model retries |
| Unknown company, or a search naming none | Error listing the corpus |
| Nothing visible at the as-of | Empty rows (or `report: null`), not an error |
| A model can't be built | Error naming the failing check (§7.3) |
| Result over the caps | Truncated, with `"omitted": n` |
| First `compare_peers` call | Slow: peers are built on first use |

---

## 6. Retrieval

How it works: [V4_RETRIEVAL.md](stages/V4_RETRIEVAL.md) and [V1_RETRIEVAL.md](stages/V1_RETRIEVAL.md). Schema: [V4_INTERFACES.md §3](V4_INTERFACES.md#3-search).

### 6.1 Contract

Find the chunks that answer a query, one search per company and filing, under the run's as-of. No LLM.

Code (`retrieval/`): decomposer, searcher, index (Qwen3-Embedding-0.6B, FTS5), reranker (bge-reranker-v2-m3).

| Interface | Contract |
|---|---|
| **Search** (3) | `search_filings(query, companies?, periods?, forms?, k = 8)` → the searches it ran, and up to k chunks with id, company, form, period, filing date and text |

### 6.2 Rules and algorithm

```text
query → decompose: one sub-question per company (and filing slot)
      → per sub-question, over child chunks filtered by company, period, form and as-of:
          BM25 (FTS5) top N  +  dense (cosine) top N
      → RRF fusion, score = Σ 1 / (k_rrf + rank) → top N → cross-encoder rerank
      → each child swapped for its parent, best child first, each parent once
      → round-robin merge → top k
```

- **Decomposition is rule-based:** companies come from the arguments, else from the tickers, names and aliases in the query. Each sub-question is rewritten to name only its company.
- No period = the latest filing of each form at D.
- The model sees parent chunks: a whole subsection or table, not a fragment.
- **Merge:** each sub-question gets ⌈k / n⌉ places (at least 2). In every round the sub-question whose best chunk scored highest goes first; duplicates are dropped.

### 6.3 Failure handling and limits

| Case | Handling |
|---|---|
| No company, or an unknown one | Error listing the corpus |
| Bad period key | Error |
| No filing for a slot at that as-of | Fewer chunks, not an error |
| GPU held by vLLM | Embedder and reranker run on CPU |
| Size | k chunks per call (default 8), each a bounded parent |

---

## 7. Model engine and valuation

How it works: [V4_MODEL.md](stages/V4_MODEL.md). Equations: [V4_FINANCIAL_MODELING.md](stages/V4_FINANCIAL_MODELING.md). Schemas: [V4_INTERFACES.md §7–8](V4_INTERFACES.md#7-templates).

### 7.1 Contract

Build a three-statement model from the data pack (the reported numbers, read from the Store, §3), forecast it, and value it. Deterministic code, except the optional S4 proposer, an LLM sub-agent (§8).

**How the pieces connect.** Two files are inputs you edit (a template and `research.yaml`) and two are outputs the engine writes (`model.json`, `valuation.json`). The DSL is the formula language inside the templates.

<p align="center">
  <img src="img/v4-model-engine.svg" alt="Model engine. The data pack (reported numbers from the Store, with source ids and filing dates) and a template (YAML lines whose formulas are written in the DSL) feed S3 history, which fills inputs from the data pack, evaluates formulas with the DSL and runs layer-2 checks. S5 forecast sets assumptions from defaults, outside data, the proposer and guidance anchors, then evaluates the forecast formulas; research.yaml supplies fade, bounds and tax range. S6 and S7 value the model with peers, prices, rates and research.yaml's weights and thresholds. The model becomes model.json and, with the same expressions written as Excel formulas, workbook.xlsx; the valuation becomes valuation.json." width="100%">
</p>

- **Templates say what the model is; the DSL is how each formula in them is written.** One expression is evaluated by Python into `model.json` and written as a formula in the workbook.
- **`research.yaml` holds numbers, not structure.** It sets what the formulas read (`@fade`, ratio bounds, tax range) and the valuation weights and rating thresholds.
- **`model.json` is the result of S3 and S5; `valuation.json` is the result of S7**, which reads `model.json`, not the templates.
- **The Toolbox's model tools (§5.3) run a lighter version of the same path:** base forecasts with default assumptions, no proposer, held in memory in the model book, with no workbook, PDF or saved version. Their `model:` ids resolve only inside that session (§10.3). Valuation always runs on a forecast; a forecast can run without valuation.

Code (`research/`): data pack (S1), DSL and graph, templates, history (S3), layer-2 checks, forecast and scenarios (S5), outside inputs, proposer (S4: optional, an LLM sub-agent whose code is listed in §8.1), valuation (S6–S7), model book (`book.py`: the base forecasts the Toolbox's model tools read, §5.3).

"S1…S7" are steps of the Report pipeline (§9.1 lists all eleven; §2.4 has the one-line key). The Model engine implements S1 and S3–S7 (S4 is its one LLM step); S2 (events) and S8–S11 are Report-only and not covered here.

| Interface | Role | Contract |
|---|---|---|
| **Templates** (7) | Input: the model's definition | YAML lines: inputs (history from a source id), formulas and forecast expressions, plus drivers, scenarios and anchors |
| **DSL** | The formula language inside templates | Restricted formula expressions (arithmetic, lags, fiscal-year sums, fallbacks, `@assumption`), parsed and never run as code; anything else is rejected. Vocabulary: [V4_INTERFACES.md §7](V4_INTERFACES.md#7-templates) |
| **`research.yaml`** | Input: the knobs | Every forecast and valuation knob (Appendix A) |
| **`model.json`** (8) | Output of S3 and S5 | Every cell (line × period) with value and kind. **Input cells carry their source id and filing date** |
| **`valuation.json`** (8) | Output of S7 | Market cap, EV, cost of capital, peers, PT per scenario, weighted PT, return, rating, DCF grid, `inputs_hash` |

### 7.2 Rules and algorithms

**Model** (spec §3.1)

```text
S1 data pack   the template's input sources (q:<metric>, kpi:<metric>[.<segment>]) → per quarter and FY:
               value, citable id, filing date; money in USD millions; per-share points put on the
               latest visible split basis (flagged split_adjusted)
S3 history     each input line takes its source, else its fallback source for periods the first lacks;
               formula lines evaluated by the DSL; layer-2 checks
S5 forecast    defaults from history → outside nudge → proposer (if on) → anchors → scenarios
```


| Template | Companies | Revenue build |
|---|---|---|
| `marketplace` | BKNG, EXPE, ABNB | Room nights × bookings per night = GB; revenue = GB × take rate |
| `segments` | UBER | Per segment: GB × its take rate; costs a share of total GB |
| `orders` | DASH, LYFT, CART | Orders or rides × value per unit; revenue = that × take rate |
| `trip` | TRIP | Revenue on one faded rate; EBITDA a margin on revenue |

- One definition (templates + DSL), two evaluators; Python is the source of truth.
- Condensed statements, shared by all templates.
- Tax at the trailing rate, within configured bounds.
- Every input cell carries its source and filing date.
- Missing inputs stay blank, never 0.
- Non-operating items forecast at 0.
- Travel prepayment is not modeled (spec §3.1).

**Forecast** (spec §3.2)

```text
first forecast quarter   Driver(q) = Driver(q-4) × (1 + g), anchored to the guidance midpoint
later quarters           Driver(q) = last FY × (1 + g) × seasonal share of q (mean of recent years)
fade                     g(t+1) = g(t) × fade
ratios                   same quarter last year + a shift held for 4 quarters, then 0;
                         bounded to the 8-quarter range ± a band per ratio
```

- Forecast per driver, never per total (spec §3.2).
- A ratio's shift holds until lapped, then 0; a one-off change is not compounded.
- Each guide anchors its own driver, upstream first (bookings → revenue → EBITDA); later anchors see earlier ones.
- Anchor at the guidance midpoint (spec §7).
- An anchor the bounds stop is logged, never forced.
- **Solving an anchor:** the guide (latest visible for the first forecast quarter, or its FY for a `period: fy` anchor) gives a target: the midpoint, × the prior-year value for a growth guide, / 100 for a margin. The anchored assumption is bracketed by stepping out from its default (doubling) until the gap changes sign, then bisected; the model keeps the best value. A driver already solved is not moved again; template order sets priority.
- Scenarios move each driver by ±1σ of its history.
- **Outside nudge** (spec §3.2): per mapped `<line>.g0`, the series' y/y over a 3-month window at lag k before the first forecast quarter; an OLS fit of the driver's history y/y on the series at the same lag; slope ≤ 0 → unused; g0 = (1 − w) × default + w × fit, w = weight × R². Guidance anchors override it.

**Proposer (S4).** Switched in `research.yaml` (off by default). When on:
- **Input:** a driver table (no data pack). Each proposable driver has its last 8 quarters, default and bounds; anchored drivers are listed as fixed.
- **Proposable:** growth starts (`.g0`), ratio shifts (`.shift0`) and `fade`, never one a guide anchors (the anchor runs after and would undo it).
- **Bounds:** each driver's bounds derive from its recent history; the fade's are in `research.yaml` (Appendix A).
- **Kept:** a proposal with a number and at least one evidence id that resolves at D. It is clipped to its bounds (logged) and stored with its rationale and evidence.
- **Dropped:** anything else, logged. A reply with no JSON means defaults.

**Valuation and rating** (spec §3.3)

| Item | Rule |
|---|---|
| Market cap | Last close before D × latest diluted shares |
| EV | Market cap + debt − cash and short-term investments (leases, stakes, customer funds excluded and logged) |
| Peers | Same-sector companies, default assumptions, same as-of |
| Multiple PT | Peer-median target-year EV/EBITDA × our target-year adj. EBITDA → per share; bear/bull ∓/± a set step; a target outside the peer range is flagged |
| DCF | Unlevered FCF = adj. EBITDA − SBC − tax − capex; modeled years, then faded to the DCF horizon; Gordon terminal |
| Cost of capital | Ke = DGS10 + β × ERP; β from monthly returns vs SPY; WACC = Ke when debt is under a set share of capital |
| Price target | Weighted multiple and DCF targets per scenario, then scenario-weighted; no roll-forward |
| Rating | Return vs the Buy and Sell thresholds, else Hold; relative ranking only with `--all` |
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

### 7.3 Failure handling and limits

| Case | Handling |
|---|---|
| Layer-2 or valuation check FAIL | The run stops, naming the check |
| Missing input | Blank, logged |
| Volume KPI not disclosed | Template falls back one level up, logged |
| Anchor out of bounds | Logged, not forced |
| Fewer than 3 years for seasonal shares | Fewer years, logged |
| DCF value ≤ 0 | Left out of the PT, logged |
| Too few monthly returns | β = 1, logged |
| Proposer fails | Defaults |
| Outside series too short or wrong-signed | Not used; logged |
| Scope | Horizon Q4 of the as-of year + 1 (2027Q4 at a 2026 as-of; DCF years beyond); condensed statements; no deferred bookings; three peers per sector |

---

## 8. Agent runtime

How it works: [V4_AGENT.md](stages/V4_AGENT.md). Reply schemas: [V4_INTERFACES.md §5](V4_INTERFACES.md#5-llm-calls).

### 8.1 Contract

Run the LLM: the Ask and Research loop, Report's sub-agents, and every single-shot call. Each reply is one JSON object.

Code: the loop (`agent/loop.py`, LangGraph), the Investigate planner (`agent/plan.py`), prompts (`prompts/*.md`), sub-agents (`research/subagents.py`, `propose.py`), and one model interface (`models.py`: vLLM or Claude).

**LLM calls** (5):

| Call | Tools | Budget |
|---|---|---|
| Ask / Research answer | `ask` / `research` | Calls and context |
| Investigate plan and replan | – | 1 call each, 1 retry; ≤ 5 sub-questions + ≤ 2 follow-ups |
| Investigate combine | – | 1 call, 1 retry; input capped |
| KPI extraction | – | 1 call + 1 repair per release |
| S2 events | `ask` | Calls and context |
| S4 proposer | `ask` | Calls and context |
| S9 debate, per driver | `ask` | Calls and context |
| S10 prose slot | – | Output tokens, 1 repair |
| Judge (evaluation) | – | Another model family, a second pass |

Budget values: Appendix A.

### 8.2 Rules

**Context per call:** a system prompt (`prompts/<mode>.md`) with the as-of, the tool schemas of the run's set, the question, then tool results as they come. Nothing else is carried between runs. The forced answer fires when calls reach the budget or the last call's input tokens reach the context cap. Sub-agents get a purpose-built view: S2 the ticker and quarter; S4 the driver table; S9 one driver and its value.


```text
question → model → tool calls → tools → model → … → JSON answer → claim check (§10.2)
```

- The first turn must be a tool call; no answers from memory.
- A repeated identical call errors and still counts.
- After the call budget or a full context: one turn with tools off to answer.
- An unparseable answer gets one retry.
- Every answer passes the claim check before it is shown.
- Sub-agents return JSON; an item is kept only if one evidence id resolves at D.
- Sub-agents are the same model as Ask and Research; only the prompt, tool set and budget differ. They run sequentially, in step order and in the pipeline's own process (no threads); S9 runs its drivers one at a time.
- A debate's evidence values become note facts, so its numbers can be traced.
- State lives in files; each call gets a purpose-built view; detail is fetched by id.

### 8.3 Failure handling and limits

| Case | Handling |
|---|---|
| Tool error | Returned to the model (§5.4) |
| Budget spent or context full | Forced answer turn, tools off |
| Answer still not JSON | Recorded as unparsed; no claims |
| Sub-agent returns nothing usable | Its Report step degrades (§9.3) |
| vLLM unreachable | The call raises; a report resumes with `--resume` |
| Determinism | Temperature 0 |
| Judge | Runs as a second pass (§13.2) |

### 8.4 Investigate (planner)

**Contract.** Answer a question too broad for one run: plan sub-questions, run each as an Ask or Research run, replan once, and combine the checked sub-answers into one claim-checked answer (spec §2.4). Code: `agent/plan.py`; prompts `plan.md`, `combine.md`.

**Flow:**

```text
question → plan (plan.md: question, as-of, coverage table, tool-set summaries; no tools)
         → [{id, question, mode: ask|research, why}]  ≤ 5
         → each sub-question, in order: a normal Ask or Research run (§8.2), own budget and claim check
         → replan (plan.md, replan turn: the plan + each sub-answer's text, claim statuses, gaps)
             → ≤ 2 follow-ups, run the same way; once only
         → combine (combine.md: question + every sub-answer's claims with ids and status; no tools)
         → JSON answer → claim check (§10.2) with the allowed-ids rule
```

**Rules:**

- Sub-runs are ordinary Ask and Research runs: same loop, prompts, tool sets, budgets and claim check. The planner changes only which questions are asked.
- Sub-runs run sequentially in one process, as Report's sub-agents do (§8.2). Each gets only its own sub-question: no plan, no other sub-answers.
- The combiner sees each sub-answer as its claims: text, status (§10.2) and ids. Only `verified`, `calculated` and `cited` claims are usable; `unverified` claims and failed sub-runs are listed as gaps it must name.
- **Allowed ids:** the final answer may cite only ids that a sub-answer cited. A new id makes the claim unverified ("not from a sub-answer").
- New numbers come only from calculations over cited values, which the claim check reruns (§10.2). The combiner calls no tools.
- No sub-question may restate the whole question: the plan is checked against an exact or near-duplicate match and rejected.
- Investigate never starts a Report and never writes versions; it reads saved reports through `research_report`.
- Every Investigate run keeps the plan, the replan, each sub-run's tool calls and answer, and the combine call, with times and tokens.

**Failure handling and limits:**

| Case | Handling |
|---|---|
| Plan unparseable after one retry, or empty | The question runs as one Research run; logged |
| More than 5 sub-questions or 2 follow-ups | Cut to the limit, in order; logged |
| Sub-run fails or hits its budget | Its forced answer is used; failed claims become gaps |
| Replan unparseable | No follow-ups; combine runs |
| Combine unparseable after one retry | Recorded as unparsed; the sub-answers are still shown |
| Combine input over its cap | Each sub-answer cut to its claims, longest text first |
| Time | Up to (5 + 2) sub-runs; budget in Appendix A, a warning past it |

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
| S3 history | code | data pack, template | History model (in memory); layer-2 checks |
| S4 propose | sub-agent (off by default) | driver table, Store | Assumption overrides |
| S5 forecast | code | model, guidance, `research.yaml` | The forecast model (in memory); layer-2 again |
| S6 peers | code | Store, per peer | Peer models (model book) |
| S7 value | code | model, peers, market | The valuation (in memory); valuation checks |
| S8 revise | code | the parent's `model.json` | `revisions.json` |
| S9 debates | sub-agent per scenario driver | model, Store, search | `debates.json`; section 7 sensitivities |
| S10 write | code + LLM | all artifacts | `facts.json`, `note.json`; consistency check |
| S11 export | code | all artifacts | `model.json`, `valuation.json`, note, PDF, workbook, `exceptions.json`, `report.json`; the version row |

Every artifact is a JSON file in the report folder. `report.json` records the version, parent, rating, PT, counts, check results and each step's time and tokens.

### 9.2 Rules

- Fixed steps; LLM calls are leaf tasks; no Ask or Research runs (spec §2.3).
- `--resume` reuses saved LLM outputs; code steps always rerun.
- `--no-llm` skips every LLM step.
- Sections 1 and 10 are written last (spec §2.3).
- Section 7 has a sensitivity row for every debated driver.
- Each LLM step has a budget; a run over the time budget logs a warning.

**Step algorithms:**

| Step | How |
|---|---|
| **S2 events** | One bounded agent (tool set `ask`, `events.md`) is asked what changed for the ticker in the quarter. Each event (type, summary, evidence ids) is kept only if one evidence id resolves at D. An unparseable reply means no events |
| **S4 propose** | §7.2 Proposer |
| **S8 revise** | Parent = the latest saved version before D; none → "initial draft (no prior run)". Headline lines, old (parent's `v<n>` id) vs new (`base` id); each typed by whether one of the parent's forecast quarters is now reported |
| **S9 debates** | One agent per scenario driver in the template (tool set `ask`, `debates.md`), told the driver's value. Kept only if one evidence id resolves; the resolved values become note facts |
| **S9 → section 7** | Sensitivities: the base model rebuilt with one driver moved one step, for the key drivers (take rate, growth, cost share) and every debated driver; each valued as a scratch run, giving the adj. EBITDA and PT change with ids |
| **S10 facts** | Code builds the sections. Every figure is registered once as an `F-n` fact (label, value, unit, source id) and formatted once by code |
| **S10 write** | One call per prose slot (`section.md`, no tools). The writer sees the slot's instruction and only that slot's facts. The number trace (§10.2) checks the text; one repair turn names the untraced numbers; any sentence still holding one is dropped and logged |
| **S10 lint** | The whole note is traced again: code-written blocks must appear verbatim and every slot's figures must trace; failures are `error` rows |

**Consistency check** (before export; spec §2.3). Code checks:

| Check | Passes when |
|---|---|
| `rating_thresholds` | The rating matches the return thresholds in `research.yaml` |
| `section1_numbers` | Every number in section 1 equals the same figure as its section's table prints it (PT, return and rating as in section 6) |
| `debated_drivers` | Every debated driver has a row in section 7's sensitivities |
| `guidance_in_2_2` | A guidance event (S2) has guided lines in section 2.2 |
| `open_issues` | Every `warning` and `error` row is listed, by X-id, in section 10 |

A failure becomes an `error` exception. The takeaways get one repair; other sections are never rewritten.

### 9.3 Failure handling and limits

| Case | Handling |
|---|---|
| Layer-2 or valuation FAIL | The run stops; nothing downstream runs on unchecked numbers |
| LLM step fails or hits its budget | Section reads "failed check: reason"; exception row; the run finishes |
| Untraced number after repair | Sentence dropped, logged (§10.2) |
| Consistency failure | Exception row; takeaways repaired once |
| Over the time budget | Warning exception |
| LibreOffice or Chrome missing | Export fails; no version saved |
| Runtime | One ticker per run |

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

**Matching algorithm:**
- **Numbers in a chunk** are parsed with sign (parentheses), written decimals, and a scale word or `%`. A bare number takes the chunk's stated units ("in millions"); with none stated, any scale.
- **Two numbers agree** within half a unit of the last digit either is written with: "$31.4 billion" matches 31,370 (USD millions), 31,500 doesn't.
- **Order:** a cited data row (`q:`, `kpi:`, `guide:`, `px:`, `macro:`), then an `xbrl:` fact, then a chunk. Data rows and facts must belong to the claim's company and are compared in whole units; percentages and unscaled units compare as written.
- **Calculated:** the claim's calculation reruns through the calculator (§5.2). Its result, × 100 for a percent, or rescaled from the input unit to the claim's unit, must match. Every input must be found in a cited chunk or fact.
- **Wrong company:** a value found only in another company's chunks is unverified.

**Note trace:**
- **A figure traces** when it equals a fact of its slot, in the fact's unit, within half a unit of its last written digit (`$9.7B` matches 9,712 USD millions). Dates, periods, ids and section numbers are not figures; every other number must be a marked fact of its own section.
- Code-written tables must appear verbatim.
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
| `ixbrl:`, `E-`, `X-` ids | Parse only, not resolved |

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

**Workbook build** (`export/workbook.py`, `report.py`): rows are the model's lines by statement, columns its quarters and FY columns. Input cells are blue values with their source id as a cell note; every other cell is a live formula written from the same DSL expression Python evaluated; assumptions are blue cells on the Assumptions tab that formulas reference; a cell Python left blank stays blank.

**Parity:** LibreOffice (headless) recalculates the workbook; every formula cell is compared with `model.json`'s value; differences are listed in `report.json`.

**Rules:**

- Parity: LibreOffice recalculates; every formula cell equals `model.json`.
- Every version is a draft until approved; an amendment is a new version (spec §2.3).
- Ask and Research show the status beside a cited estimate, so a draft is never presented as approved.
- Market data stays in `data/` (Tiingo licence).

### 11.3 Failure handling and limits

| Case | Handling |
|---|---|
| Parity mismatch | Recorded in `report.json`; **the run does not stop** |
| LibreOffice or Chrome missing | Export raises; no version saved |
| Approving an unknown version | Nothing happens, no error |
| Parity time | Up to 300 s |

---

## 12. Evaluation

How it works, including the backtest loop: [V4_EVALUATION.md](stages/V4_EVALUATION.md). Gold rows: [V4_INTERFACES.md §13](V4_INTERFACES.md#13-gold-and-results).

### 12.1 Contract

Grade each layer against bars frozen before the code they grade.

Code: extraction scorer, backtest, note eval (`evaluation/`); Ask and Research runners (`experiments/v4/`).

| Interface | Contract |
|---|---|
| **Gold** (13) | `evaluation/gold/`: extraction (337 labels), Ask (79 questions), Research (72), Investigate (10–15, `investigate-gold.jsonl`). Every row records who verified it |
| **Results** (13) | `evaluation/results/backtest-<label>/`; Ask and Research runs in `output/generation/<label>/` |

### 12.2 Rules: layers and bars

**Backtest loop** (`evaluation/backtest.py`):

```text
for each company, each release from Q4 2024 whose next quarter has reported:
    D = the day after the release's EDGAR acceptance (US/Eastern)
    forecast the next quarter at D (§7.2; proposer per research.yaml)
    leakage probe: no_future_input passes and the target quarter is not in history, else stop
    score vs the next quarter's first-reported value: revenue and the main KPI
    baselines: seasonal naive (same quarter last year × latest y/y), guidance midpoint
pool: MAPE and signed bias per metric and method → backtest-<label>/
--long: from each Q4 2024 release, all four quarters of 2025 (tests the fade)
```


| Layer | Test | Pass bar |
|---|---|---|
| 1 Extraction | 337 labels | ≥ 98% exact; 100% on revenue, EPS, main KPI |
| 2 Model integrity | §7.2 checks on every model | 100%; any FAIL stops |
| 3 Forecast | Walk-forward backtest, 48 forecasts per metric | MAPE below seasonal naive on revenue and main KPI; bias near 0 |
| 4 Valuation | Rerun on identical inputs; WACC sweep | Identical output; targets inside the peer range or flagged |
| 5 Note | Trace, ten sections, judge rubric, seeded errors | 100% traced; rubric ≥ 4/5 |
| Ask | 79 questions | ≥ 85% correct and verified; 100% of ids resolve |
| Research | 72 questions | ≥ 90% with a verifying `model:` id and stated assumptions |
| Investigate | 10–15 multi-part questions, each also given to one Research run as the baseline | Higher correct-and-verified rate than the baseline; 100% of ids resolve |

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
| Human review | Partial |

---

## 13. Reliability

Gaps against the spec and known problems: [V4_RESULTS.md](V4_RESULTS.md#gaps-against-the-spec).

### 13.1 Error policy

- **Tools never raise.** A bad call returns `{"error": …}` for the model to read, so one bad argument doesn't end a run.
- **Report code steps stop the run** on a failed integrity or valuation check.
- **LLM steps degrade:** the section reads "failed check: reason" and the run continues (spec §2.3 req. 7).
- **Builders never guess:** every gap becomes an exception row.

Per-block cases: §3.3, §4.3, §5.4, §6.3, §7.3, §8.3, §9.3, §10.3, §11.3, §12.3.

### 13.2 System limits

| Limit | Value |
|---|---|
| LLM context | 32k tokens (Qwen3.6-27B-FP8 on vLLM) |
| GPU | One, shared by vLLM and the embedder; the judge runs as a second pass |
| Licences | Market values never leave `data/` |
| Report time | Budgeted per run (Appendix A) |
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
- **Agent:** 20 calls, 20k context; tool results 40 rows, 16,000 characters.
- **Investigate:** ≤ 5 sub-questions, ≤ 2 follow-ups in one replan; each sub-run on the Ask or Research budget; combine input 16k tokens; time budget 30 minutes.
- **Sub-agents:** S2 events 8 calls, S4 proposer 10, S9 debates 8, each 16k context; S10 slot 1.5k tokens out. Extraction input ≤ 12k tokens per release. Report time budget 30 minutes.
- **Retrieval:** 40 candidates per search, RRF k = 60, 8 chunks.
- **Forecast:** horizon Q4 of the as-of year + 1 (`"+1"`; a fixed key such as `2027Q4` still works); valuation target year the as-of year + 1; fade 0.9 (bounds 0.75–1.0); guidance midpoint; seasonal shares over 3 years; ratio bands 50 bps (take rate) and 200 bps (margins); tax 10–30%; outside inputs on (weight 0.5, at least 6 quarters).
- **Scenarios:** weights 25/50/25; shocks ±1σ.
- **Valuation:** 60/40 multiple/DCF; target year 2027; scenario multiples ±15%; terminal growth 3% (grid 2.5–3.5%); 10 DCF years; β from monthly returns since 2023; debt-weight threshold 10%; Buy +15%, Sell −10%.

**Credentials.** Each user types in their own with `tenk setup` (`credentials.yaml`, gitignored). Environment variables take precedence. A missing one stops the command with an error naming it. `data/` and `output/` are never committed.

## Appendix B. CLI

| Command | Does |
|---|---|
| `tenk companies verify` | Resolve CIKs, year-ends, filing counts |
| `tenk fetch [--forms …] [--market]` | Fetch filings; `--market`: prices, macro, ERP |
| `tenk build --stage chunk,load,append,embed,pages,facts,market,kpis` | Build stages |
| `tenk research report TICKER… --as-of D [--no-llm] [--resume]` | Report mode |
| `tenk research approve TICKER:vN --by INITIALS` | Mark a version reviewed |
| `tenk investigate "question" --as-of D` | Investigate mode: plan, sub-runs, combined answer |
| `tenk setup` | Type in your credentials |
| `tenk tool NAME key=value …` | Run one tool by hand (`--list`: schemas) |
| `python -m tenk_agent.research.{model.history,forecast,valuation} TICKER… AS_OF` | One stage alone (`valuation --all`: relative ratings) |
| `python -m tenk_agent.evaluation.backtest [--label] [--proposer] [--long]` | Backtest and ablations |
| `python -m tenk_agent.evaluation.note_eval FOLDER…` | Layer 5 |
| `experiments/v4/ask_run.py`, `research_run.py` | Ask and Research gold runs |
| `experiments/v4/investigate_run.py` | Investigate gold run and its single-Research baseline |

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
  agent/         tools.py loop.py plan.py sec.py
  evaluation/    extraction.py backtest.py note_eval.py render.py (+ V0–V3)
  prompts/       ask.md research.md plan.md combine.md extract.md propose.md events.md debates.md section.md judge.md
docs/
  V4_INTERFACES.md   full interface schemas (§2.3)
  stages/V4_*.md     how each block works (companions to §3–§12)
```

## Appendix D. Glossary

| Term | Meaning |
|---|---|
| **as-of (D)** | The run date: the world as seen at the market open of D (US/Eastern) |
| **release** | An 8-K Item 2.02 exhibit 99.x (earnings release or shareholder letter). Form key `ER` |
| **period key** | `2026Q2`, `FY2025` |
| **data pack** | Every model input with its citable id and filing date (step S1) |
| **driver** | A model input the forecast grows or holds: volume, price per unit, segment bookings, take rate, cost share |
| **assumption** | A named forecast knob, such as `gb_g_mobility.g0` or `fade` |
| **anchor** | A guidance figure the forecast is solved to hit |
| **template** | A YAML file defining a model's lines (inputs and formulas), drivers, scenarios and anchors (§7.1) |
| **DSL** | The restricted formula syntax used in template `expr` and `forecast` strings; one expression is evaluated in Python and written as an Excel formula (§7.1) |
| **model book** | A session's models at one as-of, with their `model:` ids |
| **version** | A saved Report run (`UBER:v2`), with its parent |
| **tool set** | The named list of tool groups a run may call (§5.1) |
| **fact (F-n)** | A pre-formatted figure the note may print, with its source id |
| **E-n / X-n** | Evidence and exception rows of a report's log |
| **layers 1–5** | The spec's evaluation layers: extraction, model integrity, forecast, valuation, note |
