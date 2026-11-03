# V4 interfaces: schemas

The full schema of every interface in [design doc §2.3](V4_TECHNICAL_DESIGN_DOC.md#23-interface-catalog), numbered as there. The design doc states each contract in one line and the rules around it; this doc is the lookup reference. Where the two disagree, the design doc wins. Tool rules and tool sets: [design doc §7](V4_TECHNICAL_DESIGN_DOC.md#7-toolbox).

## 1. Raw cache

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
    template: segments      # templates: §7 below
    main_kpi: gross_bookings
```

## 2. Store

**Store schema** (one SQLite file, D-005; V4 tables, plus V0–V3's `companies`, `sections`, `chunks`, `children`, FTS5 and vectors):

| Table | Columns (type; key) | As-of column |
|---|---|---|
| `filings` | `accession_no` TEXT PK, `ticker`, `form` (`10-K`/`10-Q`/`ER`/`DEF14A`), `fiscal_year` INT, `fiscal_period` (`FY`, `Q1`–`Q3`; a release: the quarter it reports), `period_end_date`, `filing_date`, `accepted_at` (UTC), `source_url`, `retrieved_at` | `filing_date` |
| `chunks`, `children` | `id` TEXT PK (chunk ids below), `ticker`, `fiscal_year`, `item`, `kind`, `text`, `units`, `printed_pages`, `form`, `fiscal_period`, `filed`; children add `parent_id` → chunks | `filed` |
| `facts` | `id` TEXT PK (`xbrl:`/`ixbrl:` id), `ticker`, `taxonomy`, `concept`, `dims` (JSON `{axis: member}`, `{}` = consolidated), `start` (NULL for an instant), `end`, `value` REAL, `unit`, `decimals`, `accession`, `form`, `fy`, `fp`, `filed`, `source` (`companyfacts`/`ixbrl`) | `filed` |
| `kpis` | `ticker`, `metric`, `segment` (`total` = consolidated), `period`, `value` REAL, `unit`, `definition_version` INT, `chunk_id`, `quote`, `method` (`llm`/`rules`), `filed` | `filed` |
| `guidance` | `ticker`, `metric`, `segment`, `target_period`, `low`, `high` REAL, `unit`, `basis` (`level`/`growth_yoy`/`margin`), `chunk_id`, `quote`, `issued_in`, `filed`; PK (ticker, metric, segment, target_period, issued_in) | `filed` |
| `market` | `series` (`px:<T>:close\|adj_close\|volume`, `macro:<SERIES>`, `erp:damodaran`, `cc:…`), `date`, `value` REAL, `published` | `published` (and `date`) |
| `insider_tx` | `ticker`, `accession`, `filed`, `owner`, `director`, `officer`, `title`, `date`, `code` (S, P, A, M, F, G), `shares`, `price`, `acquired_disposed` | `filed` |
| `model_versions` | `id` TEXT PK (`<T>:v<n>`), `ticker`, `as_of`, `parent_id`, `inputs_hash`, `status` (`draft`/`reviewed`), `path` (report folder) | `as_of` |

Quarters are **not stored** (design §3.3). A saved version's numbers live in its folder's files (§9), not in tables.

**Chunk ids** (parent-child chunks, V0):

| Form | Items | Chunk id example |
|---|---|---|
| 10-K | as V0 (1, 1A, 7, 7A, 8, 15, …) | `UBER-FY2025-7-003` |
| 10-Q | per Part: `I.1`…`I.4`, `II.1`, `II.1A` (Items repeat per Part) | `UBER-2026Q2-I.2-014` |
| Release | one pseudo-Item `EX99` | `UBER-2026Q2-ER-EX99-005` |
| DEF 14A | one pseudo-Item `PROXY` | `UBER-FY2025-DEF14A-PROXY-012` |

**Fact and quarter ids:** `xbrl:<T>:<tax>:<concept>:<accession>:<start>/<end>` for durations (three-month and year-to-date facts share an end date, build log §3.3), `…:<end>` for instants; `q:<T>:<metric>:<period>` for a derived quarter, which records its derivation (`direct`, `ytd_diff`, `fy_minus_9m`), input fact ids and `filed` (the latest input).

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

## 3. Search

```text
search_filings(query: str, companies?: [str], periods?: [str], forms?: [str], k?: int = 8)
  companies   tickers or names; ["all"] = every company
  periods     period keys ("2026Q2", "FY2025"); none = the latest filing of each form at the as-of
  forms       "10-K" | "10-Q" | "ER" | "DEF14A"
→ {"query": str,
   "searches": [{"query": str, "company": str, "periods"?: [str]}],
   "chunks": [{"chunk_id", "company", "form", "period", "filed", "item", "kind", "units", "printed_pages", "text"}]}
```

Chunk ids: §2. Knobs: `config.yaml` `retrieval` (design Appendix A).

## 4. Tool calls

All as-of bound; `company` = ticker or name; `periods` = period keys, default the latest eight; tables are capped at 40 rows with `"omitted": n`. Rules: design §7.

| Tool | Arguments (required in bold) | Result | Backed by |
|---|---|---|---|
| `search_filings` | **query**, companies, periods, forms, k | §3 | Retrieval (design §4) |
| `calculator` | **inputs** `{name: number}`, `op` or `expression` | `{result, formula, inputs}` | `agent/tools.py` |
| `financials` | **company**, **metric**, periods | `{company, metric, rows: [{id (q:/xbrl:), period, value, unit, derivation, restated?}]}` | Store `facts`, derived quarters (design §3) |
| `company_kpis` | **company**, metric, segment, periods | `{company, rows: [{id (kpi:), metric, segment, period, value, unit, chunk_id, quote}]}` | Store `kpis` (design §3, §5) |
| `guidance` | **company**, metric, periods | `{company, rows: [{id (guide:), metric, segment, period, low, high, unit, basis, issued_in, chunk_id, quote, actual?, actual_id?}]}` | Store `guidance` (design §3, §5) |
| `filings` | **company**, form | `{company, filings: [{id, accession_no, form, period, period_end_date, filing_date}], total}` | Store `filings` (design §3) |
| `market_data` | company, series, dates, start | `{series, unit, rows: [{id (px:/macro:), date, value}]}` | Store `market` (design §3) |
| `crosscheck` | **series** (`tlc`/`bts`), companies, start, end | `{series, coverage, unit, rows: [{id (cc:), month, value}]}` | Store `market` (design §3) |
| `research_report` | **company**, lines, periods, as_of | `{company, version, as_of, status, parent, note, valuation: {price, price_target, return, rating: {value, id}}, estimates: [{line, period, kind, value, unit, id}], versions}`; none → `{report: null, versions, note}` | saved versions (design §11.2) |
| `model_view` | **company**, lines, periods | `{company, as_of, forecast_from, id_format: "model:<T>:<as_of>:base:<line>.<period>", rows: [{line, unit, values: {period: value}}], assumptions: [{driver, value, default, source: default \| guidance <id>}]}` | model book (design §6) |
| `run_scenario` | **company**, overrides `{assumption: value}`, deltas `{assumption: delta}` | `{company, version (scratch-n), changes, clipped_to_bounds, rows: [{line, period, base, scenario, id, base_id}], price_target: {base, scenario, id, base_id}, rating: {base, scenario}}` | model book, forecast, valuation (design §6) |
| `compare_peers` | **metric**, companies, period | `{metric, period, rows: [{company, value, unit, id, assumptions: [{driver, value, source}]}]}` | model book (design §6) |
| `variance_review` | **company**, **period** | `{company, period, estimate_as_of, rows: [{line, unit, estimate, estimate_id, actual, actual_id, variance_pct}], bridge, bridge_ids, bridge_unit}` | saved version vs Store (design §6, §11.2) |

## 5. LLM calls

**LLM calls** (each reply is one JSON object, parsed by `models.parse_json`):

| Call | Prompt | Tools (design §7.2) | Reply schema | Budget |
|---|---|---|---|---|
| Ask / Research answer | `ask.md` / `research.md` | `ask` / `research` | `{answer, claims: [{id, text, value, unit, company, period, chunk_ids, calculation: {op, expression, inputs, input_unit, result} \| null}], assumptions (Research): [{driver, value, source}], table: {columns, rows} \| null, scope_notice, abstained}` | `config.yaml` `agent`: 20 calls, 20k context |
| KPI extraction | `extract.md` | – | §6 | one call + one repair per release |
| S2 events | `events.md` | `ask` | `{answer, events: [{type: results\|guidance\|definition_change\|business_model\|m&a\|capital\|regulation\|catalyst\|other, summary, evidence: [id]}]}` | `budgets.events`: 8 calls, 16k context, 3k out |
| S4 proposer | `propose.md` | `ask` | `{proposals: [{driver, value, basis, confidence, rationale, evidence: [id]}]}` | `proposer.budget`: 10 calls, 16k context, 3k out |
| S9 debate, per driver | `debates.md` | `ask` | `{answer, debate: {question, bull, bear, hypothesis, trigger, gap, evidence: [id]}}` | `budgets.debate`: 8 calls, 16k context, 3k out |
| S10 prose slot | `section.md` | – | `{text}` with facts marked `{F-n}` | `budgets.section`: 1.5k out, one repair |
| Judge (evaluation) | `judge.md` | – | scores per criterion | gpt-oss-20b, a second pass |

## 6. Extraction rows

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

**Stored rows:** the `kpis` and `guidance` tables (§2): `target_period` normalized to a key (`2026Q3`), plus `period`/`issued_in` (the release's quarter), `filed`, `definition_version`, `method`. Ids: `kpi:<T>:<metric>[.<segment>]:<period>`, `guide:<T>:<metric>:<period>`.

## 7. Templates

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

**DSL:** arithmetic; `x[-4]` (same quarter last year); `fy(x)`, `fyp(x)` (this and last fiscal year's sum); `min`, `max`, `if_`, `first`; `@name` = an assumption. Node kinds: `input` (history, blue, provenance), `assumption` (blue), `formula` (black). Anything else is rejected.

## 8. Model and valuation files

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
| `cost_of_capital` | {`rf`, `beta`, `beta_months`, `erp`, `erp_id`, `ke`, `kd`, `debt_weight`, `wacc`} | design §6.3 |
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

## 9. Pipeline artifacts

| Step | Kind | Reads | Writes |
|---|---|---|---|
| S1 datapack | code | Store | data pack (in memory) |
| S2 events | sub-agent | Store, search | `events.json` |
| S3 model | code | data pack, template | history model; layer-2 (FAIL stops) |
| S4 propose | sub-agent (off by default) | driver table, Store | assumption overrides |
| S5 forecast | code | model, guidance, `research.yaml` | `model.json` (§8); layer-2 again |
| S6 peers | code | Store, per peer | peer models (model book) |
| S7 value | code | model, peers, market | `valuation.json` (§8); valuation checks |
| S8 revise | code | the parent's `model.json` | `revisions.json` |
| S9 debates | sub-agent per scenario driver | model, Store, search | `debates.json`; section 7 sensitivities |
| S10 write | code + LLM | all artifacts | `facts.json`, `note.json`; consistency check |
| S11 export | code | all artifacts | `note.md`, `note.pdf`, `workbook.xlsx`, `exceptions.json`, `report.json`; `model_versions` row |

| Artifact | Schema |
|---|---|
| `events.json` | `[{type, summary, evidence: [id]}]` (§5) |
| `debates.json` | `[{driver, question, bull, bear, hypothesis, trigger, gap, evidence: [id], evidence_values: [{id, value, unit}]}]` |
| `revisions.json` | `[{label, old, new, unit, old_id, new_id, type: "Reported + assumption"\|"Assumption", confidence: Medium\|Low}]` for revenue, adj. EBITDA and EPS in the first forecast quarter and the last two FY columns; `[]` without a parent |
| `facts.json` | `[{id: "F-n", label, value, unit, source: id, period, filed}]` (§10–11) |
| `note.json` | `{sections: [title], blocks: [str], slots: {key: text}, slot_facts: [{key, text, facts: [F-id]}], untraced: [str]}` |
| `exceptions.json` | `[{id: "X-nn", severity: info\|warning\|error, item}]` |
| `report.json` | `{ticker, as_of, version, parent, rating, price_target, sections: int, facts: int, untraced: int, exceptions: int, consistency: {check: bool}, parity: "ok/total", folder, timing: {seconds, steps: {step: {seconds, calls?, input_tokens?, output_tokens?}}}}` |

## 10–11. Reference ids, fact list, evidence and exception log

**Reference grammar:**

| Kind | Id | Resolves to |
|---|---|---|
| chunk | `UBER-2026Q2-ER-EX99-005` | chunk text |
| accession | `0001543151-26-000032` | filing metadata |
| XBRL | `xbrl:UBER:us-gaap:Revenues:<acc>:2026-04-01/2026-06-30` | value, unit (V3's end-only ids still resolve) |
| inline XBRL | `ixbrl:<acc>:<fact id>` | parses; not resolved (design §13.1) |
| derived quarter | `q:UBER:revenue:2025Q4` | value, derivation, input ids |
| KPI, guidance | `kpi:UBER:gross_bookings.mobility:2026Q2` · `guide:UBER:adj_ebitda:2026Q3` | the row (guide: low and high) + source chunk |
| price, macro | `px:UBER:2026-08-05` · `macro:DGS10:2026-08-05` | value |
| cross-check | `cc:tlc:HV0003:2026-06-01` · `cc:bts:airline_passengers:2026-06-01` | value |
| model | `model:<T>:<as_of>:<run>:<line>.<period>` or `…:<run>:<field>`; run = `base`, `scratch-<n>`, `val-<…>`, `var-<period>`, `v<n>` | model or valuation value (needs the model book; `v<n>` reads a version saved before the as-of) |
| evidence, exception | `E-12` · `X-03` | parse; not resolved (design §13.1) |

Data kinds resolve to `{value, unit, ticker}` in the row's own unit.

**Fact list** (one per figure the note may print):

```json
{"id": "F-2", "label": "12-month price target", "value": "<local only>", "unit": "USD",
 "source": "model:UBER:2026-08-06:val-base:price_target", "period": "", "filed": ""}
```

`text()` formats it (rounding, "2027E"/"2025A", signs, units); the writer must use it verbatim and mark it `{F-2}`. Facts print as the `E-n` rows of the log.

**Evidence and exception log:** `E-n` rows (label, value, source id) from the facts; `X-n` rows from every step (reconciliation gaps, missing inputs, clipped proposals, out-of-range multiples, EV exclusions, untraced numbers, failed consistency checks, run time). Fields today: id, severity (`info`, `warning`, `error`), item; the spec's fields are in design §13.1.

## 12. Outputs

**Report folder** `output/reports/<TICKER>/<as_of>/`: `note.md`, `note.pdf`, `workbook.xlsx` and the artifacts of §9, saved as a version (`UBER:v1`, `UBER:v2`, …) with its parent. `research_report` and `model:…:v<n>:…` ids read these files.

**Workbook:**

| Tab | Contents |
|---|---|
| Model | lines × quarters and FY; inputs blue with provenance, formulas black, links to assumptions green |
| Assumptions | name and value (the cells the Model tab's formulas link to); basis, confidence, variables affected and last updated are not built (design §13.1) |
| Summary | rating, price, price date, PT, return, market cap, EV, net debt, shares, WACC, parity |
| Scenarios | `scenario line`, year, value |
| Valuation | peer multiples, median, cost of capital, scenario PTs, DCF grid (the bridge and rating are on Summary, design §13.1) |
| DCF | base-case UFCF with live discount, terminal value, EV, terminal share and per-share formulas; the WACC × growth grid |
| Revisions | line, old, new, type, confidence |
| Model (prior) | the parent's values as saved, or "initial draft (no prior run)" |
| Checks | check, status, tested, failures, skipped |
| Log | id, kind (evidence/exception), label or severity, value or item, source |

**Review:** `tenk research approve TICKER:vN --by INITIALS` → `status: "reviewed by <INITIALS> on <date>"` (anything other than `draft` reads as reviewed).

## 13. Gold and results

**Gold files** (`evaluation/gold/`, JSONL; each row has `verified_by`):

| File | Row |
|---|---|
| `extraction-gold.jsonl` (337) | `{id, ticker, period, metric, segment, value, unit, source, block, quote, verified_by}` |
| `ask-gold.jsonl` (79) | `{id, split, category, kind, question, as_of, period, form, answer_type, expected_answer: [{value, unit, company}], tolerance, required_points, should_abstain, verified_by}` |
| `research-gold.jsonl` (72) | `{id, split, category, company, question, as_of, expected_refs: [regex], requires_assumptions, expected_tools, verified_by}` |

**Result folders:** `evaluation/results/backtest-<label>/` (`report.md`, `forecasts.csv`, `summary.json`, `llm_baseline.json`); Ask and Research runs under `output/generation/<label>/` with scores and judged reports.
