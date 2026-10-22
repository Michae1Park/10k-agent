# V6 · Fine-tune + ablation: what changes the result, and by how much?

**Builds on** V4, like V5. Two experiments, each against the same V4 system on the same questions:

| Experiment | Question | Script |
|---|---|---|
| **Fine-tune** | Does a LoRA-tuned Qwen3.5-9B, trained on its own passing trajectories, close the gap to the untuned 27B ([D-025](DECISIONS.md#d-025), [D-029](../../docs/DECISIONS.md#d-029))? | `run.sh finetune …`, `run.sh tuned`, `run.sh compare` |
| **Ablation** | What does each part contribute? Remove or add one thing at a time | `run.sh ablation` ([`ablation.sh`](../versions/v6_finetune_ablation/ablation.sh)) |

## Commands

### Fine-tune

Training-corpus steps set `TENK_CORPUS=versions/v6_finetune_ablation/corpus.yaml` and `TENK_DATA_DIR=data/train`, so they never read or write the eval corpus.

```bash
export SEC_USER_AGENT="Your Name you@example.com"
versions/v6_finetune_ablation/run.sh finetune data         # 19 companies' 10-Ks: fetch, ingest, embed; generate questions (XBRL answers)

scripts/serve_vllm.sh qwen3.5-9b                            # other terminal: the base model
versions/v6_finetune_ablation/run.sh finetune rollouts     # the agent answers each train question twice (temperature 0.7); every attempt scored
versions/v6_finetune_ablation/run.sh finetune dataset      # passing attempts -> data/train/sft.jsonl (D-022 guards)

# stop vLLM: training needs the whole GPU
versions/v6_finetune_ablation/run.sh finetune train --max-steps 3   # smoke test first
versions/v6_finetune_ablation/run.sh finetune train        # LoRA -> data/finetune/v6/adapter
versions/v6_finetune_ablation/run.sh finetune merge        # -> data/finetune/v6/merged (CPU, ~20 GB RAM)
```

(`run.sh finetune <step>` is `scripts/finetune.sh <step>`.) Then evaluate base and tuned **with the same settings**, one vLLM server at a time, with the same `TAG`:

```bash
scripts/serve_vllm.sh qwen3.5-9b      && versions/v5_models/run.sh qwen3.5-9b      # base (V5's 9B column)
scripts/serve_vllm.sh qwen3.5-9b-v6   && versions/v6_finetune_ablation/run.sh tuned
versions/v6_finetune_ablation/run.sh compare                # base 9B vs tuned 9B vs the 27B, if V5 ran it

# Rank-64 variant: same data, CONFIG=versions/v6_finetune_ablation/config-r64.yaml for train and merge, then
TUNED_MODEL=data/finetune/v6-r64/merged TUNED_NAME=tenk/qwen3.5-9b-v6-r64 scripts/serve_vllm.sh qwen3.5-9b-v6
TUNED_NAME=tenk/qwen3.5-9b-v6-r64 versions/v6_finetune_ablation/run.sh tuned

# In-distribution check: held-out training companies (never trained on)
TENK_CORPUS=versions/v6_finetune_ablation/corpus.yaml TENK_DATA_DIR=data/train TENK_MODEL=openai:tenk/qwen3.5-9b-v6 \
  tenk eval run research --gold data/train/questions.jsonl --split heldout --label v6-tuned-heldout
```

`EXTRA="--include-unverified"` adds the draft gold questions while none are verified ([I-011](DECISIONS.md#i-011)).

### Ablation

```bash
scripts/serve_vllm.sh qwen3.6-27b-fp8                      # the default model
versions/v6_finetune_ablation/run.sh ablation              # every row, then one comparison report
ROWS="full closed-book no-verify" versions/v6_finetune_ablation/run.sh ablation   # a subset
```

| Row | Change against the full V4 system | Question |
|---|---|---|
| `full` | none: the baseline | |
| `closed-book` | the model alone, no documents | What do retrieval and the filings add? (it should invent numbers) |
| `rag-only` | one call over retrieved chunks (V2) | What does the agent loop add? |
| `no-rerank` | dense candidates, no cross-encoder | What does the reranker add? |
| `keyword` | BM25 only, no embeddings | What do embeddings add? |
| `no-slots` | one search, not one per company and filing | What does slot search add ([D-011](../../docs/DECISIONS.md#d-011))? |
| `no-verify` | no verification, no repair (V3) | What do verification and repair add? |
| `budget-8` | 8 tool calls instead of 15 | How much budget does the agent need ([I-007](DECISIONS.md#i-007))? |
| `xbrl` | + the XBRL tool | More right numbers, fewer traceable ones? ([D-021](DECISIONS.md#d-021); earlier run: `v5-research-xbrl-qwen3.5-9b`) |

Report: `eval/reports/ablation-<model>-<split>-<tag>.{md,html}`. Judge metrics afterwards: serve `gpt-oss-20b`, then `tenk eval judge abl6-…` for the row labels.

## Data

| Data | Where |
|---|---|
| Training corpus | `versions/v6_finetune_ablation/corpus.yaml`: 19 companies outside the eval corpus, FY2023–2025; year ends from January to December |
| Training filings and database | `data/train/` (`raw/`, `tenk.db`), git-ignored |
| Questions | `data/train/questions.jsonl`: gold-format, `origin: xbrl_template`, `split: train \| heldout` |
| Rollouts | `data/train/rollouts.jsonl`: one row per attempt with checks, usage and the full transcript |
| SFT records | `data/train/sft.jsonl`: `{"messages", "tools", "meta"}` in OpenAI chat format |
| Adapter · merged model | `data/finetune/v6/adapter` · `data/finetune/v6/merged` |

**Question kinds** (all answers from the filing's XBRL facts, no model involved):

| Kind | Example | Teaches |
|---|---|---|
| `single` | What was Walmart's total revenue in fiscal 2025? | find one value in the right year's filing |
| `series` | … in fiscal years 2023, 2024 and 2025? | one value per filing |
| `change` | By what percentage did Nike's net income change from fiscal 2024 to fiscal 2025? | calculator, cited inputs |
| `pair` | Compare Cisco's and Intel's operating income in fiscal 2024. | two companies, same units |
| `abstain_year` | … in fiscal 2020? | decline outside the corpus years |
| `abstain_company` | What was IBM's total revenue in fiscal 2024? | decline outside the corpus companies |

20% of companies are held out (`split: heldout`), whole, so held-out questions test new filings rather than new phrasings.

## Knobs

`versions/v6_finetune_ablation/config.yaml`:

| Knob | Default | Notes |
|---|---|---|
| `base_model` | Qwen/Qwen3.5-9B | Hybrid attention (Gated DeltaNet + full), loaded as `AutoModelForImageTextToText` |
| `enable_thinking` | false | Must match `TENK_OPENAI_THINKING` at rollout and eval time |
| `max_length` | 38400 | Longer samples are dropped, never truncated. 38.3k tokens peaked at 41.3 GiB (L40S: 44.4) |
| `lora.r` · `alpha` · `dropout` | 16 · 32 · 0 | 40M trainable parameters (0.42%). Dropout > 0 runs out of memory at 38k tokens: it keeps a copy of each adapted layer's input |
| `lora.target_modules` | language-model attention, DeltaNet and MLP projections | Vision tower untouched |
| `train.epochs` · `learning_rate` | 2 · 1e-4 | Cosine schedule, 5% warmup |
| `train.gradient_accumulation_steps` | 8 | Batch size 1 (one trajectory) |

`tenk finetune` options: `rollouts --samples 2 --temperature 0.7`; `dataset --max-per-question 2 --max-tool-errors 0`. Script variables: `SAMPLES`, `TEMPERATURE`, `WORKERS`, `CPUS`, `CONFIG`.

## Outputs

`tenk finetune dataset` prints what it kept and why it rejected the rest:

```json
{"kept": N, "rejected": {"failed checks": N, "tool errors": N, "duplicate attempt": N, "split heldout": N, "D-022: …": N}, "by_kind": {"single": N, …}}
```

Any `D-022: …` count above zero means something upstream is wrong (a mixed-up data directory, a gold question copied in); find out why before training.

`train.py` logs train and eval loss (assistant tokens only) and saves checkpoints every 50 steps. The deliverable is `run.sh compare` (`tenk eval compare`): [EVAL.md](EVAL.md#comparing-runs).

## How it works

1. **Questions with known answers.** `questions.py` reads each training filing's XBRL facts (the XBRL ablation's lookup) and fills templates. No model writes questions or answers, so nothing in the data is Claude output.
2. **Rollouts.** The unchanged agent (same prompt, tools, budget, verification and repair) answers each question `--samples` times at temperature 0.7. The full conversation is captured (`research(..., transcript=)`): traces keep only summaries.
3. **Rejection sampling.** An attempt passes when the eval's deterministic checks all hold: the right numbers (company, year, unit-normalized), every numeric claim verified in its cited chunk, the right abstention, valid JSON, no tool-budget overrun, and no failed tool calls.
4. **Guards (D-022).** `dataset.py` refuses Claude outputs, gold questions (by ID and by text), and anything touching an eval-corpus filing (company list or any eval chunk ID such as `AAPL-FY2024-…`). These are rules, checked before quality filters, and unit-tested.
5. **Rendering exactly as served.** Each record is rendered with the model's own chat template, with tool-call arguments as objects (as vLLM passes them) and `enable_thinking` as at inference. A conversation with a later user turn (the repair round) is cut into segments, because the template renders earlier turns differently once a new user turn exists. The script checks that every prompt is a prefix of the full render.
6. **Loss on what the model generated.** Labels cover assistant turns only (tool calls, final JSON, `<|im_end|>`), not the prompt, tool results or the `<think></think>` the server inserts. Logits are computed only at those positions (`logits_to_keep`), because full logits for 40k tokens × 248k vocabulary would need ~40 GB.
7. **LoRA, then merge.** PEFT LoRA on the language model's projections; `merge.py` folds the adapter into full BF16 weights, so vLLM serves V6 like any model (`qwen3.5-9b-v6` preset, served as `tenk/qwen3.5-9b-v6`).

## Things to try

| Try | Question it answers |
|---|---|
| `train --max-steps 3` | Does a step fit in memory with your longest samples? |
| `rollouts --limit 20` then `dataset` | What's the pass rate, and which checks fail most? |
| `--samples 4` | Does more sampling help the hard kinds (`pair`, `change`)? |
| Drop `max_per_question` to 1 | Are easy questions over-represented? |
| Compare `v6-*-heldout` vs `v6-*-test` | Did it learn the task, or only the training companies' filings? |
| `lora.r: 64` | Does capacity matter at this data size? |

## Gotchas

- **One GPU, one job.** vLLM (rollouts, eval) and training can't share the L40S. Stop one before starting the other.
- **Thinking must match.** Trajectories are recorded with thinking off; training renders with `enable_thinking: false`; evaluate both models with `TENK_OPENAI_THINKING=0`.
- **Base vs tuned, same everything.** Use V5's 9B run with the same `TAG`, not the older `v4-research-qwen3.5-9b`: `config.json` now records `thinking`, older runs don't.
- **Small test set.** 31 test questions give ±10-point intervals; a real but small gain may read "no clear difference". The held-out training companies add hundreds of in-distribution questions.
- **Speed.** About 35 s per step for a 25k-token trajectory on the L40S (the first step is slower: kernel compilation). Budget roughly samples × epochs × 30 s; check the sample count `train.py` prints before committing to a full run.
- **No MTP head.** Transformers doesn't load Qwen's multi-token-prediction weights, so the merged model lacks them. vLLM serves it normally (tested); only speculative decoding needs them.
- **Slow kernels.** `causal_conv1d` isn't installed (it needs `nvcc`, [I-003](../../docs/DECISIONS.md#i-003)); the PyTorch fallback is correct but slower.
- **CIKs** in `corpus.yaml` were written by hand. After the first fetch, check each filer's name: `jq -r .name data/train/raw/edgar/*/submissions.json`.
