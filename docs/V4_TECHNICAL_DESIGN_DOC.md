# V4 technical design: a junior sell-side research agent

**What this is:** how the V4 build works, as built. **Spec** (what to build): [JUNIOR_SELL_SIDE_EQUITY_RESEARCH_AGENT_SPEC.md](JUNIOR_SELL_SIDE_EQUITY_RESEARCH_AGENT_SPEC.md) ("spec §n"). **Why** each choice: [DECISIONS.md](DECISIONS.md) (D-nnn). **History** (phase gates, findings, gold runs): [V4_BUILD_LOG.md](V4_BUILD_LOG.md) ("build log §n"). **Numbers:** [V4_RESULTS.md](V4_RESULTS.md). **Model equations:** [FINANCIAL_MODELING.md](FINANCIAL_MODELING.md).
**Updated:** 2026-11-01. A bare "§n" is a section of this doc. What is not built is listed once, in §13.1.

**How to read it.** §2 is the map: every component and every interface between them. §3–§11 are deep dives, one per subsystem, each in the same six parts: **components → interfaces → rules and algorithms (each with its reason) → failure handling → constraints → tests**. Each interface is defined once, in the section that owns it; §2.3 only points there. §12 follows one run across the interfaces with real payloads.

**Reasons.** Every rule carries a reason and its source: a decision (D-nnn), a build-log finding, the spec, or an issue (I-nnn). A reason marked ***(new)*** was not written down anywhere before this doc; it states the intent as the code shows it and is open to review.

---

## 1. Overview

### 1.1 Goals

One agent covers eight US travel and mobility companies (BKNG, EXPE, ABNB, TRIP · UBER, LYFT, DASH, CART) from free public data (D-038), in three modes (spec §2):

| Mode | In | Out |
|---|---|---|
| **Ask** | a question + as-of | a short cited answer from filed data |
| **Research** | a question + as-of | an analytical answer computed by the model, assumptions shown |
| **Report** | ticker + as-of | a 10-section draft note, a model-change workbook, an evidence and exception log |

