# Architecture

What the system does, what it guarantees, and how it is put together. To run it: [GETTING_STARTED.md](GETTING_STARTED.md).

## Scope and guarantees

Questions about the annual reports (10-Ks) of a fixed corpus, answered from the filings alone.

| Guarantee | How it is kept |
|---|---|
| Every number comes from a cited passage, never from the model's memory | The prompt requires it; each claim is then checked against the chunks (or the XBRL facts) it cites ([generation spec](stages/V2_GENERATION.md#how-it-works), [D-036](DECISIONS.md#d-036)) |
| Fiscal years come from filing metadata, never from text | `filings.fiscal_year` from the XBRL cover page ([D-004](DECISIONS.md#d-004)) |
| Unverifiable claims are shown as unverified, not hidden | Claim status `unverified`, with the reason |
| Questions outside the corpus are declined | `abstained: true` and a scope notice |
| No stock prices, forecasts or investment advice | Declined by the prompt ([D-001](DECISIONS.md#d-001)) |

**Corpus.** Apple, Microsoft, Amazon, Alphabet, Meta, NVIDIA, Tesla, Netflix × fiscal years 2023–2025 (24 10-Ks), chosen because their fiscal years end in different months: "NVIDIA's revenue in 2024" is mostly its fiscal 2025 ([D-001](DECISIONS.md#d-001)). Built and tested so far: the FY2025 filings of AAPL, AMZN, MSFT and NVDA.

**Out of scope.** Prices, forecasts, advice ([D-001](DECISIONS.md#d-001)); a public deployment ([D-023](DECISIONS.md#d-023)); verification-by-tools, model comparison and fine-tuning.

## Pipeline

<p align="center">
  <img src="img/pipeline.svg" alt="Pipeline. Build, once per corpus: EDGAR 10-K HTML, preprocess (rules and an LLM structure agent), parent-child chunking, one SQLite file with chunks, FTS5 and vectors. SEC XBRL facts and EDGAR submissions cached as JSON. Ask, per question: the agent, a LangGraph loop, calls tools until it can answer: search_filings (hybrid search) and get_section read the index, calculator, and xbrl_facts and edgar_filings read the SEC cache; it answers with JSON claims citing IDs; claim check (code only); cited answer" width="100%">
</p>

The system was built in stages (the V0–V3 experiments), each tested on its own before the next was built. The specs and experiment reports keep those names.

| Component | Does | Spec | Experiment and results |
|---|---|---|---|
| **Parsing and chunking** | 10-K HTML → blocks (rules) → labels (LLM structure agent, validated and repaired) → one structured document → chunks, three ways | [spec](stages/V0_PARSING_CHUNKING.md) | [V0](../experiments/V0_PARSING_CHUNKING.md): rule-based and parent-child level (~93% exact heading paths, every risk factor on its own) |
| **Retrieval (`search_filings`)** | Sub-questions per company and filing → keyword + dense → RRF → rerank → round-robin. No LLM | [spec](stages/V1_RETRIEVAL.md) | [V1](../experiments/V1_RETRIEVAL.md): parent-child best, Recall@5 87.3%, MRR 0.758 |
| **Generation and claim check** | JSON answer with claims citing chunk IDs → each claim checked | [spec](stages/V2_GENERATION.md) | [V2](../experiments/V2_GENERATION.md): 21 / 30 number questions right, 8 / 8 declines, every numeric claim grounded (one call over the top 8 chunks, no tools) |
| **Agent** | The model drives: search, read sections, calculate, XBRL facts and EDGAR metadata (LangGraph) → JSON answer and claim check | [spec](stages/V3_AGENT.md) | [V3](../experiments/V3_AGENT.md): 90.0% (10-K tools) and 91.5% (+ SEC data) of number and decline questions right vs V2's 71.7% |

Each stage spec covers: data, knobs, outputs, how it works, gotchas.

### Zoom in: the search_filings tool

The [pipeline](#pipeline)'s **Preprocess**, **Chunk** and **SQLite** blocks and its **search_filings** tool, opened up (dashed frames carry the pipeline's block names). The build runs once; the tool runs on every call the agent makes and returns chunks the agent can cite. The tool is the V1 retrieval, with one addition: companies and fiscal years the agent passes replace those found in the query, and a search that names no company returns an error asking for one instead of searching the whole corpus.

<p align="center">
  <img src="img/retrieval.svg" alt="The build and the search_filings tool. Build, once per corpus: 10-K HTML to blocks by rules, a structure agent labels them and code validates and repairs, assemble into one section per Item, parent-child chunking, load and embed into one SQLite file with FTS5 and vectors. search_filings, per call, no LLM: companies and fiscal years from the arguments or else the query (none is an error), one sub-question per company and filing, each a keyword and a dense search, RRF, children swapped for parents, cross-encoder rerank, then a round-robin merge to k chunks for the agent" width="100%">
</p>

### Zoom in: claim check

The pipeline's **Claim check** block, opened up. Each claim the model returns is checked on its own, with code only (`generation/verify.py`, run inside `generate()`):

<p align="center">
  <img src="img/claim-check.svg" alt="Claim check: a claim with a calculation reruns it and finds every input in a cited chunk (calculated); a claim with a number must be found in a cited chunk of the right company (verified); a claim without a number needs valid chunk IDs (cited); any failed check is unverified" width="100%">
</p>

Evaluation does not run this check; it reads the statuses (below). An unknown chunk ID is added to the reason whatever the status. Unverified claims are shown to the user with the reason, not hidden.

## Evaluation

Evaluation never runs the pipeline, including the claim check. It takes the pipeline's output, the generated answer with its claim statuses and the chunks the model was shown, and grades it against the gold set. The only LLM in it is the judge, and only for text answers.

<p align="center">
  <img src="img/evaluation.svg" alt="Evaluation flow: gold JSONL gives gold chunks; the generated answer arrives with the claim statuses the pipeline already gave it; grade() picks the check by answer type: decline questions are correct if the model declined, number questions if every expected value is stated within tolerance, text questions are unjudged until the LLM judge scores them against required points; summarize() aggregates per context, results are stored and report.py recomputes Recall@k and MRR to write the report" width="100%">
</p>

| Step | Code | LLM? |
|---|---|---|
| Gold chunks | `gold.resolve_evidence`, `retrieval_metrics.gold_chunks` | No |
| Number questions, declines | `scoring.grade`, `answer_metrics` | No |
| Text questions | `judge.py`, then `scoring.correct` again (`experiments/v2/judge.py`) | **Yes** |
| Aggregate and report | `scoring.summarize`, `report.build_report` | No |

`correct` does not depend on the claim statuses. A number that is in the answer text counts even if its claim is `unverified`; the claim statuses are reported as the grounded count.

## Design principles

| Area | Principle |
|---|---|
| Code | Pipeline code in `src/tenk_agent/`; experiments only run and score it |
| Settings | Every knob in `config.yaml`, per stage ([D-019](DECISIONS.md#d-019)); experiments pin their own copy |
| Prompts | One Markdown file per task in `prompts/`, its output schema beside it |
| Storage | One SQLite file (tables, FTS5, sqlite-vec), reached only through `Store` ([D-005](DECISIONS.md#d-005)) |
| Models | One interface (`Model.complete`); open models on vLLM by default, Claude optional ([D-013](DECISIONS.md#d-013), [D-029](DECISIONS.md#d-029), [SERVING.md](SERVING.md)) |
| Evaluation | Gold sets first, evidence stored as verbatim quotes so re-chunking never invalidates them ([D-006](DECISIONS.md#d-006)) |

## Repository

| Path | Contents |
|---|---|
| `src/tenk_agent/` | Shared: `config`, `corpus` (the companies and years), `store` (SQLite), `models`, `build` (the corpus build), `cli` |
| `src/tenk_agent/acquisition/` | `tenk fetch`: the corpus 10-Ks from SEC EDGAR (`edgar`), with `raw/manifest.json` |
| `src/tenk_agent/preprocess/` | Step 1 `blocks` (rules), step 2 `structure` (the LLM agent, assembly) and its `validation` rules |
| `src/tenk_agent/chunking/` | `rule_based`, `parent_child`, `agentic`, and what they share (`common`) |
| `src/tenk_agent/retrieval/` | `index` (database, vectors), `decompose`, `search`, `embeddings` (embedder, reranker) |
| `src/tenk_agent/generation/` | `generate`, `answers`, `verify`, `calculator` |
| `src/tenk_agent/agent/` | `loop` (LangGraph), `tools`, `sec` (XBRL facts, EDGAR metadata, cached) |
| `src/tenk_agent/evaluation/` | Gold sets (`gold`, `render`), retrieval metrics, number matching, the LLM judge, `scoring`, `report` |
| `src/tenk_agent/prompts/` | Every LLM prompt, one Markdown file per task, its output schema beside it: `structure.md` + `structure.schema.json`, `answer.md`, `agent.md`, `judge.md` |
| `experiments/` | One folder per experiment (V0–V3): its runner and config; `V*.md` design and results |
| `evaluation/` | `gold/`: the reviewed gold question sets (JSONL, plus review PDFs); `results/<run>/`: each run's end-to-end report (`tenk report`). The code that loads, scores and reports is `src/tenk_agent/evaluation/` |
| `docs/` | This documentation |
| `scripts/` | `serve_vllm.sh` (open models) |
| `config.yaml` | Every setting for the CLI, per stage |
| `data/`, `output/` | Filings, work files, databases; experiment outputs (generated, git-ignored) |
| `tests/` | Unit tests, no GPU or model |

## Documentation map

| You want | Read |
|---|---|
| What it is, a demo | [README](../README.md) |
| To install and run it | [GETTING_STARTED.md](GETTING_STARTED.md), [SERVING.md](SERVING.md) |
| How a stage works, its knobs and data | [stages/](stages/) |
| Why it is this way, known issues | [DECISIONS.md](DECISIONS.md) |
| How well it works | [experiments/](../experiments/README.md) |
| To contribute | [CONTRIBUTING.md](../CONTRIBUTING.md) |
