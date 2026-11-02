# V4 technical design: a junior sell-side research agent

> **What this is:** the contract of the V4 build, as built. The [spec](V4_SPEC.md) says what to build; this doc says how the system is shaped to do it: components, interfaces, rules, and what happens when things fail. Gaps against the spec are listed once, in §13.

| For | Read |
|---|---|
| Full interface schemas | [V4_INTERFACES.md](V4_INTERFACES.md) |
| How each block works, and its tests | [stages/V4_*.md](stages/), linked from each section |
| One run followed end to end | [V4_WORKED_EXAMPLE.md](stages/V4_WORKED_EXAMPLE.md) |
| Model equations | [V4_FINANCIAL_MODELING.md](stages/V4_FINANCIAL_MODELING.md) |
| Results | [V4_RESULTS.md](V4_RESULTS.md) |
| Why, and the build history | [DECISIONS.md](DECISIONS.md) (D-nnn), [V4_BUILD_LOG.md](V4_BUILD_LOG.md) |

**How to read it.** §2 is the map. §3 is the tool catalog. §4–§12 cover one block each, always in four parts: **purpose, interfaces, rules, failure handling and limits.** Each rule gives its reason. "Spec §n" is a section of the spec; a bare "§n" is a section of this doc.

*Updated 2026-11-02.*

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
| **Flag, don't hide** | A gap, conflict or failed check becomes an exception row, never an estimate shown as fact | §10.3 |
| **Degrade, don't stop** | A failed LLM step leaves "failed check: reason"; only an integrity failure stops a run | §9.4 |
| **Artifacts, not transcripts** | Steps hand over JSON files, not chat history | §9.2 |
| **One definition, two evaluators** | The model is defined once; Python and the workbook evaluate it and must agree | §8.3 |
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
| **tool set** | The named list of tool groups a run may call (§3.2) |
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
| **Acquisition** | Fetches filings, XBRL, prices, macro, ERP, cross-checks and Form 4 into a raw cache | | §4 |
| **Text processing** | Turns filings into Items, chunks and vectors (by rules) | | §4 |
| **Facts** | Loads XBRL facts; derives quarters on read | | §4 |
| **Store** | One SQLite file; every read filtered by the run's as-of | | §4 |
| **Retrieval** | Hybrid search over chunks | | §5 |
| **Toolbox** | The tools the agent calls | | §3 |
| **Agent runtime** | The tool-calling loop, prompts and budgets: Ask, Research, sub-agents | ✓ | §6 |
| **Extraction** | KPI and guidance rows from releases, each checked against a quote | ✓ | §7 |
| **Model engine** | Model, checks, forecast, scenarios, valuation | | §8 |
| **Report pipeline** | Steps S1–S11 per ticker and as-of; the note; versions | ✓ | §9 |
| **Verification** | Ids, claim check, number trace, consistency check | | §10 |
| **Export** | Note, PDF, workbook, parity check, review status | | §11 |
| **Evaluation** | Gold sets, scorers, backtest, note eval | ✓ judge | §12 |

Code paths: Appendix C.

### 2.3 Interface catalog

One row per boundary. Each is stated in its section; full schemas, numbered as here, are in [V4_INTERFACES.md](V4_INTERFACES.md).

| # | Interface | Between | § |
|---|---|---|---|
| 1 | **Raw cache** | Acquisition → builders | §4.2 |
| 2 | **Store** | everything → SQLite | §4.2 |
| 3 | **Search** | toolbox → Retrieval | §5.2 |
| 4 | **Tool calls** | agent ↔ toolbox | §3 |
| 5 | **LLM calls** | code ↔ model | §6.2 |
| 6 | **Extraction rows** | extractor → Store | §7.2 |
| 7 | **Templates** | config → Model engine | §8.2 |
| 8 | **Model and valuation files** | Model engine → Report, tools, Export | §8.2 |
| 9 | **Pipeline artifacts** | step → step | §9.2 |
| 10 | **Reference ids** | everywhere | §10.2 |
| 11 | **Fact list** | Report code → note writer | §10.2 |
| 12 | **Outputs** | system → reviewer | §11.2 |
| 13 | **Gold and results** | Evaluation ↔ files | §12.2 |
| – | **Config**, **CLI** | user → system | Appendix A, B |

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

