<p align="center">
  <h1 align="center">Equity Research Agent</h1>
  <p align="center">
    <strong>An AI financial analyst that researches equities, builds forecasts, and writes earnings notes</strong>
  </p>
  <p align="center">
    <a href="https://github.com/Michae1Park/equity-research-agent/actions/workflows/ci.yml"><img src="https://github.com/Michae1Park/equity-research-agent/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
    <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python 3.12+"></a>
    <a href="https://github.com/astral-sh/uv"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json" alt="uv"></a>
  </p>
</p>

---

<p align="center">
  <b>Author:</b> <a href="https://michae1park.github.io">Michael Park</a>
</p>

---

## Overview

An autonomous equity research agent covering eight US travel and mobility companies (**BKNG, EXPE, ABNB, TRIP, UBER, LYFT, DASH, CART**). Operating under strict as-of date constraints to eliminate look-ahead bias, it grounds every output in deterministic financial models and verified SEC data to produce audit-ready earnings notes.

<p align="center"><b>Research Mode</b></p>

<p align="center">
  <img src="docs/img/demo-research.gif" alt="Research mode: the agent forecasts Uber's Q3 2026 revenue from the model, compares it with gross bookings guidance, and every claim is verified" width="820">
</p>

<p align="center"><b>Ask Mode</b></p>

<p align="center">
  <img src="docs/img/demo-ask.gif" alt="Ask mode: a cited answer on Uber's Q2 2026 gross bookings growth with a source opened in the drawer, then a decline because Booking does not disclose hotel occupancy" width="820">
</p>

<p align="center"><b>Investigate Mode</b></p>

<p align="center">
  <img src="docs/img/demo-investigate.gif" alt="Investigate mode: Uber vs Lyft gross bookings growth and Q3 2026 forecasts, split into an Ask and a Research sub-run" width="820">
</p>

<p align="center"><b>Report</b></p>

<p align="center">
  <img src="docs/img/demo-report.gif" alt="Reports page: the UBER Q2 2026 earnings-update note scrolled from the executive summary through results, debates and the financial summary" width="820">
</p>

<p align="center"><sub><i>Report demo: price-derived figures (rating, price target, market cap, multiples, WACC, DCF values) are made up, because the real ones come from licensed data. Filing-derived figures (revenue, EBITDA, gross bookings) are real. Run the report locally with your own keys for the true numbers.</i></sub></p>

### What It Does

| Mode | Input | Execution & output |
|---|---|---|
| **Ask** | Factual question<br>+ as-of | • Retrieves filed KPIs and passages<br>• Returns a concise, citation-verified answer (or declines if unsupported) |
| **Research** | Quantitative question<br>+ as-of | • Runs the driver-based forecast and valuation engine<br>• Returns computed figures, model assumptions and cell ids |
| **Investigate** | Multi-part question<br>+ as-of | • Splits the question into Ask and Research sub-runs<br>• Synthesizes them into a single claim-checked answer |
| **Report** | Ticker<br>+ as-of | • Runs an 11-step pipeline<br>• Returns a 10-section earnings note (Markdown and PDF), a live-formula Excel workbook and an evidence and exception log |

<details>
<summary><b>Execution trace (Ask mode example)</b></summary>

**Query:** `"What was Uber's gross bookings growth in Q2 2026 and what drove it?"` (`as_of = 2026-11-12`)

1. **KPI lookup:** Retrieves Q2 2026 and Q2 2025 gross bookings from structured KPI tables tied to Uber's earnings release, ignoring anything filed on or after `2026-11-12`.
2. **Deterministic math:** Computes year-over-year growth with the calculator tool instead of letting the model do the arithmetic.
3. **Qualitative retrieval:** Runs hybrid search (keyword + dense, fused with RRF, then reranked) over the Q2 2026 release for the drivers (trips, monthly active consumers, segments).
4. **Structured synthesis:** Writes a JSON answer in which every numeric claim cites a `kpi_id` or `chunk_id`.
5. **Claim verification:** Checks each claimed figure against its cited source and tags it `verified`, `cited` or `unverified`, showing unverified claims in the UI instead of hiding them.

</details>

<details>
<summary><b>Results</b> (Ask, KPI extraction, forecast, draft notes)</summary>

[Full results and limits](docs/V4_RESULTS.md)

| Test | Evaluation set | Result |
|---|---|---|
| **Ask accuracy** | 79 questions | 89.9% correct and verified; 100% of cited ids resolve |
| **KPI extraction** | 337 hand-checked labels | 98.5% exact; 100% on revenue, EPS and the main KPI |
| **Next-quarter forecast** | 90 walk-forward points | Revenue MAPE 3.14% vs 3.77% for seasonal naive (not statistically significant) |
| **Draft notes** | All eight companies | 10 sections; 100% of figures traced; all 474 seeded errors caught |

</details>

---

## Architecture

<p align="center">
  <img src="docs/img/readme-architecture.svg" alt="Architecture. Sources (SEC EDGAR, market and macro, cross-checks) are built offline into text chunks, facts, extracted KPIs and guidance, and market series in one SQLite store read only through an AsOf filter. The web app and CLI enter four modes: Ask, Research, Investigate (which plans Ask and Research sub-runs) and Report. Ask and Research run the agent loop over a toolbox that reaches the store and the code-only model engine; answers pass a claim check. The Report pipeline runs steps S1 to S11 and writes a draft note, workbook and log, saving a model version back to the store." width="100%">
</p>

### Architectural Invariants

