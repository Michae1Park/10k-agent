# Decision & issue log

Why the project looks the way it does: decisions, and the issues found along the way. Newest entries at the bottom of each list.

- **Don't rewrite history.** When a decision changes, add an entry and mark the old one `Superseded by D-xxx`. When an issue is fixed, change its status and say how. Entries that only restate another are folded into it; their IDs are listed under the entry that absorbed them.
- **Status.** Decisions: `Accepted` · `Proposed` (planned, not built) · `Superseded`. Issues: `Open` · `Workaround` · `Resolved`.
- Keep entries short; link files instead of repeating them.
- **Archived entries.** Decisions and issues that belong only to the archived V3–V6 (agent, verification, model comparison, fine-tuning, the old gold set) moved to [archive/docs/DECISIONS.md](../archive/docs/DECISIONS.md), keeping their IDs: an ID missing below is there or was folded into another entry (D-009 → D-001, D-011 → D-010, D-024 → D-035).

## Decisions

| ID | Date | Decision | Status |
|---|---|---|---|
| [D-001](#d-001) | 2026-09-26 | Fixed corpus: 8 companies × fiscal years 2023–2025, chosen for their different fiscal calendars; no prices, forecasts or advice | Accepted |
| [D-002](#d-002) | 2026-09-26 | Parse EDGAR HTML, not PDFs; the 10-K Item (section) is the unit of citation | Accepted |
| [D-003](#d-003) | 2026-09-26 | Tables are whole chunks, never split mid-row | Accepted |
| [D-004](#d-004) | 2026-09-26 | Fiscal years come from filing metadata (XBRL cover page), never from text | Accepted |
| [D-005](#d-005) | 2026-09-26 | One SQLite file: tables, FTS5, sqlite-vec, traces | Accepted |
| [D-006](#d-006) | 2026-09-26 | Gold set before retriever; evidence stored as verbatim quotes | Accepted |
| [D-007](#d-007) | 2026-09-26 | XBRL data only checks gold answers; no pipeline stage sees it | Accepted |
| [D-010](#d-010) | 2026-09-30 | Retrieval: local Qwen3-Embedding-0.6B + bge-reranker-v2-m3; one search per company and filing named in the question | Accepted |
| [D-013](#d-013) | 2026-09-30 | One model interface; Claude is the baseline, open models via vLLM; answer is JSON as text | Accepted |
| [D-019](#d-019) | 2026-10-01 | `config.yaml` holds every knob; experiments pin their own copy | Accepted |
| [D-023](#d-023) | 2026-09-26 | Public deployment: deferred; would move SQLite → Postgres + pgvector | Proposed |
| [D-029](#d-029) | 2026-10-13 | Open models by default: Qwen3.6-27B-FP8 generates, gpt-oss-20b is the fixed judge (second pass) | Accepted |
| [D-034](#d-034) | 2026-10-21 | The renewed V0–V2 replace the old pipeline in `src/`; V3–V6 archived, V7 deleted; experiments run on `src/` | Accepted |
| [D-035](#d-035) | 2026-10-22 | Docs by type: README, getting started, architecture + stage specs, decisions, contributing | Accepted |

## Issues

| ID | Date | Issue | Status |
|---|---|---|---|
| [I-001](#i-001) | 2026-09-30 | Python segfaults at random on the i9-14900K's P-cores | Workaround |
| [I-002](#i-002) | 2026-10-01 | GPU out of memory with vLLM and the embedder and reranker at once | Workaround |
| [I-003](#i-003) | 2026-10-01 | Qwen3.6-27B-FP8 on vLLM: KV-cache OOM, then a missing `nvcc` | Workaround |
| [I-010](#i-010) | 2026-09-30 | ROS's pytest plugins break test collection | Workaround |


### D-001
**8 companies × FY2023–2025, 24 10-Ks.** Fiscal years end in different months, so "revenue in 2024" is a real disambiguation problem. The range is fixed so the corpus doesn't change when a company files a newer 10-K.

| Company | Fiscal year ends | Tests |
|---|---|---|
| Apple | late September | FY ≠ calendar year; product/services mix |
| Microsoft | June 30 | FY label runs ahead of the calendar |
| Amazon · Meta · Tesla · Netflix | December 31 | calendar-year baseline |
| Alphabet | December 31 | filed as Alphabet, asked as "Google" |
| NVIDIA | late January | FY2025 is mostly calendar 2024: hardest case |

**Out of scope** (was D-009): stock prices, forecasts and investment advice. They don't test the core problem (finding, calculating, citing) and would pull in a much larger domain; the prompt declines them. 10-Q, web search and charts are possible later extensions.

### D-002
10-K HTML keeps table structure; PDFs lose it. A section is an "Item N. Title" line outside tables (skips tables of contents, cross-references, page headers). Item 8 always holds the financial statements: filers that keep Item 8 as a pointer (NVIDIA under Item 15, Netflix after the signatures) have them moved into Item 8.

### D-003
Splitting tables mid-row was the expected #1 cause of wrong numbers. Each table is one chunk with its caption and unit line; tables over `table_chars` split into row groups that repeat the header rows. [the V0 guide](stages/V0_PARSING_CHUNKING.md).

### D-004
`filings.fiscal_year` comes from each filing's XBRL `EntityPublicFloat` fact (`fy`). The agent maps calendar years through `period_end_date`; it never guesses from text.

### D-005
At ~6,300 chunks, brute-force vector search takes milliseconds, so a database server adds nothing. All access goes through `Store`, so moving to Postgres changes one module (D-023).

### D-006
The eval drives the design, so it comes first. Each gold source keeps a verbatim `evidence` quote; gold chunk IDs are found at eval time, so re-chunking never invalidates the gold set. [EVAL.md](../archive/docs/EVAL.md).

### D-007
Every number the agent reports must come from a document, so numeric questions stay a real retrieval test. XBRL company facts only cross-check gold answers (`tenk gold`). The archived V5 measured what happens otherwise ([D-021](../archive/docs/DECISIONS.md#d-021)).

### D-010
**Dense retrieval with a cross-encoder reranker, both local, and one search per company and filing.** The plan recommended hosted models (Voyage, Cohere); the L40S runs both in seconds, eval runs stay free and reproducible, and hosted models remain one config value away.

Measured on 22 retrieval-scorable `dev` questions (draft gold set, 34 questions at the time):

| Configuration | Recall@5 | Recall@10 | MRR |
|---|---|---|---|
| Keyword (FTS5 BM25) | 17.6% | 26.7% | 0.108 |
| Dense, bge-base-en-v1.5 | 27.7% | 47.1% | 0.294 |
| Dense, bge-base + bge-reranker-v2-m3 | 42.1% | 58.5% | 0.353 |
| Dense, Qwen3-Embedding-0.6B | 34.7% | 59.9% | 0.354 |
| **Dense, Qwen3-Embedding-0.6B + bge-reranker-v2-m3** | **43.6%** | **69.1%** | **0.400** |
| Hybrid, Qwen3-Embedding-0.6B + bge-reranker-v2-m3 | 46.6% | 65.3% | 0.367 |

The stronger reranker is the biggest single gain (+10 points). Hybrid won Recall@5 by one source on 22 questions but lost Recall@10 and MRR, so it was left off. The renewed V1 turned it on, fused by RRF per sub-question (D-034; [V1 results](../experiments/V1_RETRIEVAL.md)).

**Slot search** (was D-011): a comparison needs a chunk from every filing, and an unfiltered top 5 rarely has them. So each company and filing the question names gets its own search. Companies resolve through aliases ("Google" → GOOGL). A bare calendar year also matches the fiscal year that mostly covers it, from `period_end_date` (NVIDIA "2024" → FY2024 and FY2025). +6 points Recall@5 at equal models.

### D-013
Everything calls `Model.complete(messages, tools)`; providers translate one neutral message format. Claude (Sonnet 5) is the baseline; open models run behind an OpenAI-compatible server. The final answer is JSON written as text, not provider-specific structured output, so every model is held to the same format; one repair turn handles malformed JSON.

### D-019
Knobs were spread across module constants and environment variables. `config.yaml` now holds all of them, per stage; the stage specs' Knobs tables point at its keys. `TENK_CONFIG` selects another file, so each experiment pins its own copy instead of overriding single values.

### D-023
A public demo needs rate limiting, an API budget and Postgres + pgvector. Decide closer to shipping; until then everything is local, one process and one file.

### D-029
**Open models by default.** Everything runs on one 46 GB L40S with no API key. Qwen3.6-27B-FP8 generates: the best open model that fits, with I-003's workaround; Claude stays available when a key is set. **Judge:** gpt-oss-20b, fixed whatever generates and from another family, so no model grades itself. It can't share the GPU with the 27B, so judging is a second pass after the answers ([SERVING.md](SERVING.md)).

### D-034
The V0–V2 experiments (`experiments/`) rebuilt preprocessing (rules + an LLM structure agent), chunking (rule-based, parent-child, agentic), retrieval (sub-question hybrid search) and generation, and tested each against reviewed gold sets. Their core moved into `src/tenk_agent/` (`preprocess/`, `chunking/`, `retrieval/`, `generation/`, `evaluation/`, `prompts/`), replacing the old `parse`/`chunking`/`ingest`, `retrieval.Retriever` and `ask`; outputs are byte-identical to the experiment code. Each `experiments/vN/` keeps only its runner, its scoring, its config and test data. V3–V6 (agent, verification, model comparison, fine-tuning, eval harness, API) moved to `archive/` as references for rebuilding them on the new pipeline. V7 is deleted: its parent-child index and children search became V0's parent-child chunking and V1's search. Specs: `docs/stages/V0_PARSING_CHUNKING.md`, `V1_RETRIEVAL.md`, `V2_GENERATION.md`; experiment results stay in `experiments/V*.md`.

### D-035
Each doc has one job, so none mixes how-to with design. README: what it is, a demo, a quickstart. [GETTING_STARTED.md](GETTING_STARTED.md) and [SERVING.md](SERVING.md): install, build, run (how-to). [ARCHITECTURE.md](ARCHITECTURE.md) and `stages/V*.md`: scope, guarantees and each stage's data, knobs and design (reference). This log: why. [CONTRIBUTING.md](../CONTRIBUTING.md): checks and rules. Experiments keep their own `experiments/V*.md`. Supersedes D-024 (the earlier shape, modelled on [prompt-pick-place](https://github.com/Michae1Park/prompt-pick-place): README + one guide per stage + this log, with the PRD and plan folded in): the Commands and Things-to-try sections of the stage guides moved to GETTING_STARTED, and decisions and issues belonging only to the archived V3–V6 moved to [archive/docs/DECISIONS.md](../archive/docs/DECISIONS.md).

## Issue details

### I-001
9 of 16 runs crashed on CPUs 0–15, 0 of 16 on 16–31, in two separate Python installs: the known Raptor Lake instability. Microcode 0x133 is loaded; the BIOS (1401, 2023-09) predates Intel's fixes. **Workaround:** run heavy jobs with `taskset -c 16-31`; `tenk eval run --resume` continues a crashed run. **Fix:** BIOS update + Intel default profile; RMA if crashes persist.

### I-002
vLLM (35 GB) + the embedder and reranker (6 GB) leave too little headroom on 44 GB. **Workaround:** `scripts/serve_vllm.sh` defaults to 72% GPU memory; `retrieval.device: cpu` keeps the embedder and reranker on the CPU. [SERVING.md](SERVING.md).

### I-003
28.5 GB of weights left too little for the KV cache with CUDA graphs and the vision encoder profiled; an fp8 KV cache made FlashInfer JIT-compile kernels without a CUDA toolkit. **Workaround:** `--enforce-eager`, images/video off, bf16 KV cache, 32k context (preset `qwen3.6-27b-fp8`).

### I-010
`/opt/ros/jazzy` on `PYTHONPATH` loads ROS's pytest plugins. **Workaround:** `env -u PYTHONPATH uv run pytest`.
