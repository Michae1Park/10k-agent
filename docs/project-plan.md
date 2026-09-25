# 10k-agent — Project Plan

2026-09-26 · What and why: [PRD.md](PRD.md)

**This plan covers how 10k-agent is built: a thin stack of Next.js, FastAPI, one agent loop and four tools over a single SQLite file, with an evaluation harness driving every milestone.** Product goals, requirements, demo tasks and scope live in the PRD; section references like "PRD §6.6" point there.

## Corpus details

The PRD fixes the corpus at 8 companies × fiscal years 2023–2025 (PRD §6.1). The same fiscal-year label covers different months per company; these calendars drive the fiscal-year handling below:

| Company | Ticker | Fiscal year ends | What it tests |
| --- | --- | --- | --- |
| Apple | AAPL | Late September | Product/services revenue mix; FY ≠ calendar year |
| Microsoft | MSFT | June 30 | Segment reporting; FY label runs ahead of calendar |
| Amazon | AMZN | December 31 | Calendar-year baseline; AWS segment |
| Alphabet | GOOGL | December 31 | Filed as Alphabet, not Google — entity-name test |
| Meta | META | December 31 | Two segments; heavy capex narrative |
| NVIDIA | NVDA | Late January | FY2025 is mostly calendar 2024 — hardest FY case |
| Tesla | TSLA | December 31 | Competition and risk-factor questions |
| Netflix | NFLX | December 31 | Simpler filing; good control case |

## Architecture

```mermaid
flowchart TD
  UI[Next.js UI: Ask / Research] --> API[FastAPI]
  API --> ASK[Ask pipeline: retrieve, rerank, answer]
  API --> AGENT[Research agent loop]
  AGENT --> T1[search_filings]
  AGENT --> T2[get_filing_section]
  AGENT --> T3[calculator]
  AGENT --> T4[verify_citation]
  ASK --> DB[(SQLite: sqlite-vec + FTS5)]
  T1 --> DB
  T2 --> DB
  T4 --> DB
  ING[Ingestion: EDGAR to sections to chunks] --> DB
  API --> TR[Tracing]
  EVAL[Eval harness] --> API
  EVAL --> TR
```

### Ingestion

1. **Fetch** 10-K primary documents (HTML) from EDGAR's free APIs, with a declared User-Agent and ≤ 10 requests/s. Store raw HTML for reproducibility.
2. **Section-split** by 10-K Item (1 Business, 1A Risk Factors, 7 MD&A, 8 Financial Statements, …). Section is the unit of citation. A heading is an "Item N. Title" line outside multi-row tables, which excludes tables of contents, cross-references ("Item 5.02") and running page headers.
3. **Financial statements always live in Item 8.** Some filers keep Item 8 as a pointer and place the statements elsewhere (NVIDIA under Item 15, Netflix after the signatures). When Item 8 has no tables, the statements, from the index or the auditor's report on them, are moved into Item 8.
4. **Tables are first-class.** Each financial table becomes one chunk of `| cell | cell |` rows with its caption and unit line ("in millions"). Split cells are merged (`$` + `1,234`, `(3` + `)%`). Tables over ~1,800 tokens are split into row groups that repeat the header rows, never mid-row. Splitting tables mid-row is the #1 cause of wrong-number answers.
5. **Chunk prose** into ~600-token chunks (4 characters per token), never across section boundaries; a chunk starts with the previous chunk's last paragraph when that paragraph is short.
6. **Attach metadata** to every chunk (below). Embeddings are added in V1.

`tenk ingest` runs steps 2–6 over the downloaded filings (about 30 s, ~6,300 chunks); `tenk check` verifies the V0 exit criteria.

### Data model

| Table | Key fields |
| --- | --- |
| `companies` | ticker, cik, name; aliases (e.g. "Google", added in V3) |
| `filings` | accession_no, ticker, fiscal_year, period_end_date, filing_date, source_url |
| `sections` | id (e.g. `AAPL-FY2025-7`), accession_no, item, title |
| `chunks` | id (e.g. `AAPL-FY2025-7-012`), section_id, ticker, fiscal_year, item, seq, kind (prose / table), text, units, years_covered; FTS5 index; embedding (V1) |

`fiscal_year` and `period_end_date` are stored explicitly so the system never infers fiscal years from text; each filing's fiscal year comes from its XBRL cover-page data. `aliases` handles entity naming (PRD §6.6).

### Storage

One SQLite file (`data/tenk.db`) holds everything: relational tables above, keyword search via the built-in FTS5 (V0), and vectors via the sqlite-vec extension (V1). A single query can combine vector similarity, keyword match and metadata filters (company, fiscal year, item). This is what comparative questions need.

