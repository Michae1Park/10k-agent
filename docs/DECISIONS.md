# Decision & issue log

Why the project looks the way it does: decisions, and the issues found along the way. Newest entries at the bottom of each list.

- **Don't rewrite history.** When a decision changes, add an entry and mark the old one `Superseded by D-xxx`. When an issue is fixed, change its status and say how.
- **Status.** Decisions: `Accepted` · `Proposed` (planned, not built) · `Superseded`. Issues: `Open` · `Workaround` · `Resolved`.
- Keep entries short; link files instead of repeating them.

## Decisions

| ID | Date | Decision | Status |
|---|---|---|---|
| [D-001](#d-001) | 2026-09-26 | Fixed corpus: 8 companies × fiscal years 2023–2025, chosen for their different fiscal calendars | Accepted |
| [D-002](#d-002) | 2026-09-26 | Parse EDGAR HTML, not PDFs; the 10-K Item (section) is the unit of citation | Accepted |
| [D-003](#d-003) | 2026-09-26 | Tables are whole chunks, never split mid-row | Accepted |
| [D-004](#d-004) | 2026-09-26 | Fiscal years come from filing metadata (XBRL cover page), never from text | Accepted |
| [D-005](#d-005) | 2026-09-26 | One SQLite file: tables, FTS5, sqlite-vec, traces | Accepted |
| [D-006](#d-006) | 2026-09-26 | Gold set before retriever; evidence stored as verbatim quotes | Accepted |
| [D-007](#d-007) | 2026-09-26 | XBRL data only checks gold answers; the agent never sees it (through V4) | Accepted |
| [D-008](#d-008) | 2026-09-26 | Releases V1 search → V2 RAG → V3 agent → V4 reliable agent, each measured | Accepted |
| [D-009](#d-009) | 2026-09-26 | Out of scope: stock prices, forecasts, investment advice. Later: 10-Q, web search, charts | Accepted |
| [D-010](#d-010) | 2026-09-30 | Retrieval: local Qwen3-Embedding-0.6B + bge-reranker-v2-m3, dense (hybrid off) | Accepted |
| [D-011](#d-011) | 2026-09-30 | Slot search: one search per company and filing named in the question | Accepted |
| [D-012](#d-012) | 2026-09-30 | Hand-written agent loop, four tools, 15 tool calls max | Accepted |
| [D-013](#d-013) | 2026-09-30 | One model interface; Claude is the baseline, open models via vLLM; answer is JSON as text | Accepted |
| [D-014](#d-014) | 2026-09-30 | The LLM judge is fixed (Claude Haiku 4.5); numbers are scored without it | Accepted |
| [D-015](#d-015) | 2026-09-30 | Verification labels every claim; Research mode gets one repair round | Accepted |
| [D-016](#d-016) | 2026-09-30 | Over-budget tool calls count as unnecessary, not incorrect | Accepted |
| [D-017](#d-017) | 2026-10-01 | Rates are over completed questions; errors are reported separately | Accepted |
| [D-018](#d-018) | 2026-10-01 | Numbers match to the precision they're written with, not within a % | Accepted |
| [D-019](#d-019) | 2026-10-01 | `config.yaml` holds every knob; `TENK_*` variables override the model choices | Accepted |
| [D-020](#d-020) | 2026-10-01 | Self-hosted cost = latency × GPU hourly rate ($0.86/h, L40S) | Accepted |
| [D-021](#d-021) | 2026-10-01 | V5: structured XBRL data stays out of the product | Accepted |
| [D-022](#d-022) | 2026-09-26 | V7 fine-tuning: data from open models only, outside the eval corpus | Proposed |
| [D-023](#d-023) | 2026-09-26 | Public deployment: deferred; would move SQLite → Postgres + pgvector | Proposed |
| [D-024](#d-024) | 2026-10-01 | Docs: README + one guide per stage + this log (PRD and plan folded in) | Accepted |

### D-001
**8 companies × FY2023–2025, 24 10-Ks.** Fiscal years end in different months, so "revenue in 2024" is a real disambiguation problem. The range is fixed so the corpus doesn't change when a company files a newer 10-K.

| Company | Fiscal year ends | Tests |
|---|---|---|
| Apple | late September | FY ≠ calendar year; product/services mix |
| Microsoft | June 30 | FY label runs ahead of the calendar |
| Amazon · Meta · Tesla · Netflix | December 31 | calendar-year baseline |
| Alphabet | December 31 | filed as Alphabet, asked as "Google" |
| NVIDIA | late January | FY2025 is mostly calendar 2024: hardest case |

### D-002
10-K HTML keeps table structure; PDFs lose it. A section is an "Item N. Title" line outside tables (skips tables of contents, cross-references, page headers). Item 8 always holds the financial statements: filers that keep Item 8 as a pointer (NVIDIA under Item 15, Netflix after the signatures) have them moved into Item 8.

### D-003
Splitting tables mid-row was the expected #1 cause of wrong numbers. Each table is one chunk with its caption and unit line; tables over `table_chars` split into row groups that repeat the header rows. [STAGE1_INGEST.md](STAGE1_INGEST.md).

### D-004
`filings.fiscal_year` comes from each filing's XBRL `EntityPublicFloat` fact (`fy`). The agent maps calendar years through `period_end_date`; it never guesses from text.

### D-005
At ~6,300 chunks, brute-force vector search takes milliseconds, so a database server adds nothing. All access goes through `Store`, so moving to Postgres changes one module (D-023).

### D-006
The eval drives the design, so it comes first. Each gold source keeps a verbatim `evidence` quote; gold chunk IDs are found at eval time, so re-chunking never invalidates the gold set. [EVAL.md](EVAL.md).

### D-007
Every number the agent reports must come from a document, so numeric questions stay a real retrieval test. XBRL company facts only cross-check gold answers (`tenk gold`). V5 measured what happens otherwise (D-021).

### D-008
The agent isn't built first. Each release adds one capability and is evaluated against the previous one; the README's results table has one column per release.

### D-009
Prices and advice don't test the core problem (finding, calculating, citing) and would pull in a much larger domain. 10-Q, web search and charts wait until V4 ships.

### D-010
**Dense retrieval with a cross-encoder reranker, both local.** The plan recommended hosted models (Voyage, Cohere); the L40S runs both in seconds, eval runs stay free and reproducible, and hosted models remain one config value away.

Measured on 22 retrieval-scorable `dev` questions (draft gold set, 34 questions at the time):

| Configuration | Recall@5 | Recall@10 | MRR |
|---|---|---|---|
| Keyword (FTS5 BM25) | 17.6% | 26.7% | 0.108 |
| Dense, bge-base-en-v1.5 | 27.7% | 47.1% | 0.294 |
| Dense, bge-base + bge-reranker-base | 31.6% | 55.7% | 0.247 |
| Dense, bge-base + bge-reranker-v2-m3 | 42.1% | 58.5% | 0.353 |
| Dense, Qwen3-Embedding-0.6B | 34.7% | 59.9% | 0.354 |
| **Dense, Qwen3-Embedding-0.6B + bge-reranker-v2-m3** | **43.6%** | **69.1%** | **0.400** |
| Hybrid, Qwen3-Embedding-0.6B + bge-reranker-v2-m3 | 46.6% | 65.3% | 0.367 |

The stronger reranker is the biggest single gain (+10 points). Hybrid wins Recall@5 by one source on 22 questions but loses Recall@10 and MRR; the original requirements made hybrid conditional on a clear win, so it's off. Re-run once the gold set is verified.

### D-011
A comparison needs a chunk from every filing; an unfiltered top 5 rarely has them. Companies resolve through aliases ("Google" → GOOGL). A bare calendar year also matches the fiscal year that mostly covers it, from `period_end_date` (NVIDIA "2024" → FY2024 and FY2025). +6 points Recall@5 at equal models.

### D-012
A loop of about 80 lines (`_Run` in `agent.py`) is easy to trace, test and run on any model; frameworks would hide it. Four tools: `search_filings`, `get_filing_section`, `calculator`, `verify_citation`. The model never does arithmetic. Tool errors (unknown company, year outside the corpus) come back as results the agent can correct.

### D-013
Everything calls `Model.complete(messages, tools)`; providers translate one neutral message format. Claude (Sonnet 5) is the baseline; open models run behind an OpenAI-compatible server. The final answer is JSON written as text, not provider-specific structured output, so every model is held to the same format; one repair turn handles malformed JSON.

### D-014
Changing the model under test must never change the scoring. Numbers, citations, abstention and tool use are scored deterministically; the judge only scores narrative points, faithfulness and context relevance.

### D-015
**Every claim is labeled** verified / calculated / cited / unverified, and unverified claims are shown, not dropped. Labels alone change no metric, so Research mode also sends failed claims back to the agent **once**, with `repair_calls` extra tool calls (`--no-repair` = V3). Unbounded repair loops were rejected for cost and latency. [STAGE5_VERIFY.md](STAGE5_VERIFY.md).

### D-016
A call refused for exceeding the budget had valid arguments. Counting it as incorrect made tool-call correctness 79.6% instead of 97.7% (V3); it now counts toward unnecessary calls and `over_budget_rate`.

### D-017
A run where every question errored reported a 0% unsupported-answer rate, because errors counted as "declined". Rates now use completed questions only; errors are listed and count as failed task completion.

### D-018
Verification accepted any number within 0.5%, so 31,500 "matched" a table showing 31,370. Now two numbers match when they agree to the precision either is written with: "$31.4 billion" matches 31,370 million, 31,500 doesn't. Rescoring every run changed no reported number ([I-009](#i-009)).

### D-019
Knobs were spread across module constants and environment variables. `config.yaml` now holds all of them, per stage; the stage guides' Knobs tables point at its keys. Environment variables still override model choices, so eval scripts can switch models without editing files.

### D-020
Self-hosted models have no token price. Cost per question = wall-clock latency × $0.86/h (`model.gpu_hourly_usd`, an L40S cloud rate). It's an estimate of GPU time, not a measured bill; latency itself is measured.

### D-021
V5 gave the agent an XBRL tool. On 31 test questions, correctness went 95.2% → 100% but citation accuracy 95.9% → 89.4%: the agent took figures from XBRL and cited passages that don't contain them, and often skipped document search. The product promise is that every number traces to a filing passage, so XBRL stays out (`agent.structured_data: false`).

### D-022
V7 runs only if V6 shows a gap to the Claude baseline. Training data comes from 10-Ks outside the eval corpus (other companies; corpus companies only for FY2022 and earlier, since a 10-K repeats two prior years). Trajectories come from open models, never Claude: Anthropic's Usage Policy prohibits training on its outputs without authorization. Only runs that pass the eval's deterministic checks are kept; never gold questions.

### D-023
A public demo needs rate limiting, an API budget and Postgres + pgvector. Decide closer to shipping; until then everything is local, one process and one file.

### D-024
Documentation follows the shape of [prompt-pick-place](https://github.com/Michae1Park/prompt-pick-place): a README with the pipeline table, one guide per stage (Commands · Data · Knobs · Outputs · How it works · Things to try · Gotchas), one playground per stage, and this log. The PRD and project plan were folded in: requirements and roadmap into the README, rationale here, the how into the stage guides.

## Issues

| ID | Date | Issue | Status |
|---|---|---|---|
| [I-001](#i-001) | 2026-09-30 | Python segfaults at random on the i9-14900K's P-cores | Workaround |
| [I-002](#i-002) | 2026-10-01 | GPU out of memory with vLLM, the API and an eval at once | Workaround |
| [I-003](#i-003) | 2026-10-01 | Qwen3.6-27B-FP8 on vLLM: KV-cache OOM, then a missing `nvcc` | Workaround |
| [I-004](#i-004) | 2026-09-30 | Gold sources miss correct alternatives (MD&A tables restating Item 8) | Open |
| [I-005](#i-005) | 2026-10-01 | "Amazon's net sales in 2021" is answerable from a comparative column | Open |
| [I-006](#i-006) | 2026-09-30 | No Anthropic credentials: Claude baseline and judge metrics unmeasured | Open |
| [I-007](#i-007) | 2026-09-30 | The agent hits its tool budget on 19–35% of questions | Open |
| [I-008](#i-008) | 2026-09-30 | Ask mode computes percentages in its head (no calculator) | Open |
| [I-009](#i-009) | 2026-10-01 | Verification accepted figures within 0.5% | Resolved |
| [I-010](#i-010) | 2026-09-30 | ROS's pytest plugins break test collection | Workaround |
| [I-011](#i-011) | 2026-09-30 | No gold question is verified yet | Open |

### I-001
9 of 16 runs crashed on CPUs 0–15, 0 of 16 on 16–31, in two separate Python installs: the known Raptor Lake instability. Microcode 0x133 is loaded; the BIOS (1401, 2023-09) predates Intel's fixes. **Workaround:** run heavy jobs with `taskset -c 16-31`; `tenk eval run --resume` continues a crashed run. **Fix:** BIOS update + Intel default profile; RMA if crashes persist.

### I-002
vLLM (35 GB) + an eval's embedder and reranker (6 GB) + the API (3 GB) exceed 44 GB. **Workaround:** `scripts/serve_vllm.sh` defaults to 72% GPU memory; `TENK_LOCAL_DEVICE=cpu` keeps the API's local models on the CPU. [SERVING.md](SERVING.md).

### I-003
28.5 GB of weights left too little for the KV cache with CUDA graphs and the vision encoder profiled; an fp8 KV cache made FlashInfer JIT-compile kernels without a CUDA toolkit. **Workaround:** `--enforce-eager`, images/video off, bf16 KV cache, 32k context (preset `qwen3.6-27b-fp8`).

### I-004
The Microsoft R&D gold sources are Item 8 income statements; the MD&A R&D table states the same figures and is what retrieval finds, so Recall@5 scores 0 on a correct retrieval (`playground/retrieve.py --gold aapl-msft-rd-fy2023-2025`). **Next:** the reviewer adds those as `acceptable_sources` while verifying.

### I-005
The question is an `unsupported` near miss by the gold rules (FY2021 is outside the corpus), but the FY2023 10-K shows 2021 as a comparative column and the model cites it. Decide while verifying: keep the rule, or reword the question.

### I-006
The baseline column, faithfulness, narrative correctness and context relevance show "n/m". **Next:** set `ANTHROPIC_API_KEY`, run `scripts/eval_all.sh`.

### I-007
19% (Qwen3.5-9B) to 26% (27B) of test questions exhaust the 15-call budget, mostly with repeated `verify_citation` calls on quotes. Tune the prompt on `dev`, not `test`.

### I-008
Ask mode has no calculator, so "what share…" answers carry a mental calculation as text and score 0 (rule: all arithmetic via the calculator). Research mode is the path for calculations.

### I-009
See D-018. Rescoring every run changed nothing reported: no wrong figure had slipped through yet.

### I-010
`/opt/ros/jazzy` on `PYTHONPATH` loads ROS's pytest plugins. **Workaround:** `env -u PYTHONPATH uv run pytest`.

### I-011
All 100 questions are `origin: drafted`; every reported result is provisional. **Next:** `tenk gold show <id>`, check against EDGAR, `tenk gold verify <id> --by <initials>`. [EVAL.md](EVAL.md#verifying-a-question).
