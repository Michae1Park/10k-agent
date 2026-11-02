# Architecture

The repository at a glance: its two versions, shared principles, layout and where each doc lives. To run it: [GETTING_STARTED.md](GETTING_STARTED.md).

## Versions

| Version | What | Corpus | Read |
|---|---|---|---|
| **V0–V3** | Cited answers about 10-Ks, built in stages (parsing and chunking, retrieval, generation, agent); kept as a regression check | 8 large caps × FY2023–2025 (`companies-v3.yaml`) | [V3 architecture](stages/V3_ARCHITECTURE.md), [stages/V0–V3](stages/) |
| **V4** | A junior sell-side research agent: Ask, Research and Report modes, every run at an as-of date | BKNG, EXPE, ABNB, TRIP, UBER, LYFT, DASH, CART from 2023 (`companies.yaml`) | [spec](V4_SPEC.md), [design doc](V4_TECHNICAL_DESIGN_DOC.md), [stages/V4_*](stages/), [results](V4_RESULTS.md) |

V4 reuses V0–V3's pipeline (parsing, chunking, retrieval, agent loop, claim check); scope: [D-038](DECISIONS.md#d-038).

## Design principles

| Area | Principle |
|---|---|
| Code | Pipeline code in `src/tenk_agent/`; experiments only run and score it |
| Settings | Every knob in `config.yaml`, per stage ([D-019](DECISIONS.md#d-019)); V4 adds `companies.yaml` (coverage) and `research.yaml` (forecast, valuation, proposer); experiments pin their own copy |
| Prompts | One Markdown file per task in `prompts/`, its output schema beside it |
| Storage | One SQLite file (tables, FTS5, sqlite-vec), reached only through `Store` ([D-005](DECISIONS.md#d-005)) |
| Models | One interface (`Model.complete`); open models on vLLM by default, Claude optional ([D-013](DECISIONS.md#d-013), [D-029](DECISIONS.md#d-029), [SERVING.md](SERVING.md)) |
| Evaluation | Gold sets first, evidence stored as verbatim quotes so re-chunking never invalidates them ([D-006](DECISIONS.md#d-006)) |

## Repository

| Path | Contents |
|---|---|
| `src/tenk_agent/` | Shared: `config`, `corpus` (the companies and years, from `companies.yaml`), `asof` (V4 as-of date), `store` (SQLite), `models`, `build` (the corpus build), `cli` |
| `src/tenk_agent/acquisition/` | `tenk fetch`: filings from SEC EDGAR (`edgar`, `filings`), with `raw/manifest.json`; V4: `companies` (verify), `market` (prices, macro, ERP), `crosscheck` (TLC, BTS), `insiders` (Form 4) |
| `src/tenk_agent/preprocess/` | Step 1 `blocks` (rules), step 2 `structure` (the LLM agent, assembly) and its `validation` rules; V4: `forms` (10-K, 10-Q, release) and `rules` (rule-based Items, [D-039](DECISIONS.md#d-039)) |
| `src/tenk_agent/chunking/` | `rule_based`, `parent_child`, `agentic`, and what they share (`common`) |
| `src/tenk_agent/retrieval/` | `index` (database, vectors), `decompose`, `search`, `embeddings` (embedder, reranker) |
| `src/tenk_agent/generation/` | `generate`, `answers`, `verify`, `calculator` |
| `src/tenk_agent/agent/` | `loop` (LangGraph), `tools` (tool groups and sets: V3, Ask, Research), `sec` (XBRL facts, EDGAR metadata, cached) |
| `src/tenk_agent/facts/` | V4: XBRL facts and quarters (`xbrl`, `quarters`), KPI and guidance extraction (`extract`, LLM; `rules`, ablation), `kpis.yaml`, `metrics.yaml` |
| `src/tenk_agent/research/` | V4: `refs` and `book` (citable ids), `datapack`, `model/` (DSL, graph, templates, checks), `propose`, `forecast`, `valuation`, `subagents` (events, debates), `note/`, `export/` (workbook, PDF), `pipeline` (Report mode) |
| `src/tenk_agent/evaluation/` | Gold sets (`gold`, `render`), retrieval metrics, number matching, the LLM judge, `scoring`, `report`; V4: `extraction`, `backtest`, `note_eval` |
| `src/tenk_agent/prompts/` | Every LLM prompt, one Markdown file per task, its output schema beside it: `structure.md` + `structure.schema.json`, `answer.md`, `agent.md`, `judge.md`; V4: `ask.md`, `research.md`, `extract.md`, `propose.md`, `events.md`, `debates.md`, `section.md` |
| `experiments/` | One folder per experiment (V0–V4): its runner and config; `V*.md` design and results (V4's are in `docs/`) |
| `evaluation/` | `gold/`: the reviewed gold question sets (JSONL, plus review PDFs); `results/<run>/`: each run's end-to-end report (`tenk report`). The code that loads, scores and reports is `src/tenk_agent/evaluation/` |
| `docs/` | This documentation |
| `scripts/` | `serve_vllm.sh` (open models) |
| `config.yaml` | Every setting for the CLI, per stage |
| `companies.yaml`, `companies-v3.yaml`, `research.yaml` | V4 coverage, V3 coverage, V4 forecast and valuation knobs |
| `data/`, `output/` | Filings, work files, databases; experiment outputs (generated, git-ignored) |
| `tests/` | Unit tests, no GPU or model |

## Documentation map

| You want | Read |
|---|---|
| What it is, a demo | [README](../README.md) |
| To install and run it | [GETTING_STARTED.md](GETTING_STARTED.md), [SERVING.md](SERVING.md) |
| How V0–V3 fit together | [V3 architecture](stages/V3_ARCHITECTURE.md) |
| How a stage or V4 block works, its knobs and data | [stages/](stages/) |
| Why it is this way, known issues | [DECISIONS.md](DECISIONS.md) |
| How well it works | [experiments/](../experiments/README.md) (V0–V3), [V4_RESULTS.md](V4_RESULTS.md) |
| The V4 model equations and parameters | [V4_FINANCIAL_MODELING.md](stages/V4_FINANCIAL_MODELING.md) |
| The V4 research agent | [spec](V4_SPEC.md), [design](V4_TECHNICAL_DESIGN_DOC.md) and its [per-block companions](stages/) (`V4_*.md`), [build log](V4_BUILD_LOG.md) |
| To contribute | [CONTRIBUTING.md](../CONTRIBUTING.md) |