At ~24 filings (a few thousand chunks), brute-force vector search takes milliseconds, so a database server adds nothing. All retrieval code goes through a small storage interface (`search`, `get_section`, `get_chunk`), so moving to Postgres + pgvector later changes one module. That move happens only if the project is deployed publicly (see open questions).

### Ask pipeline

Retrieve top-k chunks (dense, optionally hybrid) → rerank → single LLM call that must answer only from the supplied chunks and return claims with chunk IDs.

### Tools (Research mode)

| Tool | Signature (sketch) | Returns |
| --- | --- | --- |
| `search_filings` | query, companies?, fiscal_years?, items?, k | Ranked chunks with chunk IDs + metadata |
| `get_filing_section` | company, fiscal_year, item | Full section text / tables |
| `calculator` | expression or op (pct_change, cagr, ratio) + named inputs | Result with the formula shown |
| `verify_citation` | claim value, chunk_id | Found / not found, matched span |

Metadata filters on `search_filings` are what make comparative questions tractable: the agent searches "R&D expense" filtered to Apple FY2024, rather than hoping the top-5 contains the right company-year.

### Agent loop

- Single agent, native tool use, capped at ~15 tool calls per question.
- System prompt rules: every number must come from a tool result; arithmetic goes through `calculator`; fiscal years resolve against `filings.fiscal_year`; state what's missing rather than guess.
- Each tool call is streamed to the UI as a step (SSE), which gives Research mode its live step list.
- Final answer is structured output (JSON: claims[], each with value, citation chunk_ids, status) rendered by the UI. This makes citation accuracy machine-checkable.

### Verification (V4)

A deterministic post-pass: for each numeric claim, confirm the value (with unit normalization) appears in its cited chunk; for each calculated claim, re-run the calculation. Failures are downgraded to *unverified* and shown, not hidden.

### Model layer

The agent loop, Ask pipeline and eval harness never call a provider SDK directly. They go through one small interface: `complete(messages, tools) → response` (text, tool calls, token counts). There are two implementations:

- **Anthropic:** Claude, the baseline.
- **OpenAI-compatible:** open-source models served by vLLM (or Ollama for local development). Both expose the same chat + tool-calling API.

Tool schemas are defined once and translated per provider. Model choice is a config value, so any eval run can target any model.

**The LLM judge is fixed.** It stays the same Claude model no matter which model is under test, so changing the agent's model never changes how answers are scored.

### Tracing

Every request is traced (tool calls, inputs/outputs, tokens, latency). The same traces feed the UI's trace view and the eval harness's agent and cost metrics.

## Evaluation

**The gold set and harness are built before the retriever, so the eval drives the design.** Metrics and targets are defined in PRD §8; this section covers how they're produced.

### Gold dataset

Stored as `eval/gold/questions.jsonl`. The record format, source and unit rules, verification procedure and dev/test split are defined in the [gold-set authoring guide](eval/gold-set-guide.md). Each record carries a verbatim `evidence` quote per source. Gold chunk IDs are resolved at evaluation time by matching those quotes against the current chunks, so re-chunking never invalidates the gold set. `tenk gold` reports, per question, whether every quote resolves and whether numeric answers match an XBRL fact.

**Target mix (~100):** 30 retrieval · 15 grounding (narrative) · 15 multi-document · 15 tool use · 10 agent workflow · 15 failure cases. Each failure case in PRD §6.6 gets at least two questions, tagged by `failure_mode`. Numeric answers are cross-checked against EDGAR's structured XBRL data to catch transcription errors in the gold set.

### Structured financial data (XBRL)

Companies tag financial-statement numbers with standard labels (e.g. `us-gaap:ResearchAndDevelopmentExpense`), and the SEC's "company facts" API returns every tagged value per company and period. **Through V4 this data is used only behind the scenes, to verify gold answers; the agent never sees it.** Every number the agent reports must come from the documents, so numeric questions remain a real retrieval test.

Limits worth knowing: product-level revenue (e.g. iPhone vs. Services) is tagged with dimensions the company facts API leaves out; tag names vary between companies (`Revenues` vs. `RevenueFromContractWithCustomerExcludingAssessedTax`); and "why" questions have no structured equivalent.

### Scoring

| Metric | How it's scored |
| --- | --- |
| Recall@5, MRR | Gold chunk IDs vs. retrieved IDs (deterministic) |
| Context relevance | LLM judge, spot-checked by hand |
| Correctness | Numeric: tolerance match after unit normalization. Narrative: LLM judge vs. rubric |
| Faithfulness | Every claim supported by cited chunks (LLM judge) |
| Citation accuracy | Cited chunk contains the claimed value / statement (deterministic for numbers) |
| Task completion | All required sub-answers present and correct |
| Tool selection / call correctness | Trace vs. `expected_tools`; arguments valid |
| Unnecessary calls | Calls beyond a reference trajectory |
| Unsupported-answer rate | Answered when `should_abstain` is true |
| Latency, tokens, cost | From traces |

