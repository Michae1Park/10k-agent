# Decision & issue log: archived

Decisions and issues of the archived V3–V6 (agent, verification, model comparison, fine-tuning, the old gold set and evaluation harness). Same IDs and rules as the live log, [docs/DECISIONS.md](../../docs/DECISIONS.md), which holds everything that still applies. Frozen: not updated.

## Decisions

| ID | Date | Decision | Status |
|---|---|---|---|
| [D-008](#d-008) | 2026-09-26 | Releases V1 search → V2 RAG → V3 agent → V4 reliable agent, each measured | Superseded by D-028 |
| [D-012](#d-012) | 2026-09-30 | Hand-written agent loop, four tools, 15 tool calls max | Accepted |
| [D-014](#d-014) | 2026-09-30 | The LLM judge is fixed (Claude Haiku 4.5); numbers are scored without it | Superseded by D-029 (the model) |
| [D-015](#d-015) | 2026-09-30 | Verification labels every claim; Research mode gets one repair round | Accepted |
| [D-016](#d-016) | 2026-09-30 | Over-budget tool calls count as unnecessary, not incorrect | Accepted |
| [D-017](#d-017) | 2026-10-01 | Rates are over completed questions; errors are reported separately | Accepted |
| [D-020](#d-020) | 2026-10-01 | Self-hosted cost = latency × GPU hourly rate ($0.86/h, L40S) | Accepted |
| [D-021](#d-021) | 2026-10-01 | V5: structured XBRL data stays out of the product | Accepted |
| [D-022](#d-022) | 2026-09-26 | V7 fine-tuning: data from open models only, outside the eval corpus | Superseded by D-025 |
| [D-025](#d-025) | 2026-10-01 | V7 always runs; tuned vs the same model un-tuned; D-022's data rules kept and enforced in code | Accepted |
| [D-026](#d-026) | 2026-10-01 | V7 training: HF Trainer + PEFT LoRA in the project env, assistant-only loss, merged for vLLM | Accepted |
| [D-027](#d-027) | 2026-10-01 | Comparisons report bootstrap intervals and paired tests; "better/worse" needs p < 0.05 | Accepted |
| [D-028](#d-028) | 2026-10-13 | Versions V0–V6, one folder each under `versions/`; XBRL and closed-book become ablation rows | Superseded by D-034 |
| [D-030](#d-030) | 2026-10-14 | A separate stress gold set, written to break the pipeline | Accepted |
| [D-031](#d-031) | 2026-10-14 | V7: a second, parent-child index beside the naive one, not a rewrite of V0–V2 | Superseded by D-034 |
| [D-032](#d-032) | 2026-10-14 | V7 verification checks the company and the fiscal-year column of a number | Accepted |
| [D-033](#d-033) | 2026-10-15 | V7 Research caps each search at 12,000 characters | Accepted |

## Issues

| ID | Date | Issue | Status |
|---|---|---|---|
| [I-004](#i-004) | 2026-09-30 | Gold sources miss correct alternatives (MD&A tables restating Item 8) | Open |
| [I-005](#i-005) | 2026-10-01 | "Amazon's net sales in 2021" is answerable from a comparative column | Open |
| [I-006](#i-006) | 2026-09-30 | No Anthropic credentials: Claude baseline and judge metrics unmeasured | Open |
| [I-007](#i-007) | 2026-09-30 | The agent hits its tool budget on 19–35% of questions | Open |
| [I-008](#i-008) | 2026-09-30 | Ask mode computes percentages in its head (no calculator) | Open |
| [I-009](#i-009) | 2026-10-01 | Verification accepted figures within 0.5% | Resolved |
| [I-011](#i-011) | 2026-09-30 | No gold question is verified yet | Open |


### D-008
The agent isn't built first. Each release adds one capability and is evaluated against the previous one; the README's results table has one column per release.

### D-012
A loop of about 80 lines (`_Run` in `agent.py`) is easy to trace, test and run on any model; frameworks would hide it. Four tools: `search_filings`, `get_filing_section`, `calculator`, `verify_citation`. The model never does arithmetic. Tool errors (unknown company, year outside the corpus) come back as results the agent can correct.

### D-014
Changing the model under test must never change the scoring. Numbers, citations, abstention and tool use are scored deterministically; the judge only scores narrative points, faithfulness and context relevance.

### D-015
**Every claim is labeled** verified / calculated / cited / unverified, and unverified claims are shown, not dropped. Labels alone change no metric, so Research mode also sends failed claims back to the agent **once**, with `repair_calls` extra tool calls (`--no-repair` = V3). Unbounded repair loops were rejected for cost and latency. [the V4 guide](V4_VERIFIED_AGENT.md).

### D-016
A call refused for exceeding the budget had valid arguments. Counting it as incorrect made tool-call correctness 79.6% instead of 97.7% (V3); it now counts toward unnecessary calls and `over_budget_rate`.

### D-017
A run where every question errored reported a 0% unsupported-answer rate, because errors counted as "declined". Rates now use completed questions only; errors are listed and count as failed task completion.

### D-020
Self-hosted models have no token price. Cost per question = wall-clock latency × $0.86/h (`model.gpu_hourly_usd`, an L40S cloud rate). It's an estimate of GPU time, not a measured bill; latency itself is measured.

### D-021
V5 gave the agent an XBRL tool. On 31 test questions, correctness went 95.2% → 100% but citation accuracy 95.9% → 89.4%: the agent took figures from XBRL and cited passages that don't contain them, and often skipped document search. The product promise is that every number traces to a filing passage, so XBRL stays out (`agent.structured_data: false`).

### D-022
V7 runs only if V6 shows a gap to the Claude baseline. Training data comes from 10-Ks outside the eval corpus (other companies; corpus companies only for FY2022 and earlier, since a 10-K repeats two prior years). Trajectories come from open models, never Claude: Anthropic's Usage Policy prohibits training on its outputs without authorization. Only runs that pass the eval's deterministic checks are kept; never gold questions.

### D-025
Supersedes D-022's condition: V7 no longer waits for a Claude-baseline gap. The question is what fine-tuning does to the same model, so V7 is compared to Qwen3.5-9B un-tuned, with identical settings (thinking off, same prompt, tools, budget and verification), on the eval `test` split and on held-out training companies. D-022's data rules stand and are now code (`finetune/dataset.py`, tested): no Claude outputs, no gold questions, no eval-corpus filings (eval companies' FY2022 and earlier allowed), only attempts that pass the deterministic checks. Questions are generated from XBRL facts, so no model writes them. [V6 guide](V6_FINETUNE_ABLATION.md).

### D-026
Considered: hosted no-code tuning (no Qwen3.5 support, data leaves the machine), LLaMA-Factory (not wanted), TRL `SFTTrainer` and a raw PyTorch loop. Chosen: Hugging Face `Trainer` + PEFT LoRA, ~150 lines, because the two hard parts aren't covered by stock tools. (1) Masking: the Qwen3.5 template has no assistant-mask markers, so TRL's `assistant_only_loss` can't work; records are rendered with the model's template exactly as vLLM renders them, cut at user turns, and labeled by character span. (2) Memory: full logits for a 40k-token trajectory are ~40 GB; the loss requests logits only at assistant positions. The adapter is merged into full weights so vLLM serves V7 like the base model. Training deps are the optional `finetune` extra of the project env.

### D-027
With 31 test questions, a single percentage hides a ±10-point interval, and V-to-V differences of one or two questions are noise. `tenk eval compare` therefore reports every metric with a 95% bootstrap interval over questions, and each candidate's difference from the baseline on the same questions with a bootstrap interval and a sign-flip permutation p-value, plus exact McNemar counts (fixed vs regressed) for task completion. A difference reads as better or worse only at p < 0.05: the percentile bootstrap is over-confident when few questions change (4 changes all one way give p = 0.125, yet the interval excludes zero). Failures get one cause each, the first pipeline stage that broke. [EVAL.md](EVAL.md#comparing-runs).

### D-028
**Versions you can run one at a time.** Each version has a guide in `docs/` (`V0_DATA.md` … `V6_FINETUNE_ABLATION.md`, next to EVAL, SERVING and this log) and a folder `versions/vN_*/` with a `run.sh` that runs and scores that version only and its playground script; shared code stays in `src/`. Each builds on the one before, except V5 and V6, which both start from V4.

| Now | Was | Contents |
|---|---|---|
| V0 · Data | Stage 1 / V0 | Fetch, ingest, explore filings and chunks |
| V1 · Search | Stage 2 / V1 | Retrieval only, no LLM |
| V2 · RAG | Stage 3 / V2 | Retrieval + one model call (Ask mode) |
| V3 · Agent | Stage 4 / V3 | Tool loop, no verification |
| V4 · Verified agent | Stage 5 / V4 | Verification + repair; the LLM judge |
| V5 · Model comparison | V6 | Open models vs each other (+ Claude with a key) |
| V6 · Fine-tune + ablation | V7 + V5 | LoRA variants vs the base; one-change-at-a-time rows: closed-book, no rerank, no verify, budget, + XBRL |

Closed-book (the model with no documents) and XBRL are not steps the others build on, so they are ablation rows, not versions. Runs made before the change keep their labels (`v5-research-xbrl-*` is a V6 ablation row, `v6-research-qwen3.6-27b-fp8` a V5 column); new runs and fine-tuned models use the new numbers. Supersedes D-008's numbering; D-024's guide format stands.

### D-030
The main gold set is mostly headline statement figures (129 of 195 sources in Item 8, 143 table rows) and V1 already finds 71% of them, so a better retriever has little room to show. `eval/gold/stress.jsonl` (29 questions, all `test`) targets what naive RAG gets wrong: recast segments, row labels that repeat under group headers, transposed and oldest-first tables, units in thousands vs dollars, footnote and Item 7A prose figures, calculations across companies, near-miss unsupported questions. Kept apart so the main set's history stays comparable; each question's `tags` say what makes it hard ([EVAL.md](EVAL.md#stress-set)). First finding: retrieval isn't where it's hard (V1 Recall@5 88%); reading the table right is.

### D-031
The 2026 baseline for financial RAG is structure-aware chunks, small-to-large (parent-child) retrieval, hybrid search, and lineage on every chunk. V0 already kept tables whole and V1 already prepended company / year / Item to what it embeds; V1 had hybrid search built (D-010: no clear win). What was missing: searching small units while reading large ones, and knowing which segment and fiscal year a table cell belongs to. V7 adds both as a second index (`data/tenk-parent-child.db`) and switches that default to the naive behavior, so V0–V6 keep running unchanged and each piece is one ablation row (V7 guide (deleted, D-034)). Layout-model parsers (LlamaParse, Unstructured) were not adopted: EDGAR filings are HTML, whose tables are already structured.

### D-032
V4 verified a number if it appeared anywhere in a cited chunk. A 10-K's tables show three years side by side, and a later filing restates earlier ones, so a value can be "found" in the wrong column, or in another company's filing. With V7's row records the check also requires the claimed company and, in a table, a cell under the claimed fiscal year (undated cells and prose keep the old check). It runs automatically on the V7 index; the naive index has no row records.

### D-033
Children find a few long parents, so an uncapped V7 search returns more text than a naive one (mean 14.8k vs 12.2k characters, p90 25k vs 16k, max 42k vs 18k, over the test and stress questions). In Research mode those results pile up until the prompt passes Qwen3.6-27B's 32k context: 4 of 60 r4 questions errored (2 test, 2 stress). Row r5 caps each `search_filings` result at 12,000 characters (`TENK_CONTEXT_CHARS`), about a naive search's mean, keeping the best-ranked parents. Ask runs stay uncapped: one search fits.

## Issue details

### I-004
The Microsoft R&D gold sources are Item 8 income statements; the MD&A R&D table states the same figures and is what retrieval finds, so Recall@5 scores 0 on a correct retrieval (`versions/v1_search/retrieve.py --gold aapl-msft-rd-fy2023-2025`). **Next:** the reviewer adds those as `acceptable_sources` while verifying.

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

### I-011
All 100 questions are `origin: drafted`; every reported result is provisional. **Next:** `tenk gold show <id>`, check against EDGAR, `tenk gold verify <id> --by <initials>`. [EVAL.md](EVAL.md#verifying-a-question).
