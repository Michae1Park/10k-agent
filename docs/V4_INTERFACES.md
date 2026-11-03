# V4 interfaces: schemas

The exact shape of every interface in the [design doc §2.3](V4_TECHNICAL_DESIGN_DOC.md#23-interface-catalog), numbered as there. The design doc says what each interface is for and its rules; this doc says what crosses it, with a real example. Examples are from UBER at as-of 2026-08-06 unless marked; lists are cut to one or two items.

The tool examples (§4) are checked against the code by `tests/test_interfaces.py`; when the code changes a field, that test fails until this doc is updated.

## Index

| # | Interface | Kind | Producer → consumer | Contract |
|---|---|---|---|---|
| 1 | [Raw cache](#1-raw-cache) | files | `tenk fetch` → `tenk build` | [design §3](V4_TECHNICAL_DESIGN_DOC.md#3-data-layer) |
| 2 | [Store](#2-store) | SQLite + Python API | builders, Report → every reader | [design §3](V4_TECHNICAL_DESIGN_DOC.md#3-data-layer) |
| 3 | [Search](#3-search) | call | Toolbox → Retrieval | [design §4](V4_TECHNICAL_DESIGN_DOC.md#4-retrieval) |
| 4 | [Tool calls](#4-tool-calls) | JSON call | agent ↔ Toolbox | [design §7](V4_TECHNICAL_DESIGN_DOC.md#7-toolbox) |
| 5 | [LLM calls](#5-llm-calls) | JSON reply | code ↔ model | [design §8](V4_TECHNICAL_DESIGN_DOC.md#8-agent-runtime) |
| 6 | [Extraction rows](#6-extraction-rows) | JSON reply → rows | extractor → Store | [design §5](V4_TECHNICAL_DESIGN_DOC.md#5-extraction-layer-1) |
| 7 | [Templates](#7-templates) | YAML | config → Model engine | [design §6](V4_TECHNICAL_DESIGN_DOC.md#6-model-engine-and-valuation) |
| 8 | [Model and valuation files](#8-model-and-valuation-files) | JSON files | Model engine → Report, tools, Export | [design §6](V4_TECHNICAL_DESIGN_DOC.md#6-model-engine-and-valuation) |
| 9 | [Pipeline artifacts](#9-pipeline-artifacts) | JSON files | step → step | [design §9](V4_TECHNICAL_DESIGN_DOC.md#9-report-pipeline) |
| 10 | [Reference ids](#10-reference-ids) | strings | everywhere | [design §10](V4_TECHNICAL_DESIGN_DOC.md#10-verification-and-number-trace) |
| 11 | [Fact list and log](#11-fact-list-and-log) | JSON files | Report code → note writer, trace | [design §10](V4_TECHNICAL_DESIGN_DOC.md#10-verification-and-number-trace) |
| 12 | [Outputs](#12-outputs) | folder, workbook | Export → reviewer, Ask, Research | [design §11](V4_TECHNICAL_DESIGN_DOC.md#11-outputs-and-review) |
| 13 | [Gold and results](#13-gold-and-results) | JSONL, folders | people, runs → Evaluation | [design §12](V4_TECHNICAL_DESIGN_DOC.md#12-evaluation) |

**By kind:** at rest 1, 2, 7, 8, 12, 13 · in flight 3, 4, 5, 6, 9 · shared by all 10, 11.

## Conventions

These hold for every interface below unless it says otherwise.

| Topic | Rule |
|---|---|
| **Notation** | `name: type`; `name?` optional (absent when it doesn't apply); `[T]` list; `{k: T}` map; `a \| b` one of; `id` a reference id (§10) |
| **Null vs absent** | `null` = a value that applies but is missing (a blank cell, no report yet). Absent = the field doesn't apply |
| **Period keys** | `2026Q2`, `FY2025`. Fiscal years are calendar years (all eight companies) |
| **Dates** | ISO `YYYY-MM-DD`; `accepted_at` is UTC |
| **As-of columns** | Every Store row carries the date it became public (`filing_date`, `filed`, `published`, `as_of`). A run at D sees rows dated strictly before D ([design §2.5](V4_TECHNICAL_DESIGN_DOC.md#25-cross-cutting-rules)) |
| **Units** | Each value carries its unit string. XBRL facts and `financials` rows are in whole USD; KPIs, guidance, model lines and valuation amounts in `USD millions`; counts in `count millions`; per-share in `USD per share` |
| **Ratios** | Ratio lines (take rate, margins): fractions in stored files (`model.json`); **percent** in tool results and resolved ids (`"unit": "percent"`). **Assumptions are fractions everywhere**, tool results included: `0.0847` = 8.47% ([§7 Assumptions](#assumptions)) |
| **Rounding** | Tool results round numbers to 4 decimals; the calculator to 6 |
| **Tool errors** | Tools never raise: `{"error": str, <choices>?: [str]}`, where `<choices>` names the valid values (`metrics`, `corpus`, `lines`, …) |
| **Caps** | Tool tables hold at most the row cap (design Appendix A), latest kept, with `"omitted": n` |

---

## 1. Raw cache

*Written by `tenk fetch`; read by `tenk build`.*

**Layout** (`data/v4/raw/`):

```text
filings/<T>/<period>-<form>/   FY2025-10K · 2026Q2-10Q · 2026Q2-ER · FY2025-DEF14A
    <documents>  metadata.json
edgar/<T>/companyfacts.json
prices/  macro/  crosscheck/tlc/<YYYY-MM>.json  crosscheck/bts.json
manifest.json                  ticker → [filing]
```

**`manifest.json`:**

```json
{"UBER": [{"ticker": "UBER", "cik": 1543151, "form": "10-K", "accession_no": "0001543151-23-000010",
           "filing_date": "2023-02-21", "accepted_at": "2023-02-21T21:10:08.000Z", "period_end_date": "2022-12-31",
           "fiscal_year": 2022, "fiscal_period": "FY",
           "documents": [{"name": "uber-20221231.htm", "type": "10-K", "url": "https://www.sec.gov/…"}],
           "retrieved_at": "…"}]}
```

**`companies.yaml`** (`TENK_COMPANIES` selects it):

```yaml
data_dir: data/v4
window: {since: '2023-01-01'}
companies:
  UBER:
    name: Uber Technologies, Inc.
    cik: 1543151            # written by `tenk companies verify`, never typed
    sector: mobility        # peers = same sector
    template: segments      # §7
    main_kpi: gross_bookings
```

## 2. Store

*Written at build time (and by Report for `model_versions`); read by everything, through the as-of filter.*

### 2.1 Tables

One SQLite file: the V4 tables below, plus V0–V3's `companies`, `sections`, `chunks`, `children`, FTS5 and vectors. Quarters are **not stored**: they are derived on read (design §3.2). A saved version's numbers live in its folder (§12), not in tables.

| Table | Key | Columns | As-of column |
|---|---|---|---|
| `filings` | `accession_no` | `ticker`, `form` (`10-K` \| `10-Q` \| `ER` \| `DEF14A`), `fiscal_year`: int, `fiscal_period` (`FY`, `Q1`–`Q3`; a release: the quarter it reports), `period_end_date`, `filing_date`, `accepted_at`, `source_url`, `retrieved_at` | `filing_date` |
| `chunks`, `children` | `id` (§10) | `ticker`, `fiscal_year`, `item`, `kind`, `text`, `units`, `printed_pages`, `form`, `fiscal_period`, `filed`; children add `parent_id` → chunks | `filed` |
| `facts` | `id` (§10) | `ticker`, `taxonomy`, `concept`, `dims` (JSON `{axis: member}`, `{}` = consolidated), `start` (null for an instant), `end`, `value`: real, `unit`, `decimals`, `accession`, `form`, `fy`, `fp`, `filed`, `source` (`companyfacts` \| `ixbrl`) | `filed` |
| `kpis` | (ticker, metric, segment, period, definition_version) | `value`: real, `unit`, `chunk_id`, `quote`, `method` (`llm` \| `rules`), `filed` | `filed` |
| `guidance` | (ticker, metric, segment, target_period, issued_in) | `low`, `high`: real, `unit`, `basis` (`level` \| `growth_yoy` \| `margin`), `chunk_id`, `quote`, `filed` | `filed` |
| `market` | (series, date) | `series` (`px:<T>:close\|adj_close\|volume`, `macro:<SERIES>`, `erp:damodaran`, `cc:…`), `date`, `value`: real, `published` | `published` |
| `insider_tx` | – | `ticker`, `accession`, `filed`, `owner`, `director`, `officer`, `title`, `date`, `code` (S, P, A, M, F, G), `shares`, `price`, `acquired_disposed` | `filed` |
| `model_versions` | `id` (`<T>:v<n>`) | `ticker`, `as_of`, `parent_id`, `inputs_hash`, `status` (`draft` \| `reviewed by …`), `path` (report folder) | `as_of` |

### 2.2 Read API

`Store(path: Path, asof: AsOf)`. Every read filters by `asof`; no method takes a later date.

```python
# text
search(query, tickers=None, fiscal_years=None, items=None, k=10, periods=None, forms=None) -> list[ChunkRecord]   # BM25
vector_search(vector, tickers=None, …same filters…) -> list[ChunkRecord]                                         # cosine
child_search(…) · child_vector_search(…) -> list[ChildRecord]                                                     # over children
get_chunk(chunk_id) -> ChunkRecord | None
chunks(ticker=None, fiscal_year=None) -> list[ChunkRecord]
filing(ticker, fiscal_year) -> FilingRecord | None
filings() -> list[FilingRecord]
# facts and extracted rows
facts(ticker, concepts: list[str], dims: dict | None = None, any_dims=False) -> list[dict]   # dims parsed
fact(fact_id) -> dict | None
kpis(ticker, metric=None) -> list[dict]
guidance(ticker, metric=None) -> list[dict]          # filing order: a later guide replaces an earlier one
# market and others
series(name, start=None) -> list[tuple[str, float]]  # (date, value), latest visible vintage
insider_summary(ticker, start, end) -> dict          # counts and shares by code
model_versions(ticker) -> list[dict]                 # as_of < D, oldest first
```

### 2.3 Write API

```python
# build time
add_filing(filing: FilingRecord, cik, aliases, sections, chunks, children=()) -> None
add_facts(facts: list) -> int
add_extractions(ticker, period, filed, kpis: list, guidance: list, method="llm") -> int
add_market(rows: list[tuple[series, date, value, published]]) -> None
add_insider_tx(ticker, rows: list[tuple]) -> int
add_embeddings(chunk_ids, vectors) · add_child_embeddings(child_ids, vectors) -> None
# run time
add_model_version(ticker, as_of, parent_id, inputs_hash, path, status="draft") -> str   # "<T>:v<n>"
set_version_status(version, status) -> None
```

## 3. Search

*Called by the `search_filings` tool; served by Retrieval.*

```text
search_filings(query: str, companies?: [str], periods?: [period], forms?: [form], k?: int = 8)    # k clamped to 1–16
  companies   tickers or names; ["all"] = every company with a filing
  periods     none = the latest filing of each form at the as-of
  forms       "10-K" | "10-Q" | "ER" | "DEF14A"
→ {query: str,
   searches: [{query: str, company: str, periods?: [period]}],
   chunks: [{chunk_id, company, form, period, filed, item, kind: prose | table, units: str | null, text}]}
```

Example (`k: 1`, text cut):

```json
{"query": "Uber mobility gross bookings growth",
 "searches": [{"query": "Uber mobility gross bookings growth", "company": "UBER", "periods": ["2026Q2"]}],
 "chunks": [{"chunk_id": "UBER-2026Q2-ER-EX99-001", "company": "UBER", "form": "ER", "period": "2026Q2",
             "filed": "2026-08-05", "item": "EX99", "kind": "prose", "units": null,
             "text": "Exhibit 99.1\nUber Announces Results for Second Quarter 2026\nGross Bookings grew 22% …"}]}
```

Knobs: `config.yaml` `retrieval` (design Appendix A).

## 4. Tool calls

*Called by the agent (and `tenk tool NAME key=value`); each tool fronts one block. What each computes: [design §7.2](V4_TECHNICAL_DESIGN_DOC.md#72-catalog).*

Every tool: `company` = a ticker or name; `periods` = period keys, default the latest eight; results follow the Conventions (percent, 4 decimals, caps, error shape). Each block below gives the signature, then a checked example: `call` is the arguments, `result` what the tool returns.

**Model tools** (`model_view`, `run_scenario`, `compare_peers`, `variance_review`) do not take financial figures. They read models the Model engine has already built: each company's base forecast at the as-of, with default assumptions and guidance anchors, built on first use ([design §7.3](V4_TECHNICAL_DESIGN_DOC.md#73-rules)). A caller sends names and selectors (company, line, metric, period). The only numbers a caller sends are assumption values in `run_scenario`. Assumption names, units and bounds: [§7 Assumptions](#assumptions).

### text · `search_filings`

§3.

### calc · `calculator`

```text
calculator(inputs: {name: number}, op?: pct_change | cagr | ratio | percent_of | difference | sum, expression?: str)
→ {result: number, formula: str, inputs: {name: number}}
```

```json
{"call": {"op": "pct_change", "inputs": {"old": 10, "new": 12}},
 "result": {"result": 20.0, "formula": "(new - old) / old * 100", "inputs": {"old": 10.0, "new": 12.0}}}
```

### facts · `financials`

```text
financials(company, metric: str, periods?: [period])
→ {company, metric, rows: [{id, period, value, unit, derivation: direct | ytd_diff | fy_minus_9m | instant, restated?: true}]}
error: unknown metric → {error, metrics: [str]}
```

```json
{"call": {"company": "UBER", "metric": "revenue", "periods": ["2026Q2"]},
 "result": {"company": "UBER", "metric": "revenue",
            "rows": [{"id": "q:UBER:revenue:2026Q2", "period": "2026Q2", "value": 14191000000.0,
                      "unit": "USD", "derivation": "direct"}]}}
```

### facts · `company_kpis`

```text
company_kpis(company, metric?: str, segment?: str, periods?: [period])
→ {company, rows: [{id, period, metric, segment, value, unit, chunk_id, quote}]}
```

```json
{"call": {"company": "UBER", "metric": "gross_bookings", "segment": "mobility", "periods": ["2026Q2"]},
 "result": {"company": "UBER",
            "rows": [{"id": "kpi:UBER:gross_bookings.mobility:2026Q2", "period": "2026Q2", "metric": "gross_bookings",
                      "segment": "mobility", "value": 28988.0, "unit": "USD millions",
                      "chunk_id": "UBER-2026Q2-ER-EX99-006", "quote": "| Mobility | $23,762 | $28,988 | 22% | 20% |"}]}}
```

### facts · `guidance`

```text
guidance(company, metric?: str, periods?: [period])
→ {company, rows: [{id, period, metric, segment, low, high, unit, basis, issued_in, chunk_id, quote,
                    actual?: number, actual_id?: id}]}           # actual: a level guide whose period is reported at D
```

```json
{"call": {"company": "UBER", "metric": "adj_ebitda", "periods": ["2026Q3"]},
 "result": {"company": "UBER",
            "rows": [{"id": "guide:UBER:adj_ebitda:2026Q3", "period": "2026Q3", "metric": "adj_ebitda",
                      "segment": "total", "low": 2860.0, "high": 2960.0, "unit": "USD millions", "basis": "level",
                      "issued_in": "2026Q2", "chunk_id": "UBER-2026Q2-ER-EX99-003",
                      "quote": "Our outlook translates to Adjusted EBITDA of $2.86 billion to $2.96 billion."}]}}
```

### facts · `filings`

```text
filings(company, form?: form)
→ {company, filings: [{form, period, period_end_date, filing_date, accession_no, id}], total: int}
```

```json
{"call": {"company": "UBER", "form": "10-Q"},
 "result": {"company": "UBER",
            "filings": [{"form": "10-Q", "period": "2026Q2", "period_end_date": "2026-06-30", "filing_date": "2026-08-05",
                         "accession_no": "0001543151-26-000032", "id": "0001543151-26-000032"}],
            "total": 11}}
```

### market · `market_data`

```text
market_data(company? | series?: str, dates?: [date], start?: date)     # exactly one of company and series
→ {series, unit, rows: [{id, date, value}]}
```

```json
{"call": {"company": "UBER", "dates": ["2026-08-05"]},
 "result": {"series": "px:UBER:close", "unit": "USD",
            "rows": [{"id": "px:UBER:2026-08-05", "date": "2026-08-05", "value": 68.18}]}}
```

### market · `crosscheck`

```text
crosscheck(series: tlc | bts, companies?: [str], start?: month, end?: month)
→ {series, coverage: str, unit: trips | passengers, rows: [{id, series, month, value}]}
```

```json
{"call": {"series": "tlc", "companies": ["UBER"], "start": "2026-05"},
 "result": {"series": "tlc",
            "coverage": "NYC only, trips completed by each license; a directional check on Mobility volume, not a company total. TLC publishes about two months after the month.",
            "unit": "trips",
            "rows": [{"id": "cc:tlc:HV0003:2026-05-01", "series": "UBER", "month": "2026-05", "value": 15354816.0}]}}
```

### reports · `research_report`

```text
research_report(company, lines?: [str], periods?: [period], as_of?: date)     # as_of only narrows
→ {company, version, as_of, status, parent: str | null, note: str,
   valuation: {price, price_target, return: {value, unit, id}, rating: {text, id}},
   versions: [{version, as_of, status}],
   estimates: [{line, period, kind: estimate | actual, value, unit, id}],
   unknown_lines?: {lines: [str], valid: [str]}}
none saved → {company, report: null, versions, note}
```

Example at as-of 2026-11-03:

```json
{"as_of": "2026-11-03",
 "call": {"company": "UBER", "lines": ["revenue"], "periods": ["FY2026"]},
 "result": {"company": "UBER", "version": "UBER:v2", "as_of": "2026-08-06", "status": "draft", "parent": null,
            "note": "draft: not yet approved by the senior analyst",
            "valuation": {"price": {"value": 68.18, "unit": "USD", "id": "model:UBER:2026-08-06:v2:price"},
                          "price_target": {"value": 83.6226, "unit": "USD", "id": "model:UBER:2026-08-06:v2:price_target"},
                          "return": {"value": 22.6498, "unit": "percent", "id": "model:UBER:2026-08-06:v2:return"},
                          "rating": {"text": "Buy", "id": "model:UBER:2026-08-06:v2:rating"}},
            "versions": [{"version": "UBER:v2", "as_of": "2026-08-06", "status": "draft"}],
            "estimates": [{"line": "revenue", "period": "FY2026", "kind": "estimate", "value": 57297.2102,
                           "unit": "USD millions", "id": "model:UBER:2026-08-06:v2:revenue.FY2026"}]}}
```

### model · `model_view`

```text
model_view(company, lines?: [str], periods?: [period])
→ {company, as_of, forecast_from: period, id_format: "model:<T>:<as_of>:base:<line>.<period>",
   rows: [{line, unit, values: {period: number | null}}],
   assumptions: [{driver, value, default, source: "default" | "guidance <guide id>"}],
   exceptions: [str]}
```

```json
{"call": {"company": "UBER", "lines": ["revenue"], "periods": ["2026Q2", "2026Q3", "FY2026"]},
 "result": {"company": "UBER", "as_of": "2026-08-06", "forecast_from": "2026Q3",
            "id_format": "model:UBER:2026-08-06:base:<line>.<period>",
            "rows": [{"line": "revenue", "unit": "USD millions",
                      "values": {"2026Q2": 14191.0, "2026Q3": 14564.1153, "FY2026": 57045.238}}],
            "assumptions": [{"driver": "cost_to_gb.shift0", "value": -0.0287, "default": -0.0293,
                             "source": "guidance guide:UBER:adj_ebitda:2026Q3"}],
            "exceptions": []}}
```

### model · `run_scenario`

```text
run_scenario(company, overrides?: {assumption: value}, deltas?: {assumption: delta})    # at least one
→ {company, version: "scratch-<n>", changes: {assumption: value}, clipped_to_bounds: {assumption: [lo, hi]},
   rows: [{line, period, base, scenario, id, base_id}],
   price_target: {base, scenario, id, base_id}, rating: {base, scenario}}
error: unknown driver or no change → {error, drivers: [str]}
```

| Field | Meaning |
|---|---|
| `overrides` | The driver's new value |
| `deltas` | Added to the driver's base value (see it with `model_view`) |
| Values | Fractions ([§7 Assumptions](#assumptions)): a take rate 50 bps lower is a delta of `-0.005` |
| Allowed drivers | Every `.g0` and `.shift0` with history, and `fade`. A driver anchored to guidance may be changed too: the scenario replaces the anchor |
| `changes` | The final value of each changed driver, after clipping |
| `clipped_to_bounds` | Each driver that was clipped, with its `[lo, hi]` |
| `rows` | `revenue`, `adj_ebitda`, `eps_diluted`, `free_cash_flow` for the last two FY columns, base vs scenario |

In the example, the base `take_rate_mobility.shift0` is `-0.0527` (the mobility take rate 5.27 points below the same quarter last year). The delta `+0.005` makes it `-0.0477`, inside its bounds, so nothing is clipped.

```json
{"call": {"company": "UBER", "deltas": {"take_rate_mobility.shift0": 0.005}},
 "result": {"company": "UBER", "version": "scratch-1",
            "changes": {"take_rate_mobility.shift0": -0.0477}, "clipped_to_bounds": {},
            "rows": [{"line": "revenue", "period": "FY2027", "base": 63587.5395, "scenario": 63906.8998,
                      "id": "model:UBER:2026-08-06:scratch-1:revenue.FY2027",
                      "base_id": "model:UBER:2026-08-06:base:revenue.FY2027"}],
            "price_target": {"base": 79.2364, "scenario": 80.6961,
                             "id": "model:UBER:2026-08-06:val-scratch-1:price_target",
                             "base_id": "model:UBER:2026-08-06:val-base:price_target"},
            "rating": {"base": "Buy", "scenario": "Buy"}}}
```

### model · `compare_peers`

```text
compare_peers(metric: str, companies?: [str], period?: period = FY2027)
→ {metric, period: period | null, rows: [{company, value, unit, id, assumptions: [{driver, value, source: default | guidance}]}]}
error: unknown metric → {error, model_lines: [str], valuation: [str]} · unknown company → {error}
```

**`metric`** is a valuation field or a model line:

| `metric` | Value | `unit` | `period` |
|---|---|---|---|
| `ev_to_ebitda`, `ev_to_ebitda_less_sbc`, `pe` | 2027E multiple | `""` | ignored; `null` in the result |
| `fcf_yield` | 2027E free-cash-flow yield | `percent` | ignored |
| `price_target` | Scenario-weighted price target | `USD` | ignored |
| `return` | Price target / price − 1 | `percent` | ignored |
| `rating` | `Buy`, `Hold` or `Sell` (sorted last) | `""` | ignored |
| `ev`, `market_cap` | Amount in USD millions | `""` | ignored |
| Any model line: `revenue`, `adj_ebitda`, `adj_ebitda_margin`, `eps_diluted`, `free_cash_flow`, `take_rate`, … | The line in `period` | The line's unit (ratios in percent) | Default `FY2027` |

- **Companies:** default all eight. Each value comes from that company's base model at the same as-of.
- **Rows:** sorted ascending by value; non-numbers last.
- **`assumptions`:** up to four `.g0` and `.shift0` drivers behind that company's model, as fractions. `guidance` means an anchor solved it. Templates differ, so driver names differ between companies (`volume_g.g0` for LYFT's `orders` template, `gb_g_mobility.g0` for UBER's `segments`).

```json
{"call": {"metric": "ev_to_ebitda", "companies": ["UBER", "LYFT"]},
 "result": {"metric": "ev_to_ebitda", "period": null,
            "rows": [{"company": "LYFT", "value": 7.4106, "unit": "", "id": "model:LYFT:2026-08-06:val-base:ev_to_ebitda",
                      "assumptions": [{"driver": "volume_g.g0", "value": 0.0847, "source": "default"}]},
                     {"company": "UBER", "value": 9.6967, "unit": "", "id": "model:UBER:2026-08-06:val-base:ev_to_ebitda",
                      "assumptions": [{"driver": "gb_g_mobility.g0", "value": 0.2199, "source": "default"}]}]}}
```

### model · `variance_review`

```text
variance_review(company, period: period)                   # period reported at D
→ {company, period, estimate_as_of: date,
   rows: [{line, unit, estimate, actual, variance_pct, estimate_id, actual_id}],       # variance_pct in percent
   bridge: {revenue_variance, from_gross_value, from_take_rate, from_volume?, from_value_per_unit?},
   bridge_unit: "USD millions", bridge_ids: {field: id},
   estimate_assumptions: [{driver, value}]}
```

```json
{"call": {"company": "UBER", "period": "2026Q2"},
 "result": {"company": "UBER", "period": "2026Q2", "estimate_as_of": "2026-05-07",
            "rows": [{"line": "revenue", "unit": "USD millions", "estimate": 14082.0586, "actual": 14191.0,
                      "variance_pct": 0.7736, "estimate_id": "model:UBER:2026-05-07:base:revenue.2026Q2",
                      "actual_id": "q:UBER:revenue:2026Q2"}],
            "bridge": {"revenue_variance": 108.9414, "from_gross_value": 252.4888, "from_take_rate": -143.5474},
            "bridge_unit": "USD millions",
            "bridge_ids": {"revenue_variance": "model:UBER:2026-08-06:var-2026Q2:revenue_variance",
                           "from_gross_value": "model:UBER:2026-08-06:var-2026Q2:from_gross_value",
                           "from_take_rate": "model:UBER:2026-08-06:var-2026Q2:from_take_rate"},
            "estimate_assumptions": [{"driver": "gb_g_mobility.g0", "value": 0.2461}]}}
```

## 5. LLM calls

*Made by code; each reply is one JSON object, parsed by `models.parse_json`. Tool sets and budgets: [design §8.1](V4_TECHNICAL_DESIGN_DOC.md#81-contract).*

| Call | Prompt | Tool set | Reply |
|---|---|---|---|
| Ask / Research answer | `ask.md` / `research.md` | `ask` / `research` | Answer, below |
| KPI extraction | `extract.md` | – | §6 |
| S2 events | `events.md` | `ask` | `{answer, events: [{type, summary, evidence: [id]}]}`; type = `results \| guidance \| definition_change \| business_model \| m&a \| capital \| regulation \| catalyst \| other` |
| S4 proposer | `propose.md` | `ask` | `{proposals: [{driver, value, basis, confidence, rationale, evidence: [id]}]}` |
| S9 debate, per driver | `debates.md` | `ask` | `{answer, debate: {question, bull, bear, hypothesis, trigger, gap, evidence: [id]}}` |
| S10 prose slot | `section.md` | – | `{text}`, facts marked `{F-n}` |
| Judge (evaluation) | `judge.md` | – | scores per criterion |

**Answer** (Ask and Research):

```text
{answer: str,                         # cites claims as [n]
 claims: [{id: int, text, value: number | null, unit, company, period, chunk_ids: [id],
           calculation: {op, expression, inputs: {name: number}, input_unit, result} | null}],
 assumptions?: [{driver, value, source}],          # Research
 table: {columns, rows} | null, scope_notice: str | null, abstained: bool}
```

The claim check adds `status` (`verified | calculated | cited | unverified`) and `status_reason` to each claim. Example (as-of 2026-09-01, one of three claims):

```json
{"answer": "In Q2 2026, Lyft reported 262.4 million rides [1], while Uber reported 3,867 million trips [2]. Uber's trips were approximately 14.7 times the number of Lyft's rides [3].",
 "claims": [{"id": 3, "text": "Uber's trips were approximately 14.7 times Lyft's rides in Q2 2026.",
             "value": 14.737043, "unit": "ratio", "company": null, "period": "2026Q2",
             "chunk_ids": ["kpi:LYFT:rides:2026Q2", "kpi:UBER:trips:2026Q2"],
             "calculation": {"op": "ratio", "expression": "numerator / denominator",
                             "inputs": {"numerator": 3867, "denominator": 262.4},
                             "input_unit": "count millions", "result": 14.737043},
             "status": "calculated", "status_reason": "numerator / denominator = 14.737043"}],
 "table": null, "scope_notice": "Lyft's 'rides' and Uber's 'trips' … definitions may differ.", "abstained": false}
```

## 6. Extraction rows

*Produced by the extractor from release chunks; stored in `kpis` and `guidance` (§2).*

**Registry** (`facts/kpis.yaml`):

```yaml
UBER:
  gross_bookings: {unit: USD millions, segments: [total, mobility, delivery, freight], labels: ["Gross Bookings"]}
  trips:          {unit: count millions, labels: ["Trips"]}
```

**Reply** (`extract.md`):

```json
{"kpis": [{"metric": "gross_bookings", "segment": "mobility", "value": 28988, "unit": "USD millions",
           "chunk_id": "UBER-2026Q2-ER-EX99-006", "quote": "| Mobility | $23,762 | $28,988 | 22% | 20% |"}],
 "guidance": [{"metric": "adj_ebitda", "segment": "total", "target_period": "Q3 2026", "low": 2860, "high": 2960,
               "unit": "USD millions", "basis": "level", "chunk_id": "UBER-2026Q2-ER-EX99-003",
               "quote": "Our outlook translates to Adjusted EBITDA of $2.86 billion to $2.96 billion."}]}
```

**Stored rows** add: `target_period` normalized to a key (`2026Q3`); `period` or `issued_in` = the release's quarter; `filed`; `definition_version`; `method`. Ids: §10.

## 7. Templates

*Read by the Model engine; one YAML per template (`research/model/templates/`).*

```yaml
lines:
  volume:         {kind: input, source: "kpi:total_orders", unit: count millions, label: orders}
  gross_bookings: {kind: input, source: "kpi:marketplace_gov", unit: USD millions,
                   forecast: "volume * value_per_unit"}
  take_rate:      {kind: formula, expr: "revenue / gross_bookings", unit: fraction}
  revenue:        {kind: input, source: "q:revenue", fallback: "kpi:revenue", statement: income,
                   forecast: "gross_bookings * take_rate"}
forecast:  {growth: [volume, value_per_unit], ratios: {take_rate: take_rate, cost_to_gb: margin}}
scenarios: [volume_g.g0, value_per_unit_g.g0, take_rate.shift0]
anchors:   [{guide: gross_bookings, line: gross_bookings, basis: level, solve: value_per_unit_g.g0, period?: fy}]
```

| Field | Meaning |
|---|---|
| `kind` | `input` (history from `source`, else `fallback`; blue, carries provenance) · `formula` (black) · `assumption` (blue) |
| `source`, `fallback` | A data-pack series: `q:<metric>` or `kpi:<metric>[.<segment>]` |
| `expr`, `forecast` | DSL: history formula; forecast formula |
| `anchors` | Each guide → the line it anchors and the assumption solved to hit it; template order sets priority |

**DSL:** arithmetic; `x[-4]` (same quarter last year); `fy(x)`, `fyp(x)` (this and last fiscal year's sum); `min`, `max`, `if_`, `first`; `@name` = an assumption. Anything else is rejected.

### Assumptions

The forecast knobs a template names (`@name`), stored in `model.json` `assumptions` and shown by `model_view`. Each gets a default from history (S5); guidance anchors may then solve some of them. All are **fractions** unless marked.

| Name | Meaning | Default | Bounds (`run_scenario`, proposer) |
|---|---|---|---|
| `<driver>_g.g0` (`volume_g.g0`, `gb_g_mobility.g0`, `trips_g.g0`) | The driver's y/y growth in the first forecast quarter; it fades by `fade` each quarter after | The driver's last reported y/y | Its last 8 quarters' range ± 0.05 |
| `<ratio>.shift0` (`take_rate_mobility.shift0`, `cost_to_gb.shift0`) | The ratio's change vs the same quarter last year, held for 4 quarters, then 0 | The ratio's last y/y change | ± (its 8-quarter range + the ratio's band) |
| `fade` | Growth multiplier per quarter: g(t+1) = g(t) × fade | 0.9 | 0.75–1.0 (`research.yaml`) |
| `<line>.s1`–`.s4` | Each quarter's share of the fiscal year | 3-year mean | Fixed |
| `<line>.ratio` (`capex`, `d_and_a`, `sbc`) | Share of revenue | Trailing 4 quarters | Fixed |
| `<ratio>.lo`, `<ratio>.hi` | Clamps on a ratio line (not shown by tools) | 8-quarter range ± band | – |
| `tax_rate` | Tax rate | Trailing 4 quarters, within 10–30% | Fixed |
| `shares_last` | Last diluted shares (**count millions**) | Last reported | Fixed |
| `share_change` | Quarterly change in share count | Trend over the last 5 quarters | Fixed |
| `buybacks_q` | Buybacks per quarter (**USD millions**) | Trailing 4-quarter mean | Fixed |
| `gb_g_adj` | Adjustment to gross-bookings growth that a bookings guide solves (UBER) | 0 | Fixed |

"Fixed" means `run_scenario` rejects it. Example (UBER at 2026-08-06): `gb_g_mobility.g0` = 0.2199 (22% growth), `take_rate_mobility.shift0` = −0.0527, `cost_to_gb.shift0` = −0.0287, solved by `guide:UBER:adj_ebitda:2026Q3`.

## 8. Model and valuation files

*Written by S5 and S7; read by Report, tools and Export. Knobs: `research.yaml` (design Appendix A).*

**`model.json`:**

```text
{ticker, template, as_of,
 columns: [period],                       # quarters and FY columns, in order
 forecast: [period],                      # forecast quarters
 assumptions: {name: number},             # fractions for ratios
 lines: [{name, kind, label, unit, statement, fy, source, expr, forecast, forecast_first}],
 cells: {"<line>|<period>": {value: number | null, kind: input | formula, ref: id | null, filed: date | null}}}
```

```json
{"cells": {"revenue|2026Q2": {"value": 14191.0, "kind": "input", "ref": "q:UBER:revenue:2026Q2", "filed": "2026-08-05"}},
 "lines": [{"name": "adj_ebitda_margin", "kind": "formula", "label": "adjusted EBITDA margin (on revenue)",
            "unit": "fraction", "statement": "income", "fy": "formula", "source": null,
            "expr": "adj_ebitda / revenue", "forecast": null, "forecast_first": null}]}
```

**`valuation.json`:**

```text
{ticker, as_of, price_date,
 price, shares, market_cap, debt, cash_and_investments, net_debt, ev,          # USD; amounts in millions
 refs: {price, shares, debt, cash, short_term_investments: id},
 cost_of_capital: {rf, beta, beta_months, erp, erp_id, ke, kd, debt_weight, wacc},
 peer_multiples: {peers: {T: number}, median, range: [lo, hi]},              # target-year EV/EBITDA
 multiples_2027e: {ev_to_ebitda, ev_to_ebitda_less_sbc, pe, fcf_yield},
 scenarios: {bear | base | bull: {adj_ebitda, target_multiple, multiple_pt, multiple_outside_peer_range,
                                  dcf_ev, dcf_pt, dcf_used, price_target}},
 price_target, return,                                                         # weighted PT; PT / price − 1 (fraction)
 rating: Buy | Hold | Sell,
 dcf_grid: {"<wacc>|<g>": PT},
 dcf_base: {wacc, terminal_growth, tax_rate, flows: [[period, t, ufcf]], terminal_t, last_annual, terminal, ev, terminal_share},
 exceptions: [str],
 inputs_hash: str}                                                             # SHA-256 of assumptions, market refs, price
```

## 9. Pipeline artifacts

*Written by one step, read by later ones; all in the report folder (§12). Steps and who reads what: [design §9.1](V4_TECHNICAL_DESIGN_DOC.md#91-contract).*

| Artifact | Step | Schema |
|---|---|---|
| `events.json` | S2 | `[{type, summary, evidence: [id]}]` (§5) |
| `debates.json` | S9 | `[{driver, question, bull, bear, hypothesis, trigger, gap, evidence: [id], evidence_values: [{id, value, unit}]}]` |
| `revisions.json` | S8 | `[{label, old, new, unit, old_id, new_id, type: "Reported + assumption" \| "Assumption", confidence: Medium \| Low}]`; `[]` without a parent |
| `facts.json` | S10 | §11 |
| `note.json` | S10 | `{sections: [title], blocks: [str], slots: {key: text}, slot_facts: [{key, text, facts: [F-id]}], untraced: [str]}` |
| `exceptions.json` | S10–S11 | §11 |
| `report.json` | S11 | below |

**`events.json`** (one of six):

```json
[{"type": "results",
  "summary": "Uber reported Q2 2026 revenue of $14.19 billion, up 12% YoY, and GAAP diluted EPS of $1.17 …",
  "evidence": ["kpi:UBER:revenue:2026Q2", "kpi:UBER:eps_diluted:2026Q2"]}]
```

**`report.json`:**

```text
{ticker, as_of, version, parent: str | null, rating, price_target,
 sections: int, facts: int, untraced: int, exceptions: int,
 consistency: {check: bool}, parity: "<ok>/<total>", folder,
 timing: {seconds, steps: {step: {seconds, calls?, input_tokens?, output_tokens?}}}}
```

```json
{"ticker": "UBER", "as_of": "2026-08-06", "version": "UBER:v2", "parent": null, "rating": "Buy",
 "price_target": 83.62262237, "sections": 10, "facts": 162, "untraced": 0, "exceptions": 18,
 "parity": "750/750", "folder": "output/reports/UBER/2026-08-06"}
```

## 10. Reference ids

*Resolved by `research/refs.py` under an as-of; used in every tool result, answer and note. This is the one id grammar: other sections point here.*

| Kind | Grammar | Example | Resolves to |
|---|---|---|---|
| chunk | `<T>-<period>-<item>-<nnn>` | `UBER-FY2025-7-003` · `UBER-2026Q2-I.2-014` · `UBER-2026Q2-ER-EX99-005` · `UBER-FY2025-DEF14A-PROXY-012` | chunk text |
| accession | SEC accession number | `0001543151-26-000032` | filing metadata |
| XBRL fact | `xbrl:<T>:<taxonomy>:<concept>:<accession>:<start>/<end>` (instant: `…:<end>`) | `xbrl:UBER:us-gaap:Revenues:<acc>:2026-04-01/2026-06-30` | value, unit (end-only ids also resolve) |
| inline XBRL | `ixbrl:<accession>:<fact id>` | | parses only |
| derived quarter | `q:<T>:<metric>:<period>` | `q:UBER:revenue:2026Q2` | value, derivation, input fact ids, `filed` (latest input) |
| KPI | `kpi:<T>:<metric>[.<segment>]:<period>` | `kpi:UBER:gross_bookings.mobility:2026Q2` | the row + source chunk |
| guidance | `guide:<T>:<metric>:<period>` | `guide:UBER:adj_ebitda:2026Q3` | low, high + source chunk |
| price | `px:<T>:<date>` | `px:UBER:2026-08-05` | close |
| macro | `macro:<SERIES>:<date>` | `macro:DGS10:2026-08-05` | value |
| cross-check | `cc:tlc:<license>:<month>` · `cc:bts:<series>:<month>` | `cc:tlc:HV0003:2026-05-01` | value |
| model | `model:<T>:<as_of>:<run>:<line>.<period>` or `…:<run>:<field>` | `model:UBER:2026-08-06:base:revenue.FY2027` | model or valuation value |
| evidence, exception | `E-<n>` · `X-<nn>` | `E-12` · `X-03` | parses only |
| fact | `F-<n>` | `F-2` | a fact-list entry (§11) |

**Chunk items:** 10-K Items (`1`, `1A`, `7`, `8`, …); 10-Q Items per Part (`I.2`, `II.1A`); a release has one pseudo-Item `ER-EX99`; a proxy `DEF14A-PROXY`.

**Model runs:** `base` (default forecast, rebuilt on demand) · `scratch-<n>` (a `run_scenario` run this session) · `val-<run>` (that run's valuation fields: `price_target`, `ev`, `rating`, …) · `var-<period>` (`variance_review`'s bridge) · `v<n>` (a saved version, resolves only if saved before the as-of).

Data kinds resolve to `{value, unit, ticker}` in the row's own unit; ratios to percent.

## 11. Fact list and log

*Built by Report code (S10); read by the note writer and the trace.*

**`facts.json`** (one entry per figure the note may print):

```text
[{id: "F-<n>", label, value: number | str, unit, source: id, period: period | "", filed: date | ""}]
```

```json
[{"id": "F-1", "label": "Price", "value": 68.18, "unit": "USD", "source": "px:UBER:2026-08-05", "period": "", "filed": ""}]
```

Code formats each fact once (rounding, "2027E"/"2025A", signs, units); the writer must print it verbatim, marked `{F-1}`.

**`exceptions.json`** (`X-n` rows from every step):

```text
[{id: "X-<nn>", severity: info | warning | error, item: str}]
```

```json
[{"id": "X-01", "severity": "info", "item": "shares_diluted missing in 2023Q4, 2024Q4, 2025Q4"}]
```

`E-n` evidence rows are built from the facts (label, value, source id) and shown with the `X-n` rows on the workbook's Log tab.

## 12. Outputs

*Written by S11; read by the reviewer and, through saved versions, by Ask and Research.*

**Report folder** `output/reports/<TICKER>/<as_of>/`:

```text
note.md  note.pdf  workbook.xlsx
model.json  valuation.json                         §8
events.json  debates.json  revisions.json         §9
facts.json  note.json  exceptions.json  report.json
```

Saved as a version (`UBER:v1`, `UBER:v2`, …) with its parent. `research_report` and `model:…:v<n>:…` ids read these files.

**Workbook:**

| Tab | Contents |
|---|---|
| Model | lines × quarters and FY; inputs blue with provenance, formulas black, links to assumptions green |
| Assumptions | name and value (the cells the Model tab's formulas link to) |
| Summary | rating, price, price date, PT, return, market cap, EV, net debt, shares, WACC, parity, bridge |
| Scenarios | scenario line, year, value |
| Valuation | peer multiples, median, cost of capital, scenario PTs, DCF grid |
| DCF | base-case UFCF with live discount, terminal value, EV, terminal share and per-share formulas; the WACC × growth grid |
| Revisions | line, old, new, type, confidence |
| Model (prior) | the parent's values as saved, or "initial draft (no prior run)" |
| Checks | check, status, tested, failures, skipped |
| Log | id, kind (evidence or exception), label or severity, value or item, source |

**Review:** `tenk research approve TICKER:vN --by INITIALS` sets `status` to `"reviewed by <INITIALS> on <date>"`; anything other than `draft` reads as reviewed.

## 13. Gold and results

*Read by the Evaluation scorers.*

**Gold files** (`evaluation/gold/`, JSONL; every row records `verified_by`):

| File | Row |
|---|---|
| `extraction-gold.jsonl` | `{id, ticker, period, metric, segment, value, unit, source, block, quote, verified_by}` |
| `ask-gold.jsonl` | `{id, split, category, kind, question, as_of, period, form, answer_type, expected_answer: [{value, unit, company}], tolerance, required_points, should_abstain, verified_by}` |
| `research-gold.jsonl` | `{id, split, category, company, question, as_of, expected_refs: [regex], requires_assumptions, expected_tools, verified_by}` |

**Result folders:** `evaluation/results/backtest-<label>/` (`report.md`, `forecasts.csv`, `summary.json`, `llm_baseline.json`); Ask and Research runs under `output/generation/<label>/` (`results.json`, `scores.json`).