Calibrate the LLM judge once: hand-grade ~30 answers and report judge-human agreement in the README.

## Demo task implementation

Before building, pin down the exact expected answer for each PRD §7 task from the filings, "The last three years" always means FY2023–FY2025. Each task becomes a gold question.

**Flagship demo (task 5) expected trajectory:**

1. Identify Apple's three 10-Ks in the corpus.
2. Retrieve the net-sales-by-category table (Item 7 / Notes).
3. Extract iPhone, Mac, iPad, Wearables, Services by year.
4. Calculate each category's share and YoY change.
5. Retrieve MD&A passages explaining changes.
6. Cross-check that extracted numbers appear in the cited passages.
7. Produce report: summary, table, cited management explanations, unverified items.

This trajectory is the reference for the "unnecessary calls" metric.

## Roadmap

**Six milestones plus optional experiments, each ending with a full eval run so every architectural addition has to earn its place in the results table.** V1–V4 match the PRD §9 releases. Durations assume part-time work and are rough.

| Milestone | Builds | Exit criteria | Est. |
| --- | --- | --- | --- |
| V0 — Data + gold set | EDGAR fetch, section split, table extraction, DB schema; first 30 gold questions | `tenk check` passes: 24 filings ingested; Items 1A, 7 and 8 present, with financial tables in Item 8; 30 verified questions whose evidence resolves to chunks | 1–2 wks |
| V1 — Search | Embedding + reranking retriever; CLI; eval harness (retrieval metrics) | Recall@5 and MRR reported on the gold set | 1 wk |
| V2 — RAG | Ask pipeline with cited answers; FastAPI; minimal UI; tracing | Tasks 1, 2 and 6 pass; answer + citation metrics reported | 1–2 wks |
| V3 — Agent | Research mode, 4 tools, streaming step list; gold set to ~100 | Tasks 3–5 complete end to end; agent metrics reported | 2 wks |
| V4 — Reliable agent | Verification pass, structured claims, failure-case fixes, LLM-judge calibration | Failure suite passes; unsupported-answer rate measured and reduced vs. V2 | 1–2 wks |
| V5 — Structured-data experiment (optional) | `get_financial_facts` tool over XBRL company facts, with a per-company tag mapping | Same gold set run document-only vs. document + structured data; difference reported by question category | 1 wk |
| V6 — Open-source models | Serve 2–3 open tool-calling models with vLLM; run the gold set through the same agent | Accuracy, tool-call correctness, latency and cost per question reported vs. the Claude baseline | 1–2 wks |
| V7 — Fine-tuning (only if V6 shows a gap) | LoRA fine-tune of the best open model on agent trajectories (training data below) | Gap to baseline reduced on `test`; cost per question still below baseline | 2 wks |
| Ship | README for both hiring managers and engineers: results table and demo video up front, then architecture and design decisions; public deployment TBD | Someone can run the eval with one command | 1 wk |

Rule for the whole roadmap: when a metric doesn't move, write that down too. An honest "hybrid search didn't help on this corpus" is portfolio material.

## Tech stack

**Recommended: Python + FastAPI, SQLite with sqlite-vec and FTS5, Claude as the baseline model with open-source models as the cost-efficient target, a hosted reranker, Langfuse for traces, Next.js for the UI.** Every choice is swappable; none should become the project.

| Layer | Recommendation | Why | Alternative |
| --- | --- | --- | --- |
| Language / API | Python 3.12, FastAPI, uv | Best ecosystem for parsing + eval | — |
| Parsing | BeautifulSoup / lxml on EDGAR HTML | 10-K HTML keeps table structure; PDFs lose it | `unstructured`, `edgartools` |
| Store | SQLite + sqlite-vec + FTS5 | One file, no server: metadata, vectors and keyword search with SQL filters on company/year; ample at this corpus size | Postgres + pgvector (if deployed publicly), LanceDB |
| Embeddings | A hosted embedding model (e.g. Voyage, OpenAI) | No infra; swap and re-run eval to compare | `bge` / `e5` locally |
| Reranker | Cohere Rerank or Voyage rerank | Largest cheap retrieval win | `bge-reranker` locally |
| LLM (baseline) | Claude Sonnet 5 for agent + answers through V4 | Strong multi-step tool use; sets the accuracy bar | — |
| LLM (target) | Open-source tool-calling models (e.g. Qwen, Llama, Mistral families; pick current ones at V6) | Compute and cost; can be fine-tuned | — |
| LLM judge | Claude Haiku 4.5, fixed across all runs | Cheap; consistent scoring across models | — |
| Serving | vLLM (OpenAI-compatible, tool calling); Ollama for local dev | Standard, fast, one API for all open models | TGI, llama.cpp |
| Fine-tuning | LoRA / QLoRA with Hugging Face TRL (or Unsloth) | Fits on a single GPU | Full fine-tune |
| Agent framework | Hand-written loop over the model layer (~200 lines) | Shows you understand the loop; easier to trace, test and swap models | LangGraph, Claude Agent SDK |
| Tracing | Langfuse Cloud (self-hosting needs its own Postgres) | Traces + datasets + scores in one place | Phoenix, OpenTelemetry |
| Frontend | Next.js + streaming (SSE) | Step-by-step Research view | Streamlit for V1–V2 |
| Deploy | Local: one process + one SQLite file. Public: TBD (would move storage to managed Postgres) | Nothing to operate until deployment is decided | — |