| Rule | Reason |
|---|---|
| Strictly before D | The spec says so (spec §1.2), and it is the safest for a backtest |
| `asof.py` is the only code that compares dates | One place to audit |
| A `Store` is bound to one `AsOf` when opened; every read filters through it | No read path can skip the filter |
| No tool can widen the view (`research_report`'s `as_of` only narrows it) | The model can't see past the run's date by asking |
| `.latest()` takes, per value, the newest filing visible at D | A restatement counts only once filed (spec §1.6) |

**The no-LLM-numbers rule.** LLM output reaches the numbers only three ways:

1. Extracted rows that code checks against a verbatim quote (§7.3).
2. Bounded assumption proposals that code clips (§8.3).
3. Prose in which every number is a marked fact (§10.3).

Tables are always written by code. Reason: the spec requires every number from deterministic code (spec §3), and it keeps valuation reruns byte-identical.

**Errors.**
- **Tools never raise.** A bad call returns `{"error": …}` for the model to read, so one bad argument doesn't end a run.
- **Report code steps stop the run** on a failed integrity or valuation check.
- **LLM steps degrade:** the section reads "failed check: reason" and the run continues (spec §2.3 req. 7).
- **Builders never guess:** every gap becomes an exception row.

**Trace.** Every number in an answer or note carries an id. Agent runs keep their tool calls; `report.json` records each step's time, LLM calls and tokens.

**System limits:**

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

## 3. Tools

How it works: [V4_AGENT.md](stages/V4_AGENT.md#tools). Arguments and result schemas: [V4_INTERFACES.md §4](V4_INTERFACES.md#4-tool-calls).

### 3.1 Overview

A tool is a function the agent calls with JSON arguments; it returns JSON with citable ids. The **Toolbox** (`agent/tools.py`) holds the schemas, runs the calls, and sees exactly what its store's as-of sees.

Each tool is a thin front for one block. Not everything goes through tools:

| Caller | Uses |
|---|---|
| Ask | tool set `ask` |
| Research | tool set `research` |
| Report sub-agents (S2 events, S4 proposer, S9 debates) | tool set `ask` |
| Report code steps, builders, evaluation | their blocks directly, no tools |
| A person | `tenk tool NAME key=value …` (`--list` prints the schemas) |

### 3.2 Tool sets

| Group | Tools | `ask` | `research` |
|---|---|:-:|:-:|
| text | `search_filings` | ✓ | ✓ |
| calc | `calculator` | ✓ | ✓ |
| facts | `financials`, `company_kpis`, `guidance`, `filings` | ✓ | ✓ |
| market | `market_data`, `crosscheck` | ✓ | ✓ |
| reports | `research_report` | ✓ | ✓ |
| model | `model_view`, `run_scenario`, `compare_peers`, `variance_review` | | ✓ |

V3's sets `text` and `full` remain for the V3 corpus.

### 3.3 Catalog

| Tool | Returns | Backed by |
|---|---|---|
| `search_filings` | Chunks for a query, per company and filing | Retrieval (§5) |
| `calculator` | A result with its formula and inputs | `agent/tools.py` |
| `financials` | Reported and derived quarterly figures (`q:`, `xbrl:` ids) | Store facts (§4) |
| `company_kpis` | KPI rows with their quotes (`kpi:` ids) | Store KPIs (§7) |
| `guidance` | Guidance ranges, with the actual once reported (`guide:` ids) | Store guidance (§7) |
| `filings` | The filings visible at the as-of | Store (§4) |
| `market_data` | Prices and macro series (`px:`, `macro:` ids) | Store market (§4) |
| `crosscheck` | NYC TLC or BTS monthly series (`cc:` ids) | Store market (§4) |
| `research_report` | A saved version's PT, rating, estimates and **review status** | Saved versions (§11) |
| `model_view` | Model lines and assumptions (`model:` ids) | Model book (§8) |
| `run_scenario` | Base vs scenario lines, PT and rating after changing assumptions | Model book (§8) |
| `compare_peers` | One metric across companies, with each one's assumptions | Model book (§8) |
| `variance_review` | Our saved estimate vs the actual, with a revenue bridge | Saved version + Store |

### 3.4 Rules

| Rule | Reason |
|---|---|
| Every tool is bound to the run's as-of | §2.5 |
| Every value in a result carries an id that resolves (§10.2) | The claim check can verify what the model cites |
| Errors are results: `{"error": …}` plus the valid choices | The model can correct itself; one bad call doesn't end a run |
| Tools come in groups; no run gets every tool | Smaller schemas in a 32k window; a mode can't call what it shouldn't |
| Results capped at 40 rows and 16,000 characters | One result can't fill the window |
| Ask cites estimates only through `research_report`; Research computes only through model tools, assumptions listed | Spec §2: Ask retrieves, Research computes |
| `run_scenario` writes a session-only `scratch-n` run, clipped to the proposer's bounds (§8.3) | Only Report creates versions; scenarios stay within history |
| Peers are built with default assumptions (no LLM), once per ticker and as-of | A peer's numbers don't depend on an LLM run |

### 3.5 Failure handling and limits

| Case | Handling |
|---|---|
| Unknown tool, argument, metric, line or assumption | Error with the valid choices; the model retries |
| Unknown company, or a search naming none | Error listing the corpus |
| Nothing visible at the as-of | Empty rows (or `report: null`), not an error |
| A model can't be built | Error naming the failing check (§8.4) |
| Result over the caps | Truncated, with `"omitted": n` |
| First `compare_peers` call | Slow: peers are built on first use |

---

## 4. Data layer

How it works: [V4_DATA.md](stages/V4_DATA.md). Schemas: [V4_INTERFACES.md §1–2](V4_INTERFACES.md#1-raw-cache).

### 4.1 Purpose

Fetch every source into a raw cache, build one SQLite file from it, and serve every read through the run's as-of.

Parts: companies, EDGAR filings, market data, cross-checks and Form 4 (`acquisition/`); text processing (`preprocess/`, `chunking/`); facts (`facts/xbrl.py`, `quarters.py`); the store (`store.py`, `asof.py`).

### 4.2 Interfaces

| Interface | Contract |
|---|---|
| **Raw cache** (1) | One folder per filing (`raw/filings/<T>/<period>-<form>/`); `manifest.json` lists each filing's form, period, filing date, acceptance time and retrieval time |
| **`companies.yaml`** | Per company: CIK (written by `tenk companies verify`), sector (peers = same sector), template, main KPI |
| **Store** (2) | Tables `filings`, `chunks`, `facts`, `kpis`, `guidance`, `market`, `insider_tx`, `model_versions`. **Every row carries the date it became public** |
| **Store reads** (2) | `Store(path, asof)`: every read method filters by that as-of |
| **Ids** | Chunks `UBER-2026Q2-ER-EX99-005`; facts `xbrl:<T>:<taxonomy>:<concept>:<accession>:<start>/<end>`; derived quarters `q:<T>:<metric>:<period>` |

### 4.3 Rules

| Rule | Reason |
|---|---|
| One filing per form and period; amendments ignored | One value per period |
| Releases: 8-K Item 2.02, every `EX-99.*` exhibit | A release can carry two exhibits (BKNG 2026-08-04) |
| A release's period is the last quarter end before it was filed, checked against the 10-Q | Releases carry no XBRL period |
| DEF 14A: the first proxy each year, keyed to the prior fiscal year | Later ones are special meetings (spec §1.2) |
| Fetch cache-first; builds run offline | Reproducible builds and evaluations |
| Filings are split into Items by rules, not the LLM | Same Items as the LLM agent, at a fraction of its 6 GPU-hours |
| Inline XBRL is read for every company-specific and segment fact | Company facts lacks both |
| Quarters are derived on read, never stored: Q4 = FY − 9M; Q2 and Q3 from year-to-date when needed | Nothing goes stale between as-ofs (spec §1.6) |
| A metric uses one XBRL concept per fiscal year | Mixing concepts broke Q2 sums |
| A year recast by a later filing reads `restated`, logged, not `fail` | A recast is a known event, not a bad number (spec §1.6) |
| EPS and share counts: direct facts only; Q4 EPS from the release | They don't add up across quarters |
| Sources that block scripts are dropped, never worked around | Their terms (Stooq, TSA, Google Trends) |

### 4.4 Failure handling and limits

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

## 5. Retrieval

How it works: [V4_RETRIEVAL.md](stages/V4_RETRIEVAL.md) and [V1_RETRIEVAL.md](stages/V1_RETRIEVAL.md). Schema: [V4_INTERFACES.md §3](V4_INTERFACES.md#3-search).

### 5.1 Purpose

Find the chunks that answer a query, one search per company and filing, under the run's as-of. No LLM.

Parts: decomposer, searcher, index (Qwen3-Embedding-0.6B, FTS5), reranker (bge-reranker-v2-m3), all in `retrieval/`.

### 5.2 Interfaces

| Interface | Contract |
|---|---|
| **Search** (3) | `search_filings(query, companies?, periods?, forms?, k = 8)` → the searches it ran, and up to k chunks with id, company, form, period, filing date and text |

### 5.3 Rules

| Rule | Reason |
|---|---|
| Companies come from the arguments, else the query; no period = the latest filing of each form at D | "Latest" must respect the as-of |
| One sub-question per company and filing, naming only that company | Otherwise one company's chunks crowd out the rest |
| Keyword + dense search, fused, then reranked | Keywords catch figures; vectors catch paraphrase |
| The model sees parent chunks | A whole subsection or table, not a fragment |
| Results merged round-robin until k | Every company gets its share |

### 5.4 Failure handling and limits

| Case | Handling |
|---|---|
| No company, or an unknown one | Error listing the corpus |
| Bad period key | Error |
| No filing for a slot at that as-of | Fewer chunks, not an error |
| GPU held by vLLM | Embedder and reranker run on CPU |
| Size | 8 chunks per call, each up to 1,280 tokens |

---

## 6. Agent runtime

How it works: [V4_AGENT.md](stages/V4_AGENT.md). Reply schemas: [V4_INTERFACES.md §5](V4_INTERFACES.md#5-llm-calls).

### 6.1 Purpose

Run the LLM: the Ask and Research loop, Report's sub-agents, and every single-shot call.

Parts: the loop (`agent/loop.py`, LangGraph), prompts (`prompts/*.md`), sub-agents (`research/subagents.py`, `propose.py`), the model book (`research/book.py`), and one model interface (`models.py`: vLLM or Claude).

### 6.2 Interfaces

**LLM calls** (5). Each reply is one JSON object.

| Call | Tools | Returns | Budget |
|---|---|---|---|
| Ask / Research answer | `ask` / `research` | Answer, claims with cited ids, assumptions (Research) | 20 calls, 20k context |
| KPI extraction | – | Rows with chunk id and quote | 1 call + 1 repair per release |
| S2 events | `ask` | Events with type, summary, evidence ids | 8 calls, 16k |
| S4 proposer | `ask` | Assumption proposals with rationale and evidence | 10 calls, 16k |
| S9 debate, per driver | `ask` | Bull, bear, hypothesis, trigger, gap, evidence | 8 calls, 16k |
| S10 prose slot | – | Text with facts marked `{F-n}` | 1.5k tokens out, 1 repair |
| Judge (evaluation) | – | Scores per criterion | gpt-oss-20b, a second pass |

### 6.3 Rules

```text
question → model → tool calls → tools → model → … → JSON answer → claim check (§10.3)
```

| Rule | Reason |
|---|---|
| The first turn must be a tool call | No answers from memory |
| A repeated identical call errors and still counts | Stops loops |
| After the call budget or a full context: one turn with tools off to answer | Every run ends with an answer |
| An unparseable answer gets one retry | A formatting slip is not a wrong answer |
| Every answer passes the claim check before it is shown | No unverified number presented as verified |
| Sub-agents return JSON; an item is kept only if one evidence id resolves at D | No unsupported claim reaches the note |
| A debate's evidence values become note facts | Its numbers can then be traced |
| State lives in files; each call gets a purpose-built view; detail is fetched by id | Fits a 32k window |

### 6.4 Failure handling and limits

| Case | Handling |
|---|---|
| Tool error | Returned to the model (§3.5) |
| Budget spent or context full | Forced answer turn, tools off |
| Answer still not JSON | Recorded as unparsed; no claims |
| Sub-agent returns nothing usable | Its Report step degrades (§9.4) |
| vLLM unreachable | The call raises; a report resumes with `--resume` |
| Determinism | Temperature 0 |
| Judge | Can't share the GPU with the 27B model, so it runs afterwards |

---

## 7. Extraction (layer 1)

How it works: [V4_EXTRACTION.md](stages/V4_EXTRACTION.md). Schemas: [V4_INTERFACES.md §6](V4_INTERFACES.md#6-extraction-rows).

### 7.1 Purpose

Read KPIs and guidance from earnings releases into checked, quoted rows.

Parts: LLM extractor (`facts/extract.py`), rules extractor (`facts/rules.py`, an ablation), registry (`facts/kpis.yaml`), loader (`tenk build --stage kpis`).

### 7.2 Interfaces

| Interface | Contract |
|---|---|
| **Registry** | Per company: each KPI's key, unit, segments and labels (from spec §1.5) |
| **Reply** | KPI and guidance rows, each with value, unit, source chunk id and a verbatim quote |
| **Extraction rows** (6) | Stored in `kpis` and `guidance` with period, filing date and definition version. Ids `kpi:<T>:<metric>[.<segment>]:<period>`, `guide:<T>:<metric>:<period>` |

### 7.3 Rules

| Rule | Reason |
|---|---|
| Input is only that release's chunks | No other filing can leak in |
| Code keeps a row only if: the chunk id was given, the quote is verbatim, the value is in the quote, and the metric is in the registry | The LLM proposes; code decides what is stored |
| One repair turn, then an exception row | Never a guessed value (spec §2.3) |
| GAAP diluted EPS only | Non-GAAP EPS was taken for GAAP |
| A recast gives earlier periods a new definition version; readers take the newest | Spec §1.6: restate history and note it |
| Guidance keeps its basis (level, growth or margin); the forecast converts it | Releases guide all three ways |
| Where a KPI is also an XBRL fact, the filing wins and the gap is logged | Spec §1.6. **Not built** (§13.1) |

### 7.4 Failure handling and limits

| Case | Handling |
|---|---|
| Row fails after repair | Exception row; never stored |
| Release with no valid rows | Its KPIs are missing; the model logs the gap |
| No numeric guidance (TRIP) | Guidance baseline reads "n/a" |
| Input size | ≤ 12k tokens per release; long releases send matching chunks first |

---

## 8. Model engine and valuation

How it works: [V4_MODEL.md](stages/V4_MODEL.md). Equations: [V4_FINANCIAL_MODELING.md](stages/V4_FINANCIAL_MODELING.md). Schemas: [V4_INTERFACES.md §7–8](V4_INTERFACES.md#7-templates).

### 8.1 Purpose

Build a three-statement model from the data pack, forecast it, and value it. All deterministic code.

Parts: data pack (S1), DSL and graph, templates, history (S3), layer-2 checks, forecast and scenarios (S5), outside inputs, proposer (S4), valuation (S6–S7), model book. Code: `research/`.

### 8.2 Interfaces

| Interface | Contract |
|---|---|
| **Templates** (7) | YAML lines: inputs (history from a source id), formulas and forecast expressions, plus drivers, scenarios and anchors |
| **DSL** | Arithmetic, `x[-4]`, `fy()`, `fyp()`, `min`, `max`, `if_`, `first`, `@assumption`. Anything else is rejected |
| **`model.json`** (8) | Every cell (line × period) with value and kind. **Input cells carry their source id and filing date** |
| **`valuation.json`** (8) | Market cap, EV, cost of capital, peers, PT per scenario, weighted PT, return, rating, DCF grid, `inputs_hash` |
| **`research.yaml`** | Every forecast and valuation knob (Appendix A) |

### 8.3 Rules and algorithms

**Model** (spec §3.1)

| Template | Companies | Revenue build |
|---|---|---|
| `marketplace` | BKNG, EXPE, ABNB | Room nights × bookings per night = GB; revenue = GB × take rate |
| `segments` | UBER | Per segment: GB × its take rate; costs a share of total GB |
| `orders` | DASH, LYFT, CART | Orders or rides × value per unit; revenue = that × take rate |
| `trip` | TRIP | Revenue on one faded rate; EBITDA a margin on revenue |

| Rule | Reason |
|---|---|
| One definition (templates + DSL), two evaluators; Python is the source of truth | Two implementations would drift |
| Condensed statements, shared by all templates | One engine serves all eight |
| Tax at the trailing rate, bounded 10–30% | A one-off quarter can't set the forecast rate |
| Every input cell carries its source and filing date | Needed by the checks and the trace |
| Missing inputs stay blank, never 0 | A 0 broke full-year sums |
| Non-operating items forecast at 0 | Not forecastable |
| Travel prepayment not modeled | Spec §3.1 simplification |

**Forecast** (spec §3.2)

```text
first forecast quarter   Driver(q) = Driver(q-4) × (1 + g), anchored to the guidance midpoint
later quarters           Driver(q) = last FY × (1 + g) × seasonal share of q (3-year mean)
fade                     g(t+1) = g(t) × 0.90
ratios                   same quarter last year + a shift held for 4 quarters, then 0;
                         bounded to the 8-quarter range ± a band (take rate 50 bps, margins 200 bps)
```

| Rule | Reason |
|---|---|
| Forecast per driver, never per total | Spec §3.2 |
| Later quarters by seasonal share | One odd quarter doesn't repeat a year later |
| A ratio's shift holds until lapped, then 0 | A one-off change isn't compounded |
| Ratios stay within the 8-quarter range ± a band | Within what the company has shown |
| Each guide anchors its own driver, upstream first (bookings → revenue → EBITDA) | Later anchors see earlier ones |
| Anchor at the midpoint, not the high end | Signed off before the backtest (spec §7) |
| An anchor the bounds stop is logged, never forced | Flag, don't hide |
| Scenarios move each driver by ±1σ of its history | The shock scales with the driver's history |
| Outside data (BTS, NYC TLC, FRED) nudges one starting growth rate per company, weighted by fit; guidance overrides it | Spec §3.2 "Outside inputs" |

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

### 8.4 Failure handling and limits

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

## 9. Report pipeline

How it works, and the note's ten sections: [V4_REPORT.md](stages/V4_REPORT.md). Artifact schemas: [V4_INTERFACES.md §9](V4_INTERFACES.md#9-pipeline-artifacts).

### 9.1 Purpose

Run S1–S11 for one ticker and as-of, write the note, and save a version.

Parts: runner (`research/pipeline.py`), sections, facts and writer (`research/note/`), trace and consistency (§10).

### 9.2 Interfaces: steps and artifacts

| Step | Kind | Writes |
|---|---|---|
| S1 data pack | code | Every model input with its source |
| S2 events | sub-agent | `events.json` |
| S3 model | code | History model; layer-2 checks |
| S4 propose | sub-agent (off by default) | Assumption overrides |
| S5 forecast | code | `model.json`; layer-2 again |
| S6 peers | code | Peer models |
| S7 value | code | `valuation.json`; valuation checks |
| S8 revise | code | `revisions.json` (vs the parent version) |
| S9 debates | sub-agent per scenario driver | `debates.json` |
| S10 write | code + LLM | `facts.json`, `note.json`; consistency check |
| S11 export | code | Note, PDF, workbook, `exceptions.json`, `report.json`; the version row |

Every artifact is a JSON file in the report folder. `report.json` records the version, parent, rating, PT, counts, check results and each step's time and tokens.

### 9.3 Rules

| Rule | Reason |
|---|---|
| Fixed steps; LLM calls are leaf tasks; no Ask or Research runs | Spec §2.3: "not an open agent loop" |
| Parent = the latest version with an earlier as-of; none → "initial draft (no prior run)" | Spec §2.3 |
| Revisions cover revenue, adj. EBITDA and EPS (next quarter, last two FY columns) | The headline lines of spec §2.3 table 4.1 |
| `--resume` reuses saved LLM outputs; code steps always rerun | A crash repeats only cheap steps |
| `--no-llm` skips every LLM step | Code-only drafts and tests |
| Sections 1 and 10 are written last | Spec §2.3 req. 4 |
| Section 7 has a sensitivity row for every debated driver | Spec §2.3 req. 5 |
| Each LLM step has a budget; a run over 30 minutes logs a warning | Spec §2.3 req. 8 |

**Consistency check** (before export; spec §2.3 req. 5). Code checks that:
- the rating matches the return thresholds;
- section 1's numbers equal their section tables;
- every debated driver is in section 7;
- a guidance change in the events appears in 2.2;
- every open issue is in section 10.

A failure becomes an `error` exception. The takeaways get one repair; other sections are never rewritten.

### 9.4 Failure handling and limits

| Case | Handling |
|---|---|
| Layer-2 or valuation FAIL | The run stops; nothing downstream runs on unchecked numbers |
| LLM step fails or hits its budget | Section reads "failed check: reason"; exception row; the run finishes |
| Untraced number after repair | Sentence dropped, logged (§10.3) |
| Consistency failure | Exception row; takeaways repaired once |
| Over 30 minutes | Warning exception |
| LibreOffice or Chrome missing | Export fails; no version saved |
| Runtime | One ticker per run; pin to the E-cores |

---

## 10. Verification and number trace

How it works: [V4_VERIFICATION.md](stages/V4_VERIFICATION.md). Full id grammar: [V4_INTERFACES.md §10–11](V4_INTERFACES.md#1011-reference-ids-fact-list-evidence-and-exception-log).

### 10.1 Purpose

Make every number checkable: resolve ids, check answer claims, trace every number in the note, keep the evidence and exception log.

Parts: id grammar and resolver (`research/refs.py`), claim check (`generation/verify.py`), fact list, number trace and consistency check (`research/note/`).

### 10.2 Interfaces

| Interface | Contract |
|---|---|
| **Reference ids** (10) | One grammar for every kind: chunk, accession, `xbrl:`, `q:`, `kpi:`, `guide:`, `px:`, `macro:`, `cc:`, `model:`, `E-n`, `X-n`. Data ids resolve under an as-of to a value and unit |
| **`model:` ids** | `model:<T>:<as_of>:<run>:<line>.<period>`; run = `base`, `scratch-n`, `val-…`, `var-<period>` or a saved `v<n>` |
| **Fact list** (11) | `F-n` facts: label, formatted value, unit, source id. The writer uses them verbatim, marked `{F-n}` |
| **Log** | `E-n` rows from the facts; `X-n` rows (severity `info`, `warning` or `error`) from every step |

### 10.3 Rules

**Claim status** (Ask and Research answers):

| Status | When |
|---|---|
| **verified** | The value matches a cited, resolved value within its written precision |
| **calculated** | Its calculation reruns from resolved inputs |
| **cited** | No number, valid ids |
| **unverified** | Anything else; shown with the reason |

**Note trace:**

| Rule | Reason |
|---|---|
| Code-written tables must appear verbatim | Catches a changed table cell |
| Every number in prose is a marked fact **of its own section** or an allowed token (dates, "Q2", "10-K") | Catches a figure swapped for another fact |
| No "pending" or "TBD"; gaps read "n/a: …" or "failed check: …"; estimates carry "E" | Spec §2.3 note rules |
| One repair turn; a sentence still untraced is dropped and logged | Spec §2.3 req. 3 |
| Every gap, conflict or failed check becomes an `X-n` row | Flag, don't hide |

### 10.4 Failure handling and limits

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

### 11.1 Purpose

Write the note and workbook a reviewer reads, check the workbook against the model, and record the review.

Parts: workbook and parity (`research/export/workbook.py`), other tabs and PDF (`report.py`), renderer (headless Chrome), review (`tenk research approve`).

### 11.2 Interfaces

| Interface | Contract |
|---|---|
| **Report folder** (12) | `output/reports/<TICKER>/<as_of>/`: note (`.md`, `.pdf`), workbook, artifacts; saved as a version with its parent |
| **Workbook** | Tabs: Model, Assumptions, Summary, Scenarios, Valuation, DCF, Revisions, Model (prior), Checks, Log. Inputs blue, formulas black, links green |
| **Review** | `tenk research approve TICKER:vN --by INITIALS` marks a version reviewed |

### 11.3 Rules

| Rule | Reason |
|---|---|
| Parity: LibreOffice recalculates; every formula cell equals `model.json` | The workbook must be live and agree with the model |
| Every version is a draft until approved; an amendment is a new version | The human decides (spec §2.3) |
| Ask and Research show the status beside a cited estimate | A draft is never presented as approved |
| Market data stays in `data/` | Tiingo licence |

### 11.4 Failure handling and limits

| Case | Handling |
|---|---|
| Parity mismatch | Recorded in `report.json`; **the run does not stop** (§13.1) |
| LibreOffice or Chrome missing | Export raises; no version saved |
| Approving an unknown version | Nothing happens, no error (§13.1) |
| Parity time | Up to 300 s |

---

## 12. Evaluation

How it works, including the backtest loop: [V4_EVALUATION.md](stages/V4_EVALUATION.md). Gold rows: [V4_INTERFACES.md §13](V4_INTERFACES.md#13-gold-and-results).

### 12.1 Purpose

Grade each layer against bars frozen before the code they grade.

Parts: extraction scorer, backtest, note eval (`evaluation/`); Ask and Research runners (`experiments/v4/`).

### 12.2 Interfaces

| Interface | Contract |
|---|---|
| **Gold** (13) | `evaluation/gold/`: extraction (337 labels), Ask (79 questions), Research (72). Every row records who verified it |
| **Results** (13) | `evaluation/results/backtest-<label>/`; Ask and Research runs in `output/generation/<label>/` |

### 12.3 Rules: layers and bars

| Layer | Test | Pass bar |
|---|---|---|
| 1 Extraction | 337 labels | ≥ 98% exact; 100% on revenue, EPS, main KPI |
| 2 Model integrity | §8.3 checks on every model | 100%; any FAIL stops |
| 3 Forecast | Walk-forward backtest, 48 forecasts per metric | MAPE below seasonal naive on revenue and main KPI; bias near 0 |
| 4 Valuation | Rerun on identical inputs; WACC sweep | Identical output; targets inside the peer range or flagged |
| 5 Note | Trace, ten sections, judge rubric, seeded errors | 100% traced; rubric ≥ 4/5 |
| Ask | 79 questions | ≥ 85% correct and verified; 100% of ids resolve |
| Research | 72 questions | ≥ 90% with a verifying `model:` id and stated assumptions |

| Rule | Reason |
|---|---|
| Bars are frozen before the code they grade; a miss is reported as a miss | No tuning to the test |
| A gold change after a run is logged, old score beside the new | Changes after seeing answers stay visible |
| Backtest as-of = the day after a release's EDGAR acceptance | Works for releases before the open and after the close |
| Actuals as first reported | Scores what was knowable then |
| Baselines: seasonal naive, guidance midpoint, an LLM with no tools | Spec §6; the last also measures memorization |
| The judge is from another model family | No model grades itself |

### 12.4 Failure handling and limits

| Case | Handling |
|---|---|
| Actual not reported yet | Point skipped |
| An input filed on or after D | The backtest stops (leakage probe) |
| Judge skips a criterion | Missing from the mean |
| Sample | 48 forecasts per metric |
| Held-out quarters | None: the LLM's training cutoff is unpublished |
| Human review | Partial (§13.1) |

---

## 13. Known problems and not built

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
| `ixbrl:`, `E-`, `X-` ids resolved by the claim check (§10.2) | Parse only |
| Parity mismatch as a layer-2 FAIL (§11.3) | Recorded; the run continues |
| `tenk research approve` errors on an unknown version (§11.4) | Silently matches nothing |
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
