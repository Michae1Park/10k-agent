# V7 — Fine-tuning the agent model

LoRA fine-tune of Qwen3.5-9B on its own successful research trajectories (rejection sampling), measured against the same model un-tuned on the same eval ([D-022](DECISIONS.md#d-022), [D-025](DECISIONS.md#d-025)).

## Commands

Training-corpus steps set `TENK_CORPUS=finetune/corpus.yaml` and `TENK_DATA_DIR=data/train`, so they never read or write the eval corpus.

```bash
export SEC_USER_AGENT="Your Name you@example.com"
scripts/finetune.sh data         # 19 companies' 10-Ks: fetch, ingest, embed; generate questions (XBRL answers)

VLLM_BIN=... scripts/serve_vllm.sh qwen3.5-9b      # other terminal: the base model
scripts/finetune.sh rollouts     # the agent answers each train question twice (temperature 0.7); every attempt scored
scripts/finetune.sh dataset      # passing attempts -> data/train/sft.jsonl (D-022 guards)

# stop vLLM: training needs the whole GPU
scripts/finetune.sh train        # LoRA -> data/finetune/v7/adapter
scripts/finetune.sh merge        # -> data/finetune/v7/merged (CPU, ~20 GB RAM)
scripts/finetune.sh train --max-steps 3   # smoke test first
```

Then evaluate base and tuned **with the same settings**, one vLLM server at a time:

```bash
export TENK_OPENAI_THINKING=0 EXTRA="--include-unverified --no-judge"
# base (scripts/serve_vllm.sh qwen3.5-9b)
TENK_MODEL=openai:Qwen/Qwen3.5-9B tenk eval run research --split test --label v7-base-test $EXTRA
# tuned (scripts/serve_vllm.sh qwen3.5-9b-v7)
TENK_MODEL=openai:tenk/qwen3.5-9b-v7 tenk eval run research --split test --label v7-tuned-test $EXTRA
tenk eval compare BASE=v7-base-test V7=v7-tuned-test

# In-distribution check: held-out training companies (never trained on)
TENK_CORPUS=finetune/corpus.yaml TENK_DATA_DIR=data/train TENK_MODEL=... \
  tenk eval run research --gold data/train/questions.jsonl --split heldout --label v7-tuned-heldout $EXTRA
```

## Data

| Data | Where |
|---|---|
| Training corpus | `finetune/corpus.yaml`: 19 companies outside the eval corpus, FY2023–2025; year ends from January to December |
| Training filings and database | `data/train/` (`raw/`, `tenk.db`), git-ignored |
| Questions | `data/train/questions.jsonl`: gold-format, `origin: xbrl_template`, `split: train \| heldout` |
| Rollouts | `data/train/rollouts.jsonl`: one row per attempt with checks, usage and the full transcript |
| SFT records | `data/train/sft.jsonl`: `{"messages", "tools", "meta"}` in OpenAI chat format |
| Adapter · merged model | `data/finetune/v7/adapter` · `data/finetune/v7/merged` |

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

`finetune/config.yaml`:

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

`train.py` logs train and eval loss (assistant tokens only) and saves checkpoints every 50 steps. The deliverable is `tenk eval compare`: [EVAL.md](EVAL.md#comparing-runs).

## How it works

1. **Questions with known answers.** `questions.py` reads each training filing's XBRL facts (the V5 lookup) and fills templates. No model writes questions or answers, so nothing in the data is Claude output.
2. **Rollouts.** The unchanged agent (same prompt, tools, budget, verification and repair) answers each question `--samples` times at temperature 0.7. The full conversation is captured (`research(..., transcript=)`): traces keep only summaries.
3. **Rejection sampling.** An attempt passes when the eval's deterministic checks all hold: the right numbers (company, year, unit-normalized), every numeric claim verified in its cited chunk, the right abstention, valid JSON, no tool-budget overrun, and no failed tool calls.
4. **Guards (D-022).** `dataset.py` refuses Claude outputs, gold questions (by ID and by text), and anything touching an eval-corpus filing (company list or any eval chunk ID such as `AAPL-FY2024-…`). These are rules, checked before quality filters, and unit-tested.
5. **Rendering exactly as served.** Each record is rendered with the model's own chat template, with tool-call arguments as objects (as vLLM passes them) and `enable_thinking` as at inference. A conversation with a later user turn (the repair round) is cut into segments, because the template renders earlier turns differently once a new user turn exists. The script checks that every prompt is a prefix of the full render.
6. **Loss on what the model generated.** Labels cover assistant turns only (tool calls, final JSON, `<|im_end|>`), not the prompt, tool results or the `<think></think>` the server inserts. Logits are computed only at those positions (`logits_to_keep`), because full logits for 40k tokens × 248k vocabulary would need ~40 GB.
7. **LoRA, then merge.** PEFT LoRA on the language model's projections; `merge.py` folds the adapter into full BF16 weights, so vLLM serves V7 like any model (`qwen3.5-9b-v7` preset, served as `tenk/qwen3.5-9b-v7`).

## Things to try

| Try | Question it answers |
|---|---|
| `train --max-steps 3` | Does a step fit in memory with your longest samples? |
| `rollouts --limit 20` then `dataset` | What's the pass rate, and which checks fail most? |
| `--samples 4` | Does more sampling help the hard kinds (`pair`, `change`)? |
| Drop `max_per_question` to 1 | Are easy questions over-represented? |
| Compare `v7-*-heldout` vs `v7-*-test` | Did it learn the task, or only the training companies' filings? |
| `lora.r: 64` | Does capacity matter at this data size? |

## Gotchas

- **One GPU, one job.** vLLM (rollouts, eval) and training can't share the L40S. Stop one before starting the other.
- **Thinking must match.** Trajectories are recorded with thinking off; training renders with `enable_thinking: false`; evaluate both models with `TENK_OPENAI_THINKING=0`.
- **Base vs tuned, same everything.** Re-run the base model rather than reusing V4: `config.json` now records `thinking`, older runs don't.
- **Small test set.** 31 test questions give ±10-point intervals; a real but small gain may read "no clear difference". The held-out training companies add hundreds of in-distribution questions.
- **Speed.** About 35 s per step for a 25k-token trajectory on the L40S (the first step is slower: kernel compilation). Budget roughly samples × epochs × 30 s; check the sample count `train.py` prints before committing to a full run.
- **No MTP head.** Transformers doesn't load Qwen's multi-token-prediction weights, so the merged model lacks them. vLLM serves it normally (tested); only speculative decoding needs them.
- **Slow kernels.** `causal_conv1d` isn't installed (it needs `nvcc`, [I-003](DECISIONS.md#i-003)); the PyTorch fallback is correct but slower.
- **CIKs** in `corpus.yaml` were written by hand. After the first fetch, check each filer's name: `jq -r .name data/train/raw/edgar/*/submissions.json`.