Keep the eval harness framework-free (pytest-style runner + JSONL results), so the numbers don't depend on any vendor tool.

## Risks and open questions

**The biggest risk is table extraction quality; the biggest open decision is whether to deploy publicly.**

| Risk | Impact | Mitigation |
| --- | --- | --- |
| 10-K HTML tables parse badly (merged cells, footnotes, units in captions) | Wrong numbers everywhere downstream | Spend V0 on it; add a table-extraction check to the eval |
| Gold-set building takes longer than code | Eval stays thin | Start in V0; cross-check numbers against XBRL; 30 before V1 |
| LLM-judge scores are noisy | Metrics not credible | Deterministic scoring for numbers; calibrate judge on 30 hand grades |
| Eval runs get expensive | Fewer iterations | Cache retrieval; Haiku for judging; run a 30-question smoke set per change |
| Scope creep (10-Q, web search, charts) | MVP never ships | Hold the PRD §10 "Later" column until V4 ships |

### Hardware (V6–V7)

One NVIDIA L40S: 48 GB card, ~44 GB usable, Ada architecture with native FP8. Everything in V6–V7 must fit on this single card:

| Task | Comfortable | Possible, tight | Not practical |
| --- | --- | --- | --- |
| Serving (vLLM) | ≤ 14B in BF16 | ~32B in FP8 or 4-bit (AWQ/GPTQ) | 70B+ |
| LoRA fine-tuning | ≤ 8B in BF16 | — | — |
| QLoRA fine-tuning | ≤ 14B | ~32B with gradient checkpointing and short sequences | 70B |

Agent trajectories are long: retrieved chunks and tool results can reach 20–40k tokens. The KV cache (serving) and activations (training) grow with context length, so budget memory for the longest real trajectory, not the model weights alone. Measure it in V3 traces.

Starting point for V6: one ~7–8B, one ~14B and one ~30B-class open model, so the results show how accuracy scales with size against cost.

### Fine-tuning data (V7)

**Training data comes from 10-Ks outside the eval corpus:** other companies, and other years of the 8 corpus companies. The same pipeline (ingestion, tools, agent) runs over this separate training corpus, so the model learns the task without seeing any eval filing.

- **Other companies:** any 10-K filer. Prefer a mix of fiscal calendars and industries, so the fiscal-year and unit-handling skills generalize.
- **Other years of the corpus companies:** only filings for **FY2022 and earlier**. A 10-K repeats the prior two years' figures, so FY2026+ filings contain FY2024–FY2025 values, which are eval answers. They're excluded.
- **Narrative text repeats across years.** Risk factors change little from one year to the next, so narrative training questions should come mostly from other companies.
- **Numeric labels come cheaply from XBRL.** For training questions (not gold questions), the SEC's company facts data supplies verified answers for standard line items automatically, so thousands of numeric questions can be generated without hand labeling.
- **Trajectories come from open models, not Claude.** Anthropic's Usage Policy (effective 2025-09-15) prohibits "utilization of inputs and outputs to train an AI model (e.g., 'model scraping' or 'model distillation') without prior authorization from Anthropic." That covers Claude-written training questions too. Generate trajectories by running the open model itself and keeping verified successes (rejection sampling), optionally bootstrapped by a larger open teacher model whose license permits it. Claude remains the baseline and the eval judge, neither of which involves training on its outputs.
- **Keep only correct trajectories.** A run becomes training data only if its final answer passes the same deterministic checks as the eval (numbers, citations, abstention).
- **Never train on gold questions or eval filings,** and deduplicate training questions against `dev` and `test`.

### Open questions

- [ ] Deploy a public demo, or local-only with a recorded video? A public demo needs rate limiting, an API budget and a move from SQLite to Postgres. Deferred until closer to Ship.