Every run takes an **as-of date** and sees only data published before it, which makes the walk-forward backtest possible. V4 replaces the V0–V3 corpus but reuses their pipeline (parsing, chunking, retrieval, agent loop, claim check); V3 stays runnable as a regression check ([build log §2](V4_BUILD_LOG.md#2-migration-from-v3)).

### 1.2 Principles

| Principle | Means | Where enforced |
|---|---|---|
| **As-of everywhere** | No read can see data published on or after the as-of | §2.5, `asof.py`, `Store` |
| **Code computes every number** | The LLM extracts, proposes bounded assumptions and writes prose; it never outputs a forecast or valuation number | §2.5, §7.3, §9.3 |
| **Everything is cited** | Every figure carries an id that resolves to its source | §9.2 |
| **Flag, don't hide** | A gap, conflict or failed check becomes an exception row, never an estimate shown as fact | §9.2 |
| **Degrade, don't stop, except on integrity** | A failed LLM step leaves "failed check: reason"; a model-integrity failure stops the run | §8.4 |
| **Artifacts, not transcripts** | Steps hand over JSON files, not chat history | §5.3, §8.2 |
| **One definition, two evaluators** | The model is defined once; Python and the workbook evaluate it and must agree | §7.3, D-041 |
| **Knobs in config** | Every default lives in a pinned YAML file | Appendix A, D-019 |

### 1.3 Glossary

| Term | Meaning |
|---|---|
| **as-of (D)** | The run date; the world as seen at the market open of D (US/Eastern) |
| **release** | 8-K Item 2.02 exhibit 99.x: earnings release or shareholder letter. Form key `ER` |
| **period key** | `2026Q2`, `FY2025` |
| **data pack** | S1's history: every model input with its citable id and filing date |
| **driver** | A model input the forecast grows or holds (volume, price per unit, segment bookings, take rate, cost share) |
| **assumption** | A named forecast knob (`gb_g_mobility.g0`, `take_rate_mobility.shift0`, `fade`), listed on the Assumptions tab |
| **anchor** | A guidance figure the forecast is solved to hit |
| **model book** | A session's models at one as-of, with their `model:` ids (`research/book.py`) |
| **version** | A saved Report run (`UBER:v2`) in `model_versions`, with its parent |
| **fact (F-n)** | A pre-formatted figure the note may print, with its source id |
| **evidence / exception (E-n / X-n)** | Rows of a report's log |
| **layers 1–5** | The spec's evaluation layers: extraction, model integrity, forecast, valuation, note |

---

## 2. System architecture

### 2.1 Component map

<p align="center">
  <img src="img/v4-components.svg" alt="V4 component map. External sources feed Acquisition, which writes a raw cache. Text processing and Facts and extraction build the Store, one SQLite file read only through an AsOf filter. Retrieval and the Model engine read the Store. The Agent runtime (loop, toolbox, sub-agents) reaches Retrieval, the Store and the Model engine through tools. Ask and Research are agent runs; the Report pipeline runs fixed steps, calls the Model engine and bounded sub-agents, and its artifacts pass Verification and Export into a note, workbook and log. Saved versions go back to the Store. Evaluation reads gold sets and every layer's outputs. Components that call the LLM are marked." width="100%">
</p>

Boxes are components (§2.2); numbered arrows are interfaces (§2.3). Pink marks a component that calls the LLM.

### 2.2 Components

| Component | Responsibility | Code | Owns (state) | Depends on | LLM | Section |
|---|---|---|---|---|:-:|---|
| **Acquisition** | Fetch filings, XBRL, prices, macro, ERP, cross-checks, Form 4; cache them | `acquisition/` | `data/v4/raw/` + `manifest.json` | EDGAR, Tiingo, ALFRED, Damodaran, BTS, NYC TLC | | §3 |
| **Text processing** | Filings → blocks → Items → chunks → vectors | `preprocess/`, `chunking/`, `retrieval/index.py` | chunk and child rows, FTS5, vectors | raw cache | (rules, D-039) | §3 |
| **Facts** | XBRL and inline XBRL facts; quarters derived on read | `facts/xbrl.py`, `facts/quarters.py` | `facts` rows; `metrics.yaml` | raw cache | | §3 |
| **Extraction** | KPI and guidance rows from releases, validated against quotes | `facts/extract.py`, `facts/rules.py` | `kpis`, `guidance` rows; `kpis.yaml` | chunks, LLM | ✓ | §6 |
| **Store** | One SQLite file; every read filtered by the run's `AsOf` | `store.py`, `asof.py` | the database (D-005) | | | §3 |
| **Retrieval** | Hybrid search over chunks: slots, BM25 + dense, fusion, rerank | `retrieval/` | | Store, embedder, reranker | | §4 |
| **Agent runtime** | Tool-calling loop, toolbox, prompts, budgets; Ask, Research and the bounded sub-agents | `agent/`, `research/subagents.py`, `research/propose.py`, `prompts/` | | Retrieval, Store, Model engine, LLM | ✓ | §5 |
| **Model engine** | Data pack, model graph, layer-2 checks, forecast, scenarios, valuation, model book | `research/datapack.py`, `research/model/`, `forecast.py`, `valuation.py`, `book.py` | `model.json`, `valuation.json` per run; the book cache per session | Store | | §7 |
| **Report pipeline** | Fixed steps S1–S11 per ticker and as-of; resume; versions; the note | `research/pipeline.py`, `research/note/` | the report folder; a `model_versions` row | everything above | ✓ (S2, S4, S9, S10) | §8 |
| **Verification** | Reference grammar and resolver; claim check; number trace and lint; consistency check | `research/refs.py`, `generation/verify.py`, `research/note/trace.py`, `research/note/consistency.py` | | Store, model book | | §9 |
| **Export** | `note.md`, `note.pdf`, `workbook.xlsx`; parity check; review status | `research/export/`, `evaluation/render.py`, `cli.py` | output files | Report artifacts, LibreOffice, headless Chrome | | §10 |
| **Evaluation** | Gold sets, scorers, backtest, note eval | `evaluation/`, `experiments/v4/` | `evaluation/gold/`, `evaluation/results/` | everything | ✓ judge | §11 |

### 2.3 Interface catalog

One row per boundary. The contract is defined in the owning section; this table is the index.

| # | Interface | Between | Form | Contract (owning section) |
|---|---|---|---|---|
| 1 | **Raw cache** | Acquisition → builders | files under `raw/`, `manifest.json` | layout, manifest entry (§3.2) |
| 2 | **Store** | everything → SQLite | SQL schema; Python read API on a `Store` bound to an `AsOf` | tables, columns, keys, read signatures (§3.2) |
| 3 | **Search** | agents → Retrieval | `search_filings(query, companies, periods, forms, k)` | arguments, result schema, chunk ids (§4.2) |
| 4 | **Tool calls** | agent ↔ toolbox | JSON-schema arguments in, JSON with ids out | tool sets, signatures, result schemas (§5.2) |
| 5 | **LLM calls** | code ↔ model | system prompt + messages; JSON reply | per-call reply schema and budget (§5.2) |
| 6 | **Extraction rows** | extractor → Store | KPI and guidance rows with chunk id and quote | registry, reply and row schemas (§6.2) |
| 7 | **Templates** | config → Model engine | YAML lines with DSL expressions | node kinds, DSL, anchors, scenarios (§7.2) |
| 8 | **Model and valuation files** | Model engine → Report, tools, Export | `model.json`, `valuation.json` | typed schemas (§7.2) |
| 9 | **Pipeline artifacts** | S1 … S11 | JSON files in the report folder | step I/O and artifact schemas (§8.2) |
| 10 | **Reference ids** | everywhere | strings | grammar and resolvers (§9.2) |
| 11 | **Fact list** | Report code → note writer | `{id, label, value, unit, source, period, filed}` | §9.2 |
| 12 | **Outputs** | system → reviewer | `note.md/.pdf`, `workbook.xlsx`, JSON files, version status | §10.2 |
| 13 | **Gold and results** | Evaluation ↔ files | JSONL gold, result folders | §11.2 |
| – | **Config** | everything | `config.yaml`, `companies.yaml`, `research.yaml`, `kpis.yaml`, `metrics.yaml`, templates | Appendix A |
| – | **CLI** | user → system | `tenk …`, `python -m …` | Appendix B |

### 2.4 Data flow and control flow

<p align="center">
  <img src="img/v4-architecture.svg" alt="V4 data and control flow. Sources are fetched into a raw cache and built offline along four lanes (text, structured facts, KPI and guidance extraction, market data) into one SQLite file read only through the AsOf filter. Ask and Research are agent runs whose answers pass a claim check that resolves every cited id. Report is a fixed pipeline of eleven steps, S1 to S11, with the LLM only in S2, S4, S9 and S10; it writes a note, a workbook and a saved model version that Ask and Research can cite. Evaluation grades extraction, model integrity, forecast, valuation and the note." width="100%">
</p>

**Build (offline):** `tenk fetch` → raw cache → `tenk build` stages: text (chunk, load, embed), facts (XBRL, inline XBRL), KPIs (LLM extraction), market (prices, macro, ERP, cross-checks). New filings are appended without a rebuild (`--stage append`).

**Run (per question or report, at one as-of):**

| Mode | Control | Path through the components | New numbers from |
|---|---|---|---|
| **Ask** | The agent picks tools (tool set `ask`) | loop → toolbox → Store, Retrieval, saved versions → claim check → answer | a calculator over reported figures |
| **Research** | The agent picks tools (tool set `research`) | as Ask, plus model tools → model book → Model engine | model tools only (`model:` ids) |
| **Report** | Code runs S1–S11; LLM calls are leaf tasks | pipeline → Model engine, sub-agents, writer → Verification → Export → `model_versions` | the Model engine only |

Ask and Research are one agent with two tool sets. Report is not an agent loop and never starts an Ask or Research run (spec §2.3). Ask and Research read a saved Report version back through `research_report` and `model:…:v<n>:…` ids.

### 2.5 Cross-cutting rules and constraints

**The as-of rule** (D-040). `AsOf(D)` is the world at the market open of D, US/Eastern. Data is visible when it became public **strictly before** D:

| Data | Visible when | Column |
|---|---|---|
| Filings (any form), their chunks, facts, KPIs, guidance | `filing_date < D` | `filed` |
| Tiingo close | `date < D` (as-of 2026-08-06 sees the 08-05 close) | `published` |
| FRED / ALFRED | observation date `< D`, and for revised series (CPI, UMCSENT, RSFSDP) vintage `realtime_start < D`; DGS10 counts as published the day after its date | `published` |
| Damodaran ERP | monthly file, published three days after the month starts | `published` |
| BTS, NYC TLC | publish date (BTS: month end + 90 days, estimated) | `published` |
| Our saved versions | `as_of < D` | `as_of` |

| Rule | Reason |
|---|---|
| Strictly before D | The spec says so (§1.2); it matches its example and is the safest for a backtest (D-040) |
| `asof.py` (`sees`, `sql`, `latest`) is the only code that compares dates | One place to audit and to flip (D-040) |
| A `Store` is bound to one `AsOf` when opened, and every read filters through it | No read path can skip the filter, and V3's call sites stayed unchanged ([build log §2](V4_BUILD_LOG.md#2-migration-from-v3), step M3) |
| No tool takes an as-of that widens the view (`research_report`'s `as_of` only narrows it) | The model cannot see past the run's date by asking *(new)* |
| `.latest()` returns, per (concept, period), the value from the newest filing visible at D | A restatement counts only once it is filed, so an earlier as-of sees the original (spec §1.6) |

**The no-LLM-numbers rule** (D-038). LLM output reaches the numbers only as (a) extracted rows that code validates against a verbatim quote (§6.3), (b) bounded assumption proposals that code clips (§7.3), (c) prose in which every number is a marked fact (§9.3). Tables are written by code. Reason: the spec requires every number from deterministic code (spec §3), and it makes layer 4's byte-identical rerun possible.

**Errors.** Tools never raise: a bad call returns `{"error": …}` for the model to read (§5.2), so one bad argument doesn't end a run (V3, D-036). Report code steps raise on a failed integrity or valuation check and stop the run; LLM steps degrade (§8.4), as spec §2.3 requirement 7 asks. Builders log every gap as an exception row, never a guessed value (spec §2.3 note rules).

**Logging and trace.** Every number in an answer or note carries an id (§9.2). Agent runs keep their transcript and tool calls; `report.json` records each step's seconds, LLM calls and tokens (`timing`); `raw/manifest.json` records each file's retrieval time.

**System constraints** (each section lists its own):

| Constraint | Value | Source |
|---|---|---|
| LLM context window | 32k tokens (vLLM, Qwen3.6-27B-FP8) | I-003, D-029 |
| GPU | one 46 GB L40S, shared by vLLM and the embedder; the judge runs as a second pass | I-002, D-029 |
| CPU | Python segfaults on the P-cores: heavy runs under `taskset -c 16-31` | I-001 |
| SEC rate limit | 10 requests/s allowed; the client sends one per 0.12 s | spec §1.6 |
| Licences | Tiingo internal use only: raw prices never leave `data/` | D-044 |
| Report time | under 30 minutes per company | spec §2 |
| Coverage | December fiscal years; filings from 2023-01-01; CART from Q3 2023 | spec §1.1, D-038 |

---

## 3. Data layer

### 3.1 Components

| Component | Code | Responsibility |
|---|---|---|
| Companies | `corpus.py`, `companies.yaml`, `acquisition/companies.py` | The coverage list, sectors and templates; `tenk companies verify` fills CIKs and checks FYE and filing counts against SEC |
| EDGAR filings | `acquisition/edgar.py`, `acquisition/filings.py` | Fetch 10-K, 10-Q (one per form and period), 8-K 2.02 exhibits `EX-99.*` and DEF 14A; write the raw cache and manifest |
| Market | `acquisition/market.py` | Tiingo daily prices (+ SPY), ALFRED series, Damodaran ERP, each with its publish date |
| Cross-checks, insiders | `acquisition/crosscheck.py`, `acquisition/insiders.py` | NYC TLC trips per license, BTS airline passengers; Form 4 transactions |
| Text processing | `preprocess/forms.py`, `preprocess/rules.py`, `chunking/`, `retrieval/index.py` | Form-aware Items, parent-child chunks, FTS5 and vectors |
| Facts | `facts/xbrl.py`, `facts/quarters.py`, `facts/metrics.yaml` | Company facts and inline XBRL into `facts`; quarters derived on read |
| Store | `store.py`, `asof.py` | Schema, writes at build time, as-of-filtered reads |

### 3.2 Interfaces

**Raw cache** (`data/v4/raw/`): `filings/<T>/<period>-<form>/` (`FY2025-10K`, `2026Q2-10Q`, `2026Q2-ER`, `FY2025-DEF14A`), each with its documents and a `metadata.json`; `edgar/<T>/companyfacts.json`; `prices/`, `macro/`, `crosscheck/`. `raw/manifest.json` maps each ticker to its filings:

```json
{"UBER": [{"ticker": "UBER", "cik": 1543151, "form": "10-K", "accession_no": "0001543151-23-000010",
           "filing_date": "2023-02-21", "accepted_at": "2023-02-21T21:10:08.000Z", "period_end_date": "2022-12-31",
           "fiscal_year": 2022, "fiscal_period": "FY",
           "documents": [{"name": "uber-20221231.htm", "type": "10-K", "url": "https://www.sec.gov/…"}],
           "retrieved_at": "…"}]}
```

**`companies.yaml`** (pinned per experiment; `TENK_COMPANIES` selects it):

```yaml
data_dir: data/v4
window: {since: '2023-01-01'}
companies:
  UBER:
    name: Uber Technologies, Inc.
    cik: 1543151            # written by `tenk companies verify`, never typed
    sector: mobility        # peers = same sector
    template: segments      # §7.2
    main_kpi: gross_bookings
```

**Store schema** (one SQLite file, D-005; V4 tables, plus V0–V3's `companies`, `sections`, `chunks`, `children`, FTS5 and vectors):

| Table | Columns (type; key) | As-of column |
|---|---|---|
| `filings` | `accession_no` TEXT PK, `ticker`, `form` (`10-K`/`10-Q`/`ER`/`DEF14A`), `fiscal_year` INT, `fiscal_period` (`FY`, `Q1`–`Q3`; a release: the quarter it reports), `period_end_date`, `filing_date`, `accepted_at` (UTC), `source_url`, `retrieved_at` | `filing_date` |
| `chunks`, `children` | `id` TEXT PK (§3.4), `ticker`, `fiscal_year`, `item`, `kind`, `text`, `units`, `printed_pages`, `form`, `fiscal_period`, `filed`; children add `parent_id` → chunks | `filed` |
| `facts` | `id` TEXT PK (`xbrl:`/`ixbrl:` id), `ticker`, `taxonomy`, `concept`, `dims` (JSON `{axis: member}`, `{}` = consolidated), `start` (NULL for an instant), `end`, `value` REAL, `unit`, `decimals`, `accession`, `form`, `fy`, `fp`, `filed`, `source` (`companyfacts`/`ixbrl`) | `filed` |
| `kpis` | `ticker`, `metric`, `segment` (`total` = consolidated), `period`, `value` REAL, `unit`, `definition_version` INT, `chunk_id`, `quote`, `method` (`llm`/`rules`), `filed` | `filed` |
| `guidance` | `ticker`, `metric`, `segment`, `target_period`, `low`, `high` REAL, `unit`, `basis` (`level`/`growth_yoy`/`margin`), `chunk_id`, `quote`, `issued_in`, `filed`; PK (ticker, metric, segment, target_period, issued_in) | `filed` |
| `market` | `series` (`px:<T>:close\|adj_close\|volume`, `macro:<SERIES>`, `erp:damodaran`, `cc:…`), `date`, `value` REAL, `published` | `published` (and `date`) |
| `insider_tx` | `ticker`, `accession`, `filed`, `owner`, `director`, `officer`, `title`, `date`, `code` (S, P, A, M, F, G), `shares`, `price`, `acquired_disposed` | `filed` |
| `model_versions` | `id` TEXT PK (`<T>:v<n>`), `ticker`, `as_of`, `parent_id`, `inputs_hash`, `status` (`draft`/`reviewed`), `path` (report folder) | `as_of` |

Quarters are **not stored** (§3.3). A saved version's numbers live in its folder's files (§8.2), not in tables.

**Store read API** (every method filtered by the store's `AsOf`; `Store(path, asof)`):

| Method | Returns |
|---|---|
| `search(query, tickers, fiscal_years, items, k, periods, forms)` · `vector_search(…)` · `child_search(…)` · `child_vector_search(…)` | `list[ChunkRecord]`, BM25 or cosine ranked |
| `get_chunk(chunk_id)` · `chunks(ticker, fiscal_year)` | `ChunkRecord \| None` · `list[ChunkRecord]` |
| `filing(ticker, fiscal_year)` · `filings()` | `FilingRecord \| None` · `list[FilingRecord]` |
| `facts(ticker, concepts, dims=None, any_dims=False)` · `fact(fact_id)` | fact rows as dicts (`dims` parsed) |
| `kpis(ticker, metric=None)` · `guidance(ticker, metric=None)` | row dicts |
| `series(name, start=None)` | `list[(date, value)]` visible at D |
| `insider_summary(ticker, start, end)` | counts and shares by transaction code |
| `model_versions(ticker)` | version rows with `as_of < D`, oldest first |

Writes (`add_filing`, `add_facts`, `add_extractions`, `add_market`, `add_insider_tx`, `add_embeddings`) happen at build time; `add_model_version` (Report) and `set_version_status` (review) at run time.

### 3.3 Rules and algorithms

<p align="center">
  <img src="img/v4-data-pipeline.svg" alt="The V4 build: fetch into a raw cache, then four lanes (text, structured facts, KPI extraction, market data) into one SQLite file" width="100%">
</p>

| Rule | Reason |
|---|---|
| One filing per (form, period); amendments ignored | One value per period; the amendment, when it matters, restates a later filing *(new)* |
| Releases: 8-K rows with Item 2.02; every `EX-99.*` from the filing index | A release can carry two `EX-99.1` documents (BKNG 2026-08-04; build log §3.2) |
| A release's period = the last quarter end before it was filed, cross-checked against the 10-Q | Releases carry no XBRL period; the check matched every case (build log §3.6) |
| DEF 14A: the first proxy each year (DEFC14A when contested), keyed to the prior fiscal year | Later ones that year are special meetings (spec §1.2) |
| Fetch cache-first; builds offline | Reproducible builds and evaluations (D-036) |
| Rules, not the LLM, structure V4 filings (`FormSpec` per form) | Same Items as the LLM agent on every shared block, at a fraction of its 6 GPU-hours (D-039) |
| Tables stay whole chunks | D-003 |
| Inline XBRL read for every company-specific and dimensional fact | Company facts has no dimensional facts and drops company concepts (build log §3.3) |
| Quarters derived on read, not stored | Nothing goes stale between as-ofs (build log §3.6) |
| Within a fiscal year a metric uses one concept (`metrics.yaml` priority order) | Mixing SBC's income-statement and cash-flow concepts broke Q2 by $3–6M (build log §3.6) |
| A year whose filings disagree after a recast reads `restated`, logged, not `fail` | A recast (TRIP 2025) is a known event, not a bad number (build log §3.6, spec §1.6) |
| EPS and share counts: direct facts only; Q4 EPS from the release | They don't add up across quarters (build log §3.6) |
| DGS10 published the day after its date; Damodaran ERP three days into the month | FRED's H.15 timing; the ERP file's release pattern (build log §3.6) |
| NYC TLC aggregated to trips per month per license at fetch | Each month's Parquet is ~540 MB (build log §3.5) |
| Sources that block scripts are dropped, never worked around | Their terms (Stooq, TSA, Google Trends; build log §3.5) |

<p align="center">
  <img src="img/v4-quarters.svg" alt="Quarter derivation: Q1 direct, Q2 and Q3 direct or year-to-date difference, Q4 as FY minus 9M, newest fact visible at the as-of date, checks, financials tool" width="100%">
</p>

**Quarters** (`facts/quarters.py`): ~90-day facts direct; Q2 = H1 − Q1 and Q3 = 9M − H1 when only year-to-date exists; Q4 = FY − 9M (spec §1.6). Instants need no derivation. Each quarter records its derivation (`direct`, `ytd_diff`, `fy_minus_9m`), input fact ids and `filed` (the latest input). **Ids:** `xbrl:<T>:<tax>:<concept>:<accession>:<start>/<end>` for durations (three-month and year-to-date facts share an end date, build log §3.3), `…:<end>` for instants; `q:<T>:<metric>:<period>` for a derived quarter.

### 3.4 Text processing

| Form | Items | Chunk id example |
|---|---|---|
| 10-K | as V0 (1, 1A, 7, 7A, 8, 15, …) | `UBER-FY2025-7-003` |
| 10-Q | per Part: `I.1`…`I.4`, `II.1`, `II.1A` (Items repeat per Part) | `UBER-2026Q2-I.2-014` |
| Release | one pseudo-Item `EX99` | `UBER-2026Q2-ER-EX99-005` |
| DEF 14A | one pseudo-Item `PROXY` | `UBER-FY2025-DEF14A-PROXY-012` |

Chunks are parent-child (V0); chunk headers name the form and period ("Uber Q2 2026 10-Q, Part I Item 2 MD&A") so a chunk read alone says where it is from.

### 3.5 Failure handling

| Failure | Handling |
|---|---|
| Source unreachable | The cached copy is used; an offline build never fetches |
| A company's fetch fails | Logged; the others continue (`fetch_records_filings_and_skips_failed_companies`) |
| Release period mismatches its 10-Q, or two filings claim one period | Fetch error, flagged |
| Missing concept or quarter | Left blank; logged when the model reads it (§7.4) |
| Recast year | `restated`, logged |
| A filing's Item structure not found | The validator reports it; the filing's chunks keep the Items found |

### 3.6 Constraints

SEC: one request per 0.12 s with an identifying User-Agent. Tiingo free plan: 1,000 requests/day, 500 symbols/month, internal use only. FRED: an API key. NYC TLC: ~540 MB per month of Parquet. December fiscal years only (checked by `tenk companies verify`). Filings from 2023-01-01; CART from Q3 2023. Three releases (ABNB, CART through Q4 2025, BKNG since Q4 2024) are slide images with a hidden text layer: text, no tables (build log §3.6).

### 3.7 Tests

| File | Asserts |
|---|---|
| `test_asof.py` | strictly-before; `.latest()` takes the newest visible restatement; a store sees nothing from a filing until the day after; V4 modules always pass an `AsOf`; V3 databases migrate and backfill dates |
| `test_facts.py` | quarters from year-to-date facts; no Q4 before the 10-K; a restatement replaces the original once filed; one concept per year; inline facts keep dimensions and company concepts; a recast year reads `restated` |
| `test_filings_v4.py` | strict quarter-before; period keys; releases checked against reports; two filings for one period flagged; EX-99 rows from the index; proxies keyed to the prior year |
| `test_market.py` | an as-of sees the previous close; revised series use the vintage known then; daily series are published the next day; ERP fetch records its retrieval |
| `test_acquisition.py` | User-Agent required; window and dedupe; Item 2.02 8-Ks only; `verify` flags FYE, CIK and missing forms |
| `test_store.py`, `test_rules.py` | chunk loading and filters by period and form; KPIs and guidance dated by their release; 10-Q Items carry their Part; release exhibits become pseudo-Items |

V3 regression: `experiments/v4/v3_regress.py` checks byte identity of the shared pipeline on the V3 corpus.

---

## 4. Retrieval

### 4.1 Components

| Component | Code | Responsibility |
|---|---|---|
| Decomposer | `retrieval/decompose.py` | Resolve companies and periods (slots) from arguments or the query; one sub-question per (company, filing) |
| Searcher | `retrieval/search.py` `SubQuestionRetriever` | BM25 + dense search per sub-question under the store filter; fusion; parents; rerank; merge |
| Index | `retrieval/index.py` | Chunk headers, embeddings (Qwen3-Embedding-0.6B), FTS5 |
| Reranker | `retrieval/` (bge-reranker-v2-m3) | Cross-encoder score of each parent against its sub-question |

No LLM. Stage spec: [V1_RETRIEVAL.md](stages/V1_RETRIEVAL.md).

### 4.2 Interfaces

```text
search_filings(query: str, companies?: [str], periods?: [str], forms?: [str], k?: int = 8)
  companies   tickers or names; ["all"] = every company
  periods     period keys ("2026Q2", "FY2025"); none = the latest filing of each form at the as-of
  forms       "10-K" | "10-Q" | "ER" | "DEF14A"
→ {"query": str,
   "searches": [{"query": str, "company": str, "periods"?: [str]}],
   "chunks": [{"chunk_id", "company", "form", "period", "filed", "item", "kind", "units", "printed_pages", "text"}]}
```

Chunk ids: `<TICKER>-<period>[-<doc>]-<item>-<nnn>` (§3.4). Knobs: `config.yaml` `retrieval` (Appendix A).

### 4.3 Rules and algorithms

<p align="center">
  <img src="img/v4-search.svg" alt="search_filings in V4: slots resolved from arguments or query, one sub-question per company and filing, keyword and dense search under the as-of filter, fusion, parents, rerank, round-robin merge" width="100%">
</p>

| Step | Rule | Reason |
|---|---|---|
| Slots | Companies from the arguments, else the query; periods to keys; none = latest filing of each form at D | A comparison must name its companies; "latest" must respect the as-of (D-010) |
| Sub-questions | One per (company, filing), rewritten to name only that company | A multi-company question otherwise retrieves one company's chunks (D-010, V1) |
| Search | BM25 over FTS5 and dense over children, top 40 each, under the store filter | Keywords catch figures and names, vectors catch paraphrase (V1) |
| Fusion | Reciprocal rank fusion, k = 60 | Rewards agreement between the two lists without tuning scores (V1) |
| Parents | Each child swapped for its parent | Children match precisely; parents give the model the whole subsection or table (V0, D-037) |
| Rerank | Cross-encoder per parent and sub-question | Best precision at the top (V1) |
| Merge | Round-robin across sub-questions until k | No company crowds out the others (V1) |

### 4.4 Failure handling

No company found → `{"error": "name the companies to search…", "corpus": …}`. An unknown company → an error listing the corpus. A bad period key → an error. A slot with no filing at that as-of contributes nothing; the agent sees fewer chunks, not an error.

### 4.5 Constraints

The embedder runs on CPU when vLLM holds the GPU (`retrieval.device: cpu`, I-002). Its vectors must match the database's (`tenk build --stage embed`). k = 8 chunks per call (each up to the parent size, 1,280 tokens).

### 4.6 Tests

`test_retrieval.py`: company aliases resolve; sub-questions name only their company and fan out; every filing named returns evidence; vector search orders by cosine; RRF rewards agreement; given companies and years replace those in the question. `test_asof.py` covers the filter. The V1 retrieval gold (V3 corpus) runs through the regression check.

---

## 5. Agent runtime

### 5.1 Components

| Component | Code | Responsibility |
|---|---|---|
| Loop | `agent/loop.py` `Agent(llm, toolbox, max_calls, max_tokens, max_context, prompt)` | LangGraph tool-calling loop: budget, repeat guard, forced answer turn, JSON answer, claim check |
| Toolbox | `agent/tools.py` `Toolbox(store, search, sec, tool_set, book)` | Tool groups and schemas; dispatch; never raises; sees what its store's `AsOf` sees |
| Prompts | `prompts/` | Rules and reply schema per call: `ask.md`, `research.md`, `events.md`, `debates.md`, `propose.md`, `extract.md`, `section.md`, `judge.md` |
| Sub-agents | `research/subagents.py`, `research/propose.py` | Bounded `Agent` runs for S2 events, S4 proposer, S9 debates; keep only items with resolvable evidence |
| Model book | `research/book.py` `Book` | Session models per (ticker, as-of) for the model tools and `model:` ids; builds a peer with defaults on demand |
| LLM | `models.py` `Model.complete` | vLLM (Qwen3.6-27B-FP8, D-029) or Claude, one interface (D-013) |

### 5.2 Interfaces

**Tool sets.** A tool set is a list of groups:

| Group | Tools | `ask` | `research` | Sub-agents (S2, S4, S9) |
|---|---|:-:|:-:|:-:|
| text | `search_filings` | ✓ | ✓ | ✓ |
| calc | `calculator` | ✓ | ✓ | ✓ |
| facts | `financials`, `company_kpis`, `guidance`, `filings` | ✓ | ✓ | ✓ |
| market | `market_data`, `crosscheck` | ✓ | ✓ | ✓ |
| reports | `research_report` | ✓ | ✓ | ✓ |
| model | `model_view`, `run_scenario`, `compare_peers`, `variance_review` | | ✓ | |

V3's sets `text` and `full` (with `xbrl_facts`, `edgar_filings`) remain for the V3 corpus.

**Tool signatures and results** (all as-of bound; `company` = ticker or name; `periods` = period keys, default the latest eight; tables are capped at 40 rows with `"omitted": n`):

| Tool | Arguments (required in bold) | Result |
|---|---|---|
| `search_filings` | **query**, companies, periods, forms, k | §4.2 |
| `calculator` | **inputs** `{name: number}`, `op` or `expression` | `{result, formula, inputs}` |
| `financials` | **company**, **metric**, periods | `{company, metric, rows: [{id (q:/xbrl:), period, value, unit, derivation, restated?}]}` |
| `company_kpis` | **company**, metric, segment, periods | `{company, rows: [{id (kpi:), metric, segment, period, value, unit, chunk_id, quote}]}` |
| `guidance` | **company**, metric, periods | `{company, rows: [{id (guide:), metric, segment, period, low, high, unit, basis, issued_in, chunk_id, quote, actual?, actual_id?}]}` |
| `filings` | **company**, form | `{company, filings: [{id, accession_no, form, period, period_end_date, filing_date}], total}` |
| `market_data` | company, series, dates, start | `{series, unit, rows: [{id (px:/macro:), date, value}]}` |
| `crosscheck` | **series** (`tlc`/`bts`), companies, start, end | `{series, coverage, unit, rows: [{id (cc:), month, value}]}` |
| `research_report` | **company**, lines, periods, as_of | `{company, version, as_of, status, parent, note, valuation: {price, price_target, return, rating: {value, id}}, estimates: [{line, period, kind, value, unit, id}], versions}`; none → `{report: null, versions, note}` |
| `model_view` | **company**, lines, periods | `{company, as_of, forecast_from, id_format: "model:<T>:<as_of>:base:<line>.<period>", rows: [{line, unit, values: {period: value}}], assumptions: [{driver, value, default, source: default \| guidance <id>}]}` |
| `run_scenario` | **company**, overrides `{assumption: value}`, deltas `{assumption: delta}` | `{company, version (scratch-n), changes, clipped_to_bounds, rows: [{line, period, base, scenario, id, base_id}], price_target: {base, scenario, id, base_id}, rating: {base, scenario}}` |
| `compare_peers` | **metric**, companies, period | `{metric, period, rows: [{company, value, unit, id, assumptions: [{driver, value, source}]}]}` |
| `variance_review` | **company**, **period** | `{company, period, estimate_as_of, rows: [{line, unit, estimate, estimate_id, actual, actual_id, variance_pct}], bridge, bridge_ids, bridge_unit}` |

**Errors** are results: `{"error": "…"}` plus the valid choices (`metrics`, `kpis`, `lines`, `corpus`, `tools`).

**LLM calls** (each reply is one JSON object, parsed by `models.parse_json`):

| Call | Prompt | Tools | Reply schema | Budget |
|---|---|---|---|---|
| Ask / Research answer | `ask.md` / `research.md` | `ask` / `research` | `{answer, claims: [{id, text, value, unit, company, period, chunk_ids, calculation: {op, expression, inputs, input_unit, result} \| null}], assumptions (Research): [{driver, value, source}], table: {columns, rows} \| null, scope_notice, abstained}` | `config.yaml` `agent`: 20 calls, 20k context |
| KPI extraction | `extract.md` | – | §6.2 | one call + one repair per release |
| S2 events | `events.md` | `ask` | `{answer, events: [{type: results\|guidance\|definition_change\|business_model\|m&a\|capital\|regulation\|catalyst\|other, summary, evidence: [id]}]}` | `budgets.events`: 8 calls, 16k context, 3k out |
| S4 proposer | `propose.md` | `ask` | `{proposals: [{driver, value, basis, confidence, rationale, evidence: [id]}]}` | `proposer.budget`: 10 calls, 16k context, 3k out |
| S9 debate, per driver | `debates.md` | `ask` | `{answer, debate: {question, bull, bear, hypothesis, trigger, gap, evidence: [id]}}` | `budgets.debate`: 8 calls, 16k context, 3k out |
| S10 prose slot | `section.md` | – | `{text}` with facts marked `{F-n}` | `budgets.section`: 1.5k out, one repair |
| Judge (evaluation) | `judge.md` | – | scores per criterion | gpt-oss-20b, a second pass |

### 5.3 Rules and algorithms

```text
question ─► model ──tool calls──► tools ─► model ─► … ─► JSON answer ─► claim check (§9.3)
```

| Rule | Reason |
|---|---|
| The first turn must be a tool call | No answer from the model's memory (V3) |
| A repeated identical call returns an error and still counts | Stops loops (V3) |
| Calls past `max_calls` return an error; after the budget or at `max_context` tokens, one turn with tools off to answer | Every run ends with an answer within the window (V3, I-003) |
| An unparseable answer gets one "only the JSON" retry | A formatting slip is not a wrong answer (V2) |
| Tool groups; no context gets every tool | Smaller schemas in a 32k window, and a mode can't call what it shouldn't *(new)* |
| Results capped at 40 rows and `result_chars` | Keeps one tool result from filling the window *(new)* |
| Ask cites estimates only through `research_report`; Research's computed numbers only from model tools, assumptions listed | Spec §2: Ask retrieves, Research computes |
| Sub-agents return JSON, not transcripts; an item is kept only if one evidence id resolves at D | Artifacts, not transcripts; no unsupported claim reaches the note *(new)* |
| A debate's resolved evidence values become note facts | Its numbers can then be traced (build log §3.15) |
| `run_scenario` writes a session-only `scratch-n` run | Only Report creates versions *(new)* |
| Peers and model-book entries are built with defaults (no LLM) | A peer's numbers don't depend on an LLM run, and each is built once per as-of *(new)* |

<p align="center">
  <img src="img/v4-research-tools.svg" alt="run_scenario, compare_peers and variance_review: validate, rerun or reuse cached code runs, return tables with citable ids" width="100%">
</p>

**Context management.** State lives in files, not chat; each call gets a purpose-built view (the proposer a driver table, a section writer only its own facts); detail is fetched by id, never preloaded.

### 5.4 Failure handling

| Failure | Handling |
|---|---|
| Unknown tool, argument or assumption | `{"error": …}` with the valid choices; the model retries |
| Budget spent or context full | Forced answer turn with tools off |
| Answer not JSON after one retry | Recorded as unparsed; the claim check sees no claims |
| Sub-agent: bad JSON after repair, or no resolvable evidence | Returns nothing; its Report step degrades (§8.4) |
| Proposal out of bounds | Clipped and logged (§7.3) |
| vLLM unreachable | The call raises; a Report run stops at that step and `--resume` reruns from saved outputs |

### 5.5 Constraints

32k-token window (I-003): Ask and Research 20k context and 20 calls; sub-agents 16k and 8–10 calls. Tool result ≤ `result_chars` (16,000). LLM steps at temperature 0. The judge can't share the GPU with the 27B model, so judging is a second pass (D-029).

### 5.6 Tests

`test_agent_loop.py`: tool results feed the next turn; calls past the budget error and force an answer; a non-JSON reply gets one retry; a repeated call errors and counts; a long conversation answers with tools off. `test_agent_tools.py` (21): calculator formula and errors; offline SEC data never fetches; a value matching a cited fact verifies; another company's fact doesn't. `test_ask.py`: guidance targets become period keys; data ids resolve at the store's as-of; claims verify against data rows; the prompt names the as-of and coverage. `test_forecast.py::proposals_are_bounded_anchors_kept_and_evidence_required`. Gold: Ask (79) and Research (72) (§11).

---

## 6. Extraction (layer 1)

### 6.1 Components

| Component | Code | Responsibility |
|---|---|---|
| LLM extractor | `facts/extract.py` | One schema-constrained call per release; code validation; one repair; exception rows |
| Rules extractor | `facts/rules.py` | Label-pattern table-row matcher (the ablation; `extract.method: rules`) |
| Registry | `facts/kpis.yaml` | Per company: each KPI's key, unit, segments, labels, definition version |
| Loader | `tenk build --stage kpis` | Writes validated rows to `kpis` and `guidance` |

### 6.2 Interfaces

**Registry** (seeded from spec §1.5):

```yaml
UBER:
  gross_bookings: {unit: USD millions, segments: [total, mobility, delivery, freight], labels: ["Gross Bookings"]}
  trips:          {unit: count millions, labels: ["Trips"]}
```

**Reply** (`extract.md`):

```json
{"kpis": [{"metric": "gross_bookings", "segment": "mobility", "value": 28988, "unit": "USD millions",
           "chunk_id": "UBER-2026Q2-ER-EX99-004", "quote": "| Mobility | $23,762 | $28,988 | 22% | 20% |"}],
 "guidance": [{"metric": "gross_bookings", "segment": "total", "target_period": "Q3 2026", "low": 58250, "high": 60250,
               "unit": "USD millions", "basis": "level", "chunk_id": "UBER-2026Q2-ER-EX99-002",
               "quote": "Gross Bookings of $58.25 billion to $60.25 billion"}]}
```

**Stored rows:** the `kpis` and `guidance` tables (§3.2): `target_period` normalized to a key (`2026Q3`), plus `period`/`issued_in` (the release's quarter), `filed`, `definition_version`, `method`. Ids: `kpi:<T>:<metric>[.<segment>]:<period>`, `guide:<T>:<metric>:<period>`.

### 6.3 Rules and algorithms

<p align="center">
  <img src="img/v4-extraction.svg" alt="KPI and guidance extraction: one LLM call per release, code validation of quote and value, one repair turn, exception rows, reconciliation with XBRL" width="100%">
</p>

| Rule | Reason |
|---|---|
| Input: the release's chunks, tables first, up to 40k characters; slide-image releases send the chunks naming the most registry labels | Fits the 12k-token budget with the registry (§6.5); slide releases have no tables (build log §3.6, §3.10) |
| Thinking off, temperature 0 | With thinking on, every call spent its tokens reasoning and returned no JSON (build log §3.7) |
| The chunk id must be one it was given; the quote must occur verbatim; the value (guidance: low and high) must be found in the quote, unit-aware | The LLM proposes, code decides what is stored (no-LLM-numbers rule, §2.5) |
| No elided quotes ("…"); a series is quoted from label to number | Elided quotes failed the verbatim check (build log §3.7) |
| A prior-year column is rejected (`growth_pair` finds the later column) | Two-column tables invite the prior-year value (build log §3.7) |
| Metric and segment must be in the registry | The model can't invent KPIs *(new)* |
| One repair turn showing the chunk text, then an exception row (data gap) | Fixed most misses (build log §3.10); never a guessed value (spec §2.3) |
| GAAP diluted EPS only | Non-GAAP EPS was taken for GAAP (build log §3.10) |
| Where a KPI is also an XBRL fact, the filing should win and the gap be logged | Spec §1.6; **not built** (§13.1) |
| A recast release gives prior periods a new `definition_version`; readers take the newest, the log keeps the old | Spec §1.6: restate history and note it |
| Guidance keeps its basis; `forecast.anchor()` converts growth to a level via the year-ago actual | Releases guide in dollars, growth or margins (build log §3.4) |

### 6.4 Failure handling

A row failing validation after repair → an exception row (data gap), never stored. A release with no valid rows → its KPIs are missing; the model logs the gap (§7.4). No numeric guidance (TRIP) → the guidance baseline reads "n/a". A release too long to send whole → registry-matched chunks first (build log §3.10).

### 6.5 Constraints

≤ 12k input tokens per release; `MAX_CHARS` 40,000. Slide-image releases: text only. Registry units are fixed per KPI.

### 6.6 Tests

`test_extract.py`: valid rows kept and bad ones repaired once; validation rejects an unknown metric, segment or a value not in its quote; only that release's visible chunks are sent; `growth_pair` finds the later column; `definition_version` follows a recast; the registry covers all eight; a quote miss shows the chunk text. Gold: 337 labels, `evaluation/gold/extraction-gold.jsonl`; result 98.5% exact, 100% critical (§11.3).

---

## 7. Model engine and valuation

### 7.1 Components

| Component | Code | Responsibility |
|---|---|---|
| Data pack (S1) | `research/datapack.py` | Every model input with its id and filing date; split adjustment to the latest filing's basis |
| DSL and graph | `research/model/dsl.py`, `graph.py` | Parse expressions; lines × periods; evaluate in Python; write Excel formulas |
| Templates | `research/model/templates/*.yaml` | `statements` + one of `marketplace`, `segments`, `orders`, `trip` |
| History (S3) | `research/model/history.py` | Fill inputs from the data pack |
| Layer-2 checks | `research/model/checks.py` | Integrity checks; any FAIL stops |
| Forecast and scenarios (S5) | `research/forecast.py` | Defaults, anchors, seasonal shares, fade, bear/base/bull |
| Outside inputs (S5) | `research/outside.py` | Move mapped starting growth rates toward a nowcast from TLC, BTS or FRED (`forecast.outside`) |
| Proposer (S4) | `research/propose.py` | Bounded LLM assumptions (`proposer.method`, default `defaults`) |
| Valuation (S6–S7) | `research/valuation.py` | Peers, market cap and EV, multiple and DCF PTs, ratings |
| Model book | `research/book.py` | Cached models per (ticker, as-of) |

### 7.2 Interfaces

**Template YAML.** Lines are nodes; history comes from a `source` (with an optional `fallback`), the forecast from an expression:

```yaml
lines:
  volume:         {kind: input, source: "kpi:total_orders", unit: count millions, label: orders}
  gross_bookings: {kind: input, source: "kpi:marketplace_gov", unit: USD millions,
                   forecast: "volume * value_per_unit"}
  take_rate:      {kind: formula, expr: "revenue / gross_bookings", unit: fraction}
  revenue:        {kind: input, source: "q:revenue", fallback: "kpi:revenue", statement: income,
                   forecast: "gross_bookings * take_rate"}
forecast: {growth: [volume, value_per_unit], ratios: {take_rate: take_rate, cost_to_gb: margin}}
scenarios: [volume_g.g0, value_per_unit_g.g0, take_rate.shift0]
anchors: …   # guide name as each company prints it → the line it anchors
```

**DSL:** arithmetic; `x[-4]` (same quarter last year); `fy(x)`, `fyp(x)` (this and last fiscal year's sum); `min`, `max`, `if_`, `first`; `@name` = an assumption. Node kinds: `input` (history, blue, provenance), `assumption` (blue), `formula` (black). Anything else is rejected (`test_model_dsl.py::rejects_anything_else`).

**`model.json`:**

| Field | Type | Meaning |
|---|---|---|
| `ticker`, `template`, `as_of` | str | |
| `columns` | [str] | quarters and FY columns in order |
| `forecast` | [str] | forecast quarters |
| `assumptions` | {name: number} | the Assumptions tab |
| `lines` | [{`name`, `kind`, `label`, `unit`, `statement`, `fy`, `source`, `expr`, `forecast`, `forecast_first`}] | the template, resolved |
| `cells` | {"line\|period": {`value`: number\|null, `kind`: input\|formula, `ref`: id\|null, `filed`: date\|null}} | every value; inputs carry `ref` and `filed` |

**`valuation.json`:**

| Field | Type | Meaning |
|---|---|---|
| `ticker`, `as_of`, `price_date` | str | |
| `price`, `shares`, `market_cap`, `debt`, `cash_and_investments`, `net_debt`, `ev` | number | USD, millions where amounts |
| `refs` | {price, shares, debt, cash, short_term_investments: id} | sources |
| `cost_of_capital` | {`rf`, `beta`, `beta_months`, `erp`, `erp_id`, `ke`, `kd`, `debt_weight`, `wacc`} | §7.3 |
| `peer_multiples` | {`peers`: {T: x}, `median`, `range`: [lo, hi]} | 2027E EV/EBITDA |
| `multiples_2027e` | {`ev_to_ebitda`, `ev_to_ebitda_less_sbc`, `pe`, `fcf_yield`} | this company at the price |
| `scenarios` | {bear\|base\|bull: {`adj_ebitda`, `target_multiple`, `multiple_pt`, `multiple_outside_peer_range`, `dcf_ev`, `dcf_pt`, `dcf_used`, `price_target`}} | per scenario |
| `price_target`, `return` | number | probability-weighted PT; PT / price − 1 |
| `rating` | Buy\|Hold\|Sell | |
| `dcf_grid` | {"wacc\|g": PT} | the grid |
| `dcf_base` | {`wacc`, `terminal_growth`, `tax_rate`, `flows`: [[period, t, ufcf]], `terminal_t`, `last_annual`, `terminal`, `ev`, `terminal_share`} | the workbook DCF tab |
| `exceptions` | [str] | logged by S7 |
| `inputs_hash` | str | SHA-256 of assumptions, market refs and price |

**`research.yaml`:** every forecast and valuation knob (Appendix A).

### 7.3 Rules and algorithms

<p align="center">
  <img src="img/v4-model-valuation.svg" alt="Steps S3 to S7: model graph and checks, guidance-anchored forecast with a bounded proposer, peers, price targets, rating" width="100%">
</p>

**Model** (spec §3.1; equations in [FINANCIAL_MODELING.md](FINANCIAL_MODELING.md)):

| Template | Companies | Revenue build |
|---|---|---|
| `marketplace` | BKNG, EXPE, ABNB | room nights (ABNB: nights and experiences) × bookings per night = GB; revenue = GB × take rate |
| `segments` | UBER | per segment: GB on its own faded growth × its take rate; revenue is the sum; adj. EBITDA = revenue − costs held as a share of total GB |
| `orders` | DASH, LYFT, CART | orders or rides × value per unit = GOV, GB or GTV; revenue = that × one take rate |
| `trip` | TRIP | revenue on one faded rate; adj. EBITDA as a margin on revenue |

| Rule | Reason |
|---|---|
| One definition (templates + DSL), two evaluators; Python is the source of truth | Two implementations would drift (D-041) |
| Condensed statements (`statements.yaml`): adj. EBITDA − SBC − D&A − other = GAAP operating income; tax at the trailing rate bounded 10–30%; OCF = NI + D&A + SBC; FCF = OCF − capex; cash, other assets, liabilities, redeemable NCI, equity | One engine serves all eight (D-041); the tax bound keeps a one-off quarter from setting the forecast rate *(new)* |
| Balance includes redeemable NCI and temporary equity; EXPE's liabilities derived from the reported total | UBER, DASH and CART tie only that way (build log §3.12) |
| Shares and per-share points on the latest filing's split basis | BKNG's 25-for-1 split mixed bases (build log §3.12) |
| Missing inputs stay blank (no template default) | Filling buybacks with 0 broke FY sums (build log §3.12) |
| Adj. EBITDA = revenue − costs as a share of GB (all GB-based templates) | A margin on GB left a take-rate cut without effect on EBITDA (build log §3.13) |
| Non-operating items forecast at 0 | They are not forecastable (D-042) |
| Travel prepayment not modeled | Spec §3.1 simplification |

**Forecast** (spec §3.2, D-042):

```text
first forecast quarter   Driver(q) = Driver(q-4) × (1 + g), anchored to the guidance midpoint
later quarters           Driver(q) = fyp(Driver) × (1 + g) × share(q)
share(q)                 mean share of quarter q in its fiscal year, last 3 complete years (fewer if fewer; logged)
g(t+1)                   g(t) × d,   d = 0.90 per quarter
ratios                   same quarter last year + a shift that holds until lapped (4 quarters), then 0,
                         within the last-8-quarter min/max ± a band (take rate 50 bps, margins 200 bps)
```

| Rule | Reason |
|---|---|
| Per driver, never per total | Spec §3.2 |
| Later quarters by the seasonal share | One unusual quarter doesn't repeat a year later (spec §3.2) |
| A quarter missing last year steps from two years back (`first()`); an incomplete last year falls back to the q-4 rule | ABNB's unextracted 2025 GBV (build log §3.13) |
| A ratio's shift holds until lapped, then 0 | A one-off change is not compounded (build log §3.13) |
| Ratios bounded to the 8-quarter range ± a band | A forecast ratio stays within what the company has shown *(new)* |
| Anchors: a bracketing search solves one assumption per guide, in order bookings → revenue → EBITDA | Each guide is hit by its own driver, upstream first so later anchors see it *(new)* |
| Midpoint, not the high end (`guidance_point: mid`) | Signed off before the backtest; `high` stays an ablation (spec §8) |
| A full-year-only guide anchors the FY column | ABNB guides only a full-year margin (build log §3.13 seasonal-share run) |
| An anchor the bounds stop is logged, never forced | Flag, don't hide (spec §2.3) |
| Scenarios move each template driver by ±`shock_sigma` × σ of its history | Scales the shock to how much the driver has moved (D-042) |
| Outside inputs: one starting growth rate per company (`forecast.outside.inputs`: BKNG, EXPE, ABNB volume and TRIP revenue ← BTS airline passengers; UBER Mobility bookings ← TLC HV0003; LYFT rides ← TLC HV0005; DASH orders ← FRED RSFSDP and order value ← CPI; CART order value ← CPI) | Spec §3.2 "Outside inputs" (D-045) |
| The series enters at the lag it has at the as-of: x = its mean y/y over the 3 latest visible months; the fit regresses the driver's history y/y on x taken at the same lag before each quarter (≥ 6 quarters) | Most series lag the target quarter by 3–6 months; fitting at another lag would overstate the signal (D-045) |
| g0 = (1 − w) × default + w × (a + b·x), w = 0.5 × R²; a slope ≤ 0 is not used | A weak or wrong-signed fit moves the forecast little or not at all (D-045) |
| Applied before the proposer and anchors; an anchor on the same assumption overrides it | Management's guide wins for the guided quarter; the input still carries into later quarters through the fade (D-045) |

**Proposer (S4):** may move only growth starts (`.g0`), ratio shifts (`.shift0`) and the fade, never one an anchor solves; clipped to the growth start's 8-quarter range ± 5 points, the ratio's range plus its band, and `fade_bounds` [0.75, 1.0]; kept only if one evidence id resolves.

| Rule | Reason |
|---|---|
| Default `defaults` (LLM proposer off) | It did not beat the defaults in the backtest (build log §3.13, spec §8) |
| Not on anchored assumptions | The anchor runs after and would undo it (`propose.py`) |
| The clip bounds | A proposal stays within what the history supports *(new)* |
| Evidence must resolve | No unsupported proposal *(new)* |

**Valuation and rating** (spec §3.3):

| Item | Rule | Reason |
|---|---|---|
| Market cap, EV | last close before D × latest diluted shares; EV = market cap + debt − cash and ST investments | D-042; leases, stakes, customer funds excluded and logged (D-042) |
| Peers (S6) | same-sector companies, defaults, same as-of, cached | Spec §1.1; defaults (§5.3) |
| Multiple PT | target × 2027E adj. EBITDA → EV − net debt → per share; target = peer median; bear/bull ∓/±15%; outside the peer range flagged | Spec §3.3; ±15% from the spec's example (D-042); flag per layer 4 |
| DCF | UFCF = adj. EBITDA − SBC − tax on EBIT − capex; modeled quarters, then annual years fading toward g to 10 years; Gordon terminal at 3.0%; Ke = DGS10 + β × ERP (β: 60 monthly returns vs SPY); WACC = Ke below a 10% debt weight, else weighted | Spec §3.3 (SBC a cost, one ERP); the 10% rule from the spec's all-equity UBER (D-042) |
| Non-positive DCF EV | left out of the PT, logged | A negative DCF would set a negative PT (TRIP; build log §3.14) |
| Price target | 0.6 × multiple + 0.4 × DCF per scenario; weighted 25/50/25; no roll-forward | Spec §3.3; no roll-forward (D-042) |
| Rating | absolute: Buy ≥ +15%, Sell ≤ −10%; relative (`--all`): thirds | Spec §3.3; relative needs every model (D-042) |
| Determinism | identical inputs → byte-identical `valuation.json` | Layer 4 (spec §5) |

**Layer-2 checks** (run after S3 and after S5; after S7 for the valuation checks):

| Check | Rule | Reason |
|---|---|---|
| `balance` | assets = liabilities + equity within $1M, every period; and assets = reported liabilities-and-equity | Spec §3.1; the second test catches a derived side (build log §3.12) |
| `quarters_sum` | Q1–Q4 = FY for every flow line (recast years skipped, listed) | Spec §5 |
| `q4_ties` | derived Q4 + 9M = the 10-K's FY | Spec §1.6 |
| `take_rate` | recomputes from reported revenue / GB | Spec §3.1 |
| `segments_sum` | segments sum to totals (alternative part lists for redrawn segments) | Spec §1.5; TRIP's eliminations (build log §3.12) |
| `no_future_input` | every input has `filed < as_of` | As-of rule (§2.5) |
| `formula_only` | forecast cells are formulas | Spec §3.1 |
| `wacc_gt_g`, `weights` | WACC > g; weights sum to 1 | A Gordon value needs WACC > g; weights must be a distribution *(new)* |

### 7.4 Failure handling

| Failure | Handling |
|---|---|
| Layer-2 or valuation check FAIL | The run stops with the failing check |
| Missing input | Blank, logged as an exception |
| Volume KPI not disclosed | The template falls back one level up, logged |
| Anchor out of bounds | Logged, not forced |
| Fewer than 3 complete years for seasonal shares | Fewer years, logged |
| Non-positive DCF | Left out of the PT, logged |
| Proposer failure | Defaults |
| Outside series too short, not visible, or slope ≤ 0 | That input is not used; an `info` exception says why |

### 7.5 Constraints

Forecast horizon 2027Q4 (DCF annual years beyond). Condensed statements; no deferred bookings; Δ working capital 0. Peer sets of three per sector. LibreOffice needed for parity (§10).

### 7.6 Tests

| File | Asserts |
|---|---|
| `test_model_dsl.py` | one expression, two evaluators; `fy`, `fyp`, `first`; missing inputs give None, not 0; anything else rejected |
| `test_model_graph.py` | checks pass on a consistent model and fail on a broken one (each check); `no_future_input` fails on a late filing; split basis; alternative part lists; temporary equity summed |
| `test_model_workbook.py` | inputs blue, formulas black; parity with LibreOffice |
| `test_forecast.py` | growth starts at last y/y and fades; ratios within bounds; shifts hold until lapped; seasonal shares from the latest three complete years; forecast cells are formulas; anchors hit the midpoint and log when out of bounds; proposals bounded with evidence; forecast parity |
| `test_credentials_outside.py` | the outside-input fit and lag window; a missing, blank or placeholder credential raises an error naming it; `tenk fetch` without credentials exits 2; the example file holds only placeholders |
| `test_valuation.py` | DCF falls as WACC rises; negative flows give a negative DCF; terminal share grows with g; relative ratings by thirds; deterministic rounding; market value |

---

## 8. Report pipeline

### 8.1 Components

| Component | Code | Responsibility |
|---|---|---|
| Runner | `research/pipeline.py` `run(db, ticker, as_of, settings, llm_steps, resume, …)` | S1–S11 in order; parent lookup; resume; timing; version |
| Sections | `research/note/sections.py` | The ten sections from artifacts: code blocks, tables and prose slots with their facts |
| Facts | `research/note/facts.py` `Facts` | Pre-formatted facts with sources (§9.2) |
| Writer | `research/note/write.py` | One LLM call per slot, trace, one repair, drop |
| Trace, consistency | `research/note/trace.py`, `consistency.py` | §9 |

### 8.2 Interfaces: step I/O and artifacts

<p align="center">
  <img src="img/v4-report-runner.svg" alt="Report pipeline S1 to S11: code steps and LLM steps, the artifact each writes, which outputs --resume reuses, and the failure policy: integrity and valuation checks stop the run, LLM steps degrade" width="100%">
</p>

| Step | Kind | Reads | Writes |
|---|---|---|---|
| S1 datapack | code | Store | data pack (in memory) |
| S2 events | sub-agent | Store, search | `events.json` |
| S3 model | code | data pack, template | history model; layer-2 (FAIL stops) |
| S4 propose | sub-agent (off by default) | driver table, Store | assumption overrides |
| S5 forecast | code | model, guidance, `research.yaml` | `model.json` (§7.2); layer-2 again |
| S6 peers | code | Store, per peer | peer models (model book) |
| S7 value | code | model, peers, market | `valuation.json` (§7.2); valuation checks |
| S8 revise | code | the parent's `model.json` | `revisions.json` |
| S9 debates | sub-agent per scenario driver | model, Store, search | `debates.json`; section 7 sensitivities |
| S10 write | code + LLM | all artifacts | `facts.json`, `note.json`; consistency check |
| S11 export | code | all artifacts | `note.md`, `note.pdf`, `workbook.xlsx`, `exceptions.json`, `report.json`; `model_versions` row |

| Artifact | Schema |
|---|---|
| `events.json` | `[{type, summary, evidence: [id]}]` (§5.2) |
| `debates.json` | `[{driver, question, bull, bear, hypothesis, trigger, gap, evidence: [id], evidence_values: [{id, value, unit}]}]` |
| `revisions.json` | `[{label, old, new, unit, old_id, new_id, type: "Reported + assumption"\|"Assumption", confidence: Medium\|Low}]` for revenue, adj. EBITDA and EPS in the first forecast quarter and the last two FY columns; `[]` without a parent |
| `facts.json` | `[{id: "F-n", label, value, unit, source: id, period, filed}]` (§9.2) |
| `note.json` | `{sections: [title], blocks: [str], slots: {key: text}, slot_facts: [{key, text, facts: [F-id]}], untraced: [str]}` |
| `exceptions.json` | `[{id: "X-nn", severity: info\|warning\|error, item}]` |
| `report.json` | `{ticker, as_of, version, parent, rating, price_target, sections: int, facts: int, untraced: int, exceptions: int, consistency: {check: bool}, parity: "ok/total", folder, timing: {seconds, steps: {step: {seconds, calls?, input_tokens?, output_tokens?}}}}` |

### 8.3 Rules and algorithms

| Rule | Reason |
|---|---|
| A fixed pipeline; LLM calls are leaf tasks; no Ask or Research runs | Spec §2.3 ("not an open agent loop") |
| Parent = the latest version for the ticker with an earlier as-of; none → "initial draft (no prior run)" | Spec §2.3 note rules |
| Revisions cover revenue, adj. EBITDA and EPS (first forecast quarter, last two FY columns); `type` = "Reported + assumption" when the parent's forecast quarters are now reported, else "Assumption"; confidence Low for a future fiscal year, else Medium | The headline lines of spec §2.3's table 4.1; the type says whether new actuals or only assumptions moved the estimate *(new)* |
| `--resume` reuses `events.json`, `debates.json` and the prose slots; code steps always rerun | Crashes (I-001) repeat only the cheap, deterministic steps |
| `--no-llm` skips S2, S4's LLM, S9 and S10's prose | Code-only drafts and tests *(new)* |
| Sections 1 and 10 written last | Spec §2.3 requirement 4 |
| Section 7 adds a sensitivity row for every debated driver | The consistency check requires it (spec §2.3 req. 5) |
| Budgets per LLM step; over `run_minutes` → warning exception | Spec §2.3 requirement 8 |

**Note sections:**

| Section | Built by | Content |
|---|---|---|
| 1 Executive summary | code + slot `takeaways`, **last** | price, PT, return, rating, change vs parent; takeaways from other sections' facts |
| 2 Earnings results | code + slot `quality` | 2.1 actual vs estimate vs guide mid vs y/y; 2.2 guidance vs our forecast; 2.3 quality of the quarter |
| 3 Drivers and debates | S9 | per scenario driver: bull, bear, hypothesis, trigger, gap |
| 4 Estimate revisions | code | old vs new, change, type (source), confidence; 4.3 layer-2 results |
| 5 Financial summary | code | annual 2024A–2028E |
| 6 Valuation | code + slot `valuation` | comps, DCF, scenarios, weighted PT, return, rating; target multiple justification |
| 7 Risks and sensitivity | code | sensitivity rows |
| 8 Catalysts | code + S2 events | next earnings (estimated), events with ids |
| 9 Sources, assumptions | code | sources by date, key assumptions, Form 4 context, rating definitions |
| 10 Senior analyst review | code, **last** | decisions; every open warning and error by X-id |

**Consistency check** (before export; spec §2.3 req. 5): `rating_thresholds`, `section1_numbers`, `debated_drivers`, `guidance_in_2_2`, `open_issues` (run after numbering). Code checks; a failure is an `error` exception and is in `report.json` `consistency`; the takeaways get one repair; other sections are never rewritten (spec §2.3 req. 6).

### 8.4 Failure handling

| Failure | Result |
|---|---|
| Layer-2 or valuation FAIL | The run stops with the failing check; nothing downstream runs on numbers that did not pass |
| LLM step: bad JSON after repair, budget hit, no resolvable evidence | Defaults replace proposals; the section reads "failed check: reason"; an exception row; the run finishes |
| Untraced number after repair | Sentence dropped; exception row (§9.3) |
| Consistency failure | Exception row; takeaways repaired once |
| Run over the time target | Warning exception |
| LibreOffice or Chrome missing | Export raises; no version is saved |

### 8.5 Constraints

30-minute target per company; LLM budgets per step (§5.2); `taskset -c 16-31` (I-001); one ticker per run, peers cached in-process.

### 8.6 Tests

`test_note.py` (13): number figures skip dates, periods and ids; true figures trace; seeded errors are caught; a table cell changed to another fact's value is caught; prose traces to its own slot only; the writer drops sentences still untraced after repair; consistency passes a consistent note and catches each inconsistency. Pipeline runs with `--no-llm` on all eight (build log §3.15). Layer 5 (§11.3).

---

## 9. Verification and number trace

### 9.1 Components

| Component | Code | Responsibility |
|---|---|---|
| Reference grammar | `research/refs.py` `parse`, `Resolver` | Parse, format and resolve any id under an `AsOf` |
| Claim check | `generation/verify.py` | Mark each answer claim verified, calculated, cited or unverified |
| Fact list | `research/note/facts.py` | Pre-formatted facts for the writer and the log |
| Number trace and lint | `research/note/trace.py` | Every number in the note traced; note rules linted |
| Consistency check | `research/note/consistency.py` | §8.3 |

### 9.2 Interfaces

**Reference grammar:**

| Kind | Id | Resolves to |
|---|---|---|
| chunk | `UBER-2026Q2-ER-EX99-005` | chunk text |
| accession | `0001543151-26-000032` | filing metadata |
| XBRL | `xbrl:UBER:us-gaap:Revenues:<acc>:2026-04-01/2026-06-30` | value, unit (V3's end-only ids still resolve) |
| inline XBRL | `ixbrl:<acc>:<fact id>` | parses; not resolved (§13.1) |
| derived quarter | `q:UBER:revenue:2025Q4` | value, derivation, input ids |
| KPI, guidance | `kpi:UBER:gross_bookings.mobility:2026Q2` · `guide:UBER:adj_ebitda:2026Q3` | the row (guide: low and high) + source chunk |
| price, macro | `px:UBER:2026-08-05` · `macro:DGS10:2026-08-05` | value |
| cross-check | `cc:tlc:HV0003:2026-06-01` · `cc:bts:airline_passengers:2026-06-01` | value |
| model | `model:<T>:<as_of>:<run>:<line>.<period>` or `…:<run>:<field>`; run = `base`, `scratch-<n>`, `val-<…>`, `var-<period>`, `v<n>` | model or valuation value (needs the model book; `v<n>` reads a version saved before the as-of) |
| evidence, exception | `E-12` · `X-03` | parse; not resolved (§13.1) |

Data kinds resolve to `{value, unit, ticker}` in the row's own unit.

**Fact list** (one per figure the note may print):

```json
{"id": "F-2", "label": "12-month price target", "value": "<local only>", "unit": "USD",
 "source": "model:UBER:2026-08-06:val-base:price_target", "period": "", "filed": ""}
```

`text()` formats it (rounding, "2027E"/"2025A", signs, units); the writer must use it verbatim and mark it `{F-2}`. Facts print as the `E-n` rows of the log.

### 9.3 Rules and algorithms

| Rule | Reason |
|---|---|
| A claim is **verified** when its value matches a resolved cited value within its written precision; **calculated** when its calculation reruns from resolved inputs; **cited** with no number and valid ids; else **unverified**, shown with the reason | V2/V3's guarantee, extended to every id kind (D-036) |
| Model refs verify against the model book | Research answers keep that guarantee for computed numbers *(new)* |
| Code-written blocks must appear verbatim | A corrupted table cell passed a global trace (build log §3.15) |
| Prose: every number is a marked fact **of its own slot** or an allowed token (dates, section numbers, "Q2", "10-K") | A figure swapped for another fact's value passed a global trace (build log §3.15) |
| Lint: no "pending"/"TBD"; gaps read "n/a: …" or "failed check: …"; an estimate period carries its "E" | Spec §2.3 note rules |
| One repair turn naming the violations; a sentence still untraced is dropped and logged | Spec §2.3 requirement 3 |

<p align="center">
  <img src="img/v4-number-trace.svg" alt="Number trace: fact list from artifacts, writer marks facts, trace.py checks every number, one repair turn, failed-check section, tables rendered by code" width="100%">
</p>

**Evidence and exception log:** `E-n` rows (label, value, source id) from the facts; `X-n` rows from every step (reconciliation gaps, missing inputs, clipped proposals, out-of-range multiples, EV exclusions, untraced numbers, failed consistency checks, run time). Fields today: id, severity (`info`, `warning`, `error`), item; the spec's fields are in §13.1.

### 9.4 Failure handling

| Failure | Handling |
|---|---|
| A cited id doesn't parse or resolve | The claim is unverified; "unknown id" is added to its reason |
| A value doesn't match within precision | Unverified, with the reason |
| An untraced number or a lint violation in prose | One repair; then the sentence is dropped and an `error` exception logged |
| A code block changed | Reported as untraced (it should never happen; it is the seeded-error test) |
| A consistency check fails | `error` exception; takeaways repaired once |

### 9.5 Constraints

`model:` ids need the session's model book (Research) or the saved version's files (`v<n>`). `E-`/`X-` ids are meaningful only within one report.

### 9.6 Tests

`test_refs.py`: every kind parses; XBRL ids name a duration's start; the resolver reads V3 and V4 XBRL ids. `test_generation.py`: `numbers_in` reads signs, scales and percentages; `find_value` normalizes units and respects the rounding of prose figures. `test_agent_tools.py` and `test_ask.py`: claims verify against facts and data rows; another company's fact doesn't. `test_note.py`: trace, lint and seeded errors (§8.6).

---

## 10. Outputs and review

### 10.1 Components

| Component | Code | Responsibility |
|---|---|---|
| Model and Assumptions tabs, parity | `research/export/workbook.py` | Write the Model tab with formulas from the DSL; Assumptions; LibreOffice parity |
| Other tabs, PDF | `research/export/report.py` | Summary, Scenarios, Valuation, DCF, Revisions, Model (prior), Checks, Log; the note's PDF |
| Renderer | `evaluation/render.py` | Markdown → HTML → PDF with headless Chrome |
| Review | `cli.py` `tenk research approve` | Set a version `reviewed` |

### 10.2 Interfaces

**Report folder** `output/reports/<TICKER>/<as_of>/`: `note.md`, `note.pdf`, `workbook.xlsx` and the artifacts of §8.2, saved as a version (`UBER:v1`, `UBER:v2`, …) with its parent. `research_report` and `model:…:v<n>:…` ids read these files.

**Workbook:**

| Tab | Contents |
|---|---|
| Model | lines × quarters and FY; inputs blue with provenance, formulas black, links to assumptions green |
| Assumptions | name and value (the cells the Model tab's formulas link to); basis, confidence, variables affected and last updated are not built (§13.1) |
| Summary | rating, price, price date, PT, return, market cap, EV, net debt, shares, WACC, parity |
| Scenarios | `scenario line`, year, value |
| Valuation | peer multiples, median, cost of capital, scenario PTs, DCF grid (the bridge and rating are on Summary, §13.3) |
| DCF | base-case UFCF with live discount, terminal value, EV, terminal share and per-share formulas; the WACC × growth grid |
| Revisions | line, old, new, type, confidence |
| Model (prior) | the parent's values as saved, or "initial draft (no prior run)" |
| Checks | check, status, tested, failures, skipped |
| Log | id, kind (evidence/exception), label or severity, value or item, source |

**Review:** `tenk research approve TICKER:vN --by INITIALS` → `status: "reviewed by <INITIALS> on <date>"` (anything other than `draft` reads as reviewed).

### 10.3 Rules

| Rule | Reason |
|---|---|
| Parity: LibreOffice recalculates; every Model formula cell equals `model.json` (1e-9 relative) | The workbook must be live and agree with the source of truth (D-041) |
| PDF by headless Chrome | Already a dependency; no new one (D-044) |
| Every version is `draft` until approved; numbers never edited, an amendment is a new version | The human decides (D-044, spec §2.3) |
| Ask and Research show the status beside a cited estimate | A draft is not presented as approved (D-044) |
| Prices stay in `data/` | Tiingo licence (D-044) |

### 10.4 Failure handling

| Failure | Handling |
|---|---|
| Parity mismatch | Recorded in `report.json` `parity` ("ok/total"); **the run does not stop** (§13.1: the doc earlier called it a layer-2 FAIL) |
| `soffice` or Chrome not on PATH | Export raises a `RuntimeError`; the version is not saved |
| Approving an unknown version | Nothing is updated and no error is shown (§13.1) |

### 10.5 Constraints

LibreOffice (`soffice`) and Chrome or Chromium on PATH. Parity recalculation takes up to the 300 s timeout.

### 10.6 Tests

`test_model_workbook.py` (colors, parity), `test_export_tabs.py` (the DCF tab recalculates in LibreOffice to `dcf()`'s EV, terminal share and per-share value; Model (prior) without and with a parent), `test_forecast.py::forecast_parity_with_libreoffice`.

---

## 11. Evaluation

### 11.1 Components

| Component | Code | Responsibility |
|---|---|---|
| Extraction scorer | `evaluation/extraction.py` | Exact match and critical fields vs the gold labels |
| Backtest | `evaluation/backtest.py` | Walk-forward S1–S5, baselines, ablations, leakage probe |
| Note eval | `evaluation/note_eval.py` | Trace, sections, rubric (judge), seeded errors |
| Ask / Research runs | `experiments/v4/ask_run.py`, `research_run.py`; `evaluation/scoring.py`, `judge.py` | Run the agent on gold, judge, score against frozen bars |

### 11.2 Interfaces

**Gold files** (`evaluation/gold/`, JSONL; each row has `verified_by`):

| File | Row |
|---|---|
| `extraction-gold.jsonl` (337) | `{id, ticker, period, metric, segment, value, unit, source, block, quote, verified_by}` |
| `ask-gold.jsonl` (79) | `{id, split, category, kind, question, as_of, period, form, answer_type, expected_answer: [{value, unit, company}], tolerance, required_points, should_abstain, verified_by}` |
| `research-gold.jsonl` (72) | `{id, split, category, company, question, as_of, expected_refs: [regex], requires_assumptions, expected_tools, verified_by}` |

**Result folders:** `evaluation/results/backtest-<label>/` (`report.md`, `forecasts.csv`, `summary.json`, `llm_baseline.json`); Ask and Research runs under `output/generation/<label>/` with scores and judged reports.

### 11.3 Rules: layers and bars

| Layer | Test | Pass bar |
|---|---|---|
| 1 Extraction | 337 labels vs the extractor | ≥ 98% exact; 100% on revenue, EPS, main KPI |
| 2 Model integrity | §7.3 checks on every model; unit tests | 100%; any FAIL stops |
| 3 Forecast | walk-forward backtest | MAPE below seasonal naive on revenue and main KPI; bias near zero |
| 4 Valuation | identical inputs → byte-identical `valuation.json`; PT falls as WACC rises | identical; every target multiple inside the peer range or flagged |
| 5 Note | trace, ten sections, rubric (judge gpt-oss-20b: grounded, complete, analysis, balance, clarity), seeded errors | 100% traced; rubric ≥ 4/5 |
| Ask | 79 questions | ≥ 85% correct and verified; 100% of cited ids resolve |
| Research | 72 questions | ≥ 90% with a verifying `model:` ref and stated assumptions; 100% of computed numbers from model tools |

| Rule | Reason |
|---|---|
| Bars frozen (2026-10-27) before the code they grade; a miss is reported as a miss | No tuning to the test (D-043, D-006) |
| A gold change after a run is logged, and the old score kept beside the new | Changing gold after seeing answers must be visible (build log §3.11) |
| Backtest D = the day after a release's EDGAR acceptance (US/Eastern) | Works for before-open and after-close releases (build log §3.2) |
| Actuals as first reported | Scores what was knowable then, not later restatements *(new)* |
| Baselines: seasonal naive, guidance midpoint, plain LLM with no tools | Spec §6; the no-tools LLM also measures memorization (D-043) |
| A judge from another model family, as a second pass | No model grades itself; it can't share the GPU (D-029) |

```text
for company in the eight:
  for release from Q4 2024 onward:            6 per company, CART included: 48 per metric
     D = the day after the release's EDGAR acceptance
     run S1–S5 at D → forecast the next quarter
     score vs the actual as first reported, and vs the baselines
```

**Metrics:** MAPE, bias (positive = too high), guidance call; medians and ranges by company; paired Wilcoxon per baseline. **Ablations** (spec §6): extraction LLM vs rules; fixed vs fitted fade; guidance mid vs high; proposer `llm` vs `defaults`; outside inputs on vs `"off"` (D-045); one long-horizon run from early February 2025. **Leakage probe:** every forecast's inputs were filed before D.

### 11.4 Failure handling

| Failure | Handling |
|---|---|
| A backtest point whose actual isn't reported yet | Skipped (no row) |
| An input filed on or after D (leakage probe) | `RuntimeError`: the backtest stops |
| The judge leaves out a criterion | That criterion is missing from the rubric mean; a reply that isn't JSON raises |
| A gold row unverified by a person | Kept; `verified_by` shows who checked it (§13.1) |

### 11.5 Constraints

Small samples (48 forecasts per metric); the LLM's training cutoff is unpublished, so no held-out set (D-043); the judge needs the GPU free of the 27B model; human review only partial (§13.1).

### 11.6 Tests

`test_extract.py::score_kpis_guidance_and_critical`; `test_evaluation.py` (gold validation and verification marking, retrieval metrics); `test_note.py::seeded_errors_are_caught`; `test_forecast.py::revenue_bridge_adds_up`.

---

## 12. Worked example: UBER as of 2026-08-06

One Report run, following the interfaces. Values are from `output/reports/UBER/2026-08-06/`.

**1. Build → Store (interfaces 1, 6).** The Q2 2026 release (8-K filed 2026-08-05, before the open) is chunked as `UBER-2026Q2-ER-EX99-*`, and extraction stores a KPI row:

```json
{"metric": "gross_bookings", "segment": "mobility", "period": "2026Q2", "value": 28988.0,
 "unit": "USD millions", "chunk_id": "UBER-2026Q2-ER-EX99-…", "quote": "| Mobility | … | 28,988 | …"}
```

At as-of 2026-08-06 the store sees it (`filed` 2026-08-05 < D); at 2026-08-05 it would not (§2.5).

**2. S1 → S3 (interfaces 2, 7, 8).** The template line `gross_bookings_mobility` takes `source: kpi:gross_bookings.mobility`, so `model.json` gets:

```json
"gross_bookings_mobility|2026Q2": {"value": 28988.0, "kind": "input",
  "ref": "kpi:UBER:gross_bookings.mobility:2026Q2", "filed": "2026-08-05"}
```

The layer-2 checks pass; `no_future_input` confirms `filed < 2026-08-06`.

**3. S5 forecast.** Later cells are formulas: `take_rate_mobility|2026Q3` = the same quarter last year + the held shift → `{"value": 0.2532, "kind": "formula"}`. FY2027 adj. EBITDA sums its quarters.

**4. S2 events and S9 debates (interfaces 4, 5, 9).** A sub-agent with the `ask` tools returns an event:

```json
{"type": "results", "summary": "Uber reported Q2 2026 revenue of $14.19 billion, up 12% YoY, …",
 "evidence": ["kpi:UBER:revenue:2026Q2", "kpi:UBER:eps_diluted:2026Q2", …]}
```

One debate per scenario driver (`gb_g_mobility.g0`, …) returns bull, bear, hypothesis, trigger, gap and evidence ids; items whose ids don't resolve are dropped.

**5. S7 value → `valuation.json` (interface 8).** Peers LYFT, DASH and CART are built with defaults at the same as-of; the PT blends the multiple and DCF values per scenario: `price_target` and `rating` (values local only), with an `inputs_hash`.

**6. S10 facts and prose (interfaces 10, 11).** The price target becomes fact `F-2` with source `model:UBER:2026-08-06:val-base:price_target`. The `takeaways` slot writes "…a 12-month PT of $<PT> {F-2}…"; `trace.py` finds every number marked; the consistency check confirms that section 1's PT equals section 6's.

**7. S11 export (interface 12).** `note.md` and `note.pdf`; `workbook.xlsx` with parity 750/750 formula cells; 18 exceptions numbered `X-01`…`X-18`, e.g. `{"id": "X-01", "severity": "info", "item": "shares_diluted missing in 2023Q4, 2024Q4, 2025Q4"}`; version `UBER:v2` saved as `draft`.

**8. Read back.** A later Ask run at an as-of after 2026-08-06 calls `research_report(company="UBER")` and cites `model:UBER:2026-08-06:v2:price_target`, shown with its status `draft`.

---

## 13. Known problems and not built

### 13.1 Not built

| Item | Spec or design | Today |
|---|---|---|
| Bounded research runs (Research tools, call budget) for note sections 2.3, 4, 6, 7, 8 | spec §2.3 req. 2 | 2.3 and 6 are single LLM calls over facts, without tools; 4, 7 and 8 have no LLM text. Only S9 debates are tool-using runs |
| `initiation` report type | spec §2, §2.3 | `update` only |
| Variance flag: over 2% (revenue) or 5% (adj. EBITDA) from the guide midpoint needs a written reason | spec §3.2 | `forecast.variance_flag` unused; no Flag column in 2.2 |
| Consensus column ("n/a") and a "no consensus" exception | spec §2.3 | absent |
| Evidence log fields: period, filing date, location, retrieval date, type, workbook cell | spec §2.3 | Log tab: id, kind, label, value, source |
| Exception fields: treatment, status | spec §2.3 | id, severity (`info`, `warning`, `error`, as the spec now uses), item |
| Summary tab as old vs new with $ and % change and source of change | spec §2.3 | Summary is the valuation headline; old vs new is on Revisions |
| Scenarios tab: weights, probability-weighted value, one-at-a-time sensitivities | spec §2.3 | values only |
| Thin peer set exception (fewer than 4 peers) | spec example X-04 | not logged |
| Open exceptions carried forward from the parent version | design | not built |
| An exception when a non-operating item exceeds 10% of NI in the last 4 quarters | design | forecast 0 only |
| `ixbrl:`, `E-` and `X-` ids resolving in the claim check | §9.2 | parse only |
| Parity mismatch as a layer-2 FAIL | §10.3, D-041 | recorded in `report.json`; the run continues |
| Revision type (recurring, mixed, timing) and source of change as separate columns; revisions for every line | spec §2.3 table 4.1, Summary tab | `type` holds the source of change ("Reported + assumption" / "Assumption"); three lines only |
| `tenk research approve` on an unknown version reports an error | §10.4 | the update silently matches nothing |
| Human sample of the note rubric; human review of the Ask and Research gold sets | §11 | not done |
| Debates (and any section research run) with the **Research** toolset | spec §2.3 req. 2 | S9 debates use the `ask` set: no model tools, so a debate cannot run a scenario or quote a sensitivity itself (§5.2) |
| DCF with 3 modeled years (2026E–2028E), then 7 faded | spec §3.3, §3.2 | the model's quarters end at 2027Q4; 2028 onward is faded in the DCF (§7.3) |
| Held-out quarters (released after the LLM's training cutoff) reported separately | spec §6 | not reported: the cutoff is unpublished (D-043) |
| Section 10 gets a repair turn on a failed consistency check | spec §2.3 req. 6 | section 10 is written by code from the exception log, so only the takeaways (section 1) are repaired (§8.3) |
| Assumptions tab: basis, confidence, variables affected, last updated | spec §2.3 | name and value only (§10.2) |
| Release vs filing gap logged as an exception ("filing wins", spec example X-01) | spec §1.6, §2.3 | no comparison between release KPIs and XBRL facts; integrity checks cover Q4 ties and restatements only |
| Secondary multiples (EV/(EBITDA − SBC), P/E, FCF yield) shown | spec §3.3 | computed in `valuation.json` `multiples_2027e`; not in note section 6 or the Valuation tab |
| Section 7 columns: model driver and evidence; unquantified risks from new risk-factor text | spec §2.3 | risk, 2027E EBITDA change and PT impact for the scenario drivers only |

Working list with effort estimates: [tmp_inconsistencies.md](tmp_inconsistencies.md).

### 13.2 Known problems

| Problem | Handling |
|---|---|
| **LLM memory leaks into the backtest** | The LLM never outputs forecast numbers; the no-tools baseline (56% MAPE) shows no sign of memorized actuals; no quarter is called held out (D-043) |
| **Point-in-time data that changes** (restatements, recast segments, FRED revisions, KPI redefinitions) | Dated rows, `.latest()`, `definition_version`, ALFRED vintages, `restated` checks |
| **Structural breaks** (UBER's 2026 UK agent change) | They enter as the last y/y change and hold for four quarters, lifting UBER's 2027E margin; the proposer can catch them but hurt accuracy overall |
| **Non-operating swings** (stake revaluations, tax allowance releases) | Forecast 0; history shows reported EPS |
| **Instacart starts at Q3 2023** | Seasonal share from fewer years, logged; 6 backtest points like the others |
| **Thin peer sets** (3 per sector) | The sector median drives the ratings (DASH valued at its peers' multiple); a stated limit in V4_RESULTS |
| **Negative DCF** (TRIP) | Left out of the PT, logged; the PT rests on the multiple |
| **Release time vs filing date** | `filing_date` (conservative); backtest D = the day after acceptance |
| **Long runs on a crash-prone CPU and a shared GPU** | `--resume`, `taskset -c 16-31` (I-001), CPU embedder (I-002) |
| **Determinism with an LLM** | Valuation is code; LLM steps at temperature 0; `--resume` replays them |
| **Data licensing** | Tiingo internal use only; Stooq, TSA and Google Trends dropped |

### 13.3 Deliberate differences from the spec

Built differently on purpose; each keeps the spec's intent.

| Spec | Design | Why |
|---|---|---|
| §3.1: Uber's adj. EBITDA is a margin on total gross bookings | revenue − costs held as a share of total GB (§7.3) | with a plain margin on GB, a take-rate cut left EBITDA unchanged ([build log §3.13](V4_BUILD_LOG.md#313-v44-exit-forecast-and-backtest-2026-10-28)) |
| §2.3: Valuation tab holds the EV-to-equity bridge and rating | they are on the Summary tab (§10.2) | one headline tab for the reviewer; Valuation keeps the peers and DCF detail |
| §2.3: the prior model kept as a separate tab | Model (prior) holds the parent's values, not formulas (§10.2) | the parent version's own workbook keeps its live formulas |
| §3.2: later quarters by the seasonal share | a full-year-only guide anchors the FY column (§7.3) | ABNB guides only a full-year margin |

---

## Appendix A. Config

| File | Holds | Pinned |
|---|---|---|
| `config.yaml` (`TENK_CONFIG`) | per-stage V0–V3 knobs: chunking; `retrieval` (embedder, reranker, candidates 40, `rrf_k` 60, k 8, device); `generation`; `agent` (`max_calls` 20, `max_turns` 20, `max_context` 20000, `result_chars` 16000) | per experiment (D-019) |
| `companies.yaml` (`TENK_COMPANIES`) | coverage, `data_dir: data/v4`, window from 2023-01-01; per company CIK, sector, template, main KPI | per experiment |
| `research.yaml` | `forecast` (horizon 2027Q4, fade 0.9, `fade_bounds` [0.75, 1.0], `guidance_point: mid`, `ratio_bounds` take rate 0.005 / margin 0.02, `tax_rate_bounds` [0.10, 0.30], `variance_flag`, `outside` (method `nowcast` \| `"off"`, weight 0.5, `min_quarters` 6, inputs per company)); `scenarios` (weights 25/50/25, `shock_sigma` 1.0); `proposer` (method, model, base_url, budget); `budgets` (events, debate, section, `run_minutes` 30); `valuation` (weights 0.6/0.4, target year 2027, scenario multiple 0.15, terminal growth 0.03 and grid 2.5–3.5%, WACC step 0.01, DCF years 10, annual fade 0.9, beta months 60, debt-weight threshold 0.10, rating thresholds +15% / −10%) | per experiment |
| `facts/kpis.yaml`, `facts/metrics.yaml` | the KPI registry (§6.2); XBRL concepts per metric | with the code |
| `research/model/templates/*.yaml` | model templates (§7.2) | with the code |

Credentials: each user's own, typed in with `tenk setup` → `credentials.yaml` (mode 600, gitignored; template `credentials.example.yaml`). `SEC_USER_AGENT`, `TIINGO_API_KEY` and `FRED_API_KEY` in the environment (or `.env`) take precedence. A command that needs a missing, blank or placeholder credential stops with an error naming it (`credentials.py`). `data/` and `output/` are local only and never committed.

## Appendix B. CLI

| Command | Does |
|---|---|
| `tenk companies verify [--file companies.yaml]` | resolve CIKs, FYE, filing counts |
| `tenk fetch [--forms 10-K 10-Q ER DEF14A] [--market]` | acquisition; `--market` = prices, macro, ERP only |
| `tenk build --stage chunk,load,append,embed,pages,facts,market,kpis` | build stages; `append` loads new filings into an existing database |
| `TENK_COMPANIES=companies.yaml tenk research report TICKER… --as-of D [--no-llm] [--resume]` | Report mode (= `python -m tenk_agent.research.pipeline TICKER D`) |
| `tenk research approve TICKER:vN --by INITIALS` | mark a version reviewed |
| `tenk setup` | type in your own SEC User-Agent, Tiingo and FRED keys (`credentials.yaml`) |
| `python -m tenk_agent.research.valuation TICKER… AS_OF [--all]` | valuation; `--all` adds relative ratings |
| `python -m tenk_agent.research.forecast TICKER… AS_OF` · `python -m tenk_agent.research.model.history TICKER… AS_OF` | forecast or history models alone |
| `python -m tenk_agent.evaluation.backtest [TICKER…] [--label] [--proposer defaults\|llm] [--long]` | backtest and ablations |
| `python -m tenk_agent.evaluation.note_eval FOLDER… [--judge …] [--seeds N]` | layer 5 |
| `experiments/v4/ask_run.py`, `research_run.py` | Ask and Research gold runs |
| `tenk tool NAME key=value …` | run one tool by hand (`--list`: the schemas) |

## Appendix C. Code layout

```text
src/tenk_agent/
  asof.py corpus.py store.py models.py cli.py
  acquisition/   edgar.py filings.py companies.py market.py crosscheck.py insiders.py
  preprocess/    forms.py rules.py blocks.py structure.py validation.py
  chunking/ retrieval/ generation/                      (V0–V2, shared)
  facts/         xbrl.py quarters.py extract.py rules.py kpis.yaml metrics.yaml
  research/      refs.py book.py datapack.py subagents.py propose.py forecast.py valuation.py pipeline.py
                 model/   dsl.py graph.py history.py checks.py templates/{statements,marketplace,segments,orders,trip}.yaml
                 note/    facts.py sections.py write.py trace.py consistency.py
                 export/  workbook.py report.py
  agent/         tools.py loop.py sec.py
  evaluation/    extraction.py backtest.py note_eval.py render.py (+ V0–V3 modules)
  prompts/       ask.md research.md extract.md propose.md events.md debates.md section.md judge.md (+ V3's)
```