1. **Point-in-time isolation:** every read of the SQLite store goes through the AsOf filter, so nothing filed on or after the as-of date is visible to text search, XBRL facts or market series.
2. **Deterministic computation:** the model never computes ratios, growth rates or valuation multiples itself. All financial math runs in Python, with integrity checks on the history (balance, quarters sum, Q4 ties, segments).
3. **Claim auditing:** every number must cite a chunk, KPI or model cell. A claim that fails the check is shown as unverified, never silently dropped.
4. **Human sign-off:** ratings and price targets stay drafts until a human approves them.

<details>
<summary><b>System layers: what each part does</b></summary>

| Subsystem | Layer | What it does |
|---|---|---|
| **Data plane** (offline build) | **Acquisition** | Fetches 10-Ks, 10-Qs, earnings releases (8-K Ex. 99.1), proxies, Form 4s, XBRL company facts, prices, FRED/ALFRED macro, the equity risk premium, BTS airline passengers and NYC TLC trips into a local cache |
| | **Store** | One SQLite file holding chunks, FTS5, vectors, XBRL facts, extracted KPIs and guidance, and market series, read only through the **AsOf** filter |
| **Reasoning and compute** (runtime) | **Retrieval** | Keyword + dense search, fused with RRF, then reranked, over parent-child chunks of every filing |
| | **Model engine** | A history model, a driver-based forecast anchored to guidance, and a DCF plus peer-multiple valuation |
| | **Agent and planner** | A tool-calling loop (search, KPIs, facts, calculator, model and valuation tools) that writes a JSON answer with cited claims. The Investigate planner splits a multi-part question into Ask and Research sub-runs, can add a follow-up, and claim-checks the combined answer again |
| **Delivery** | **Report pipeline** | Eleven fixed steps (S1–S11), with the LLM only for events, forecast proposals, debates and the write-up. Exports a note, a workbook whose formulas recalculate to Python's values, and a saved model version |
| | **Web app** | FastAPI with server-sent events and a React UI: chat with a live trace and source drawer, plus report generation and review |

</details>

<details>
<summary><b>Iterative build progression: V0 to V4</b></summary>

| Version | Core addition | Result |
|---|---|---|
| V0 · Parsing and chunking | 10-K HTML → structured document → parent-child chunks | [**~93%** exact heading paths](experiments/V0_PARSING_CHUNKING.md) |
| V1 · Retrieval | Hybrid search → RRF → rerank | [Recall@5 **87.3%**](experiments/V1_RETRIEVAL.md) |
| V2 · Generation | JSON claims citing chunk ids, each claim checked | [**21 / 30** numeric questions right, **8 / 8** declines](experiments/V2_GENERATION.md) |
| V3 · Agent | Tool use (search, calculator, XBRL, EDGAR) | [**87–89%**, up from V2's 66.7%](experiments/V3_AGENT.md) |
| **V4 · Research agent** | As-of store, KPI extraction, forecast and valuation model, Research / Investigate / Report modes, web app | [V4 results](docs/V4_RESULTS.md) |

</details>

Design: [spec](docs/V4_SPEC.md), [technical design](docs/V4_TECHNICAL_DESIGN_DOC.md), [architecture and doc map](docs/ARCHITECTURE.md).

---

## Quickstart

**Prerequisites:** Linux or macOS, Python 3.12 + [uv](https://docs.astral.sh/uv/), a ~40 GB GPU for the default 27B model, and your own free SEC User-Agent, Tiingo and FRED keys ([all requirements](docs/GETTING_STARTED.md#requirements)).

### 1. Install and configure

```bash
git clone https://github.com/Michae1Park/equity-research-agent.git && cd equity-research-agent
uv sync --extra local --extra chunking
source .venv/bin/activate
era setup                                   # your SEC, Tiingo and FRED credentials (gitignored)
export ERA_COMPANIES=companies.yaml         # the eight-company corpus
```

### 2. Start the model server (terminal 1)

```bash
scripts/serve_vllm.sh qwen3.6-27b-fp8       # other GPU sizes: docs/SERVING.md
```

### 3. Build the corpus (terminal 2)

```bash
era fetch && era fetch --market             # filings, XBRL facts, market and macro series
era build --stage chunk --stage load --stage facts --stage market --stage embed
python experiments/v4/extract_pilot.py --releases all   # KPIs and guidance
era build --stage kpis
```

### 4. Run it

**CLI** (needs the model server from step 2):

```bash
era research report UBER --as-of 2026-08-06    # draft note, workbook and logs → output/reports/
era investigate "Compare Uber and Lyft Q2 2026 gross bookings growth and Q3 forecasts" --as-of 2026-08-06
```

**Web UI** (Ask, Research, Investigate, and Reports — requires the corpus from step 3; stop any running vLLM first):

```bash
scripts/launch.sh            # local: vLLM, API and UI together, UI at http://localhost:5173 (replaces step 2)
docker compose up --build    # containers: vLLM, API and UI together, UI at http://localhost:8000
```

<details>
<summary><b>Once open: what you can do</b></summary>

- **Chat:** Pick a mode, set the as-of date, and ask (e.g., Ask mode with as-of `2026-11-12`: *"What was Uber's gross bookings growth in Q2 2026 and what drove it?"*). Every answer renders a live execution trace and an interactive source drawer.
- **Reports:** Generate a draft earnings note for any covered ticker and review the output.

</details>

Full steps, evaluation suites, and troubleshooting: [Getting started](docs/GETTING_STARTED.md). Serving presets: [SERVING.md](docs/SERVING.md).
