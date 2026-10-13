# V5 · Model comparison: which model should run the agent?

**Builds on** V4. The system stays fixed (same prompt, tools, budget, verification and repair); only the model changes. Every model answers the same questions, and one report compares them to the default with intervals and paired tests ([D-027](DECISIONS.md#d-027)). **Run it:** `versions/v5_models/run.sh <model>` per model, then `run.sh compare`.

## Commands

One model fits on the GPU at a time: serve one, run it, stop it, serve the next. Keep the same `TAG` (default: today's date) across steps.

```bash
scripts/serve_vllm.sh qwen3.6-27b-fp8   # terminal 1: the default model; wait for "Application startup complete"
versions/v5_models/run.sh qwen3.6-27b-fp8   # terminal 2

scripts/serve_vllm.sh gemma-4-31b-fp8   # after stopping the previous server
versions/v5_models/run.sh gemma-4-31b-fp8

scripts/serve_vllm.sh qwen3.5-9b        # the V6 fine-tune base
versions/v5_models/run.sh qwen3.5-9b

ANTHROPIC_API_KEY=... versions/v5_models/run.sh claude   # optional; CLAUDE_MODEL=anthropic:<id> to pick one

versions/v5_models/run.sh compare       # every finished model vs the first in the list
scripts/serve_vllm.sh gpt-oss-20b && versions/v5_models/run.sh judge && versions/v5_models/run.sh compare   # + judge metrics
```

`SPLIT=dev` tunes, `test` reports. `EXTRA="--include-unverified"` adds the draft gold questions while none are verified ([I-011](DECISIONS.md#i-011)); `EXTRA="--limit 5"` for a quick look.

## Models

| Preset | Model | Size on the GPU | Why it's here |
|---|---|---|---|
| `qwen3.6-27b-fp8` | Qwen/Qwen3.6-27B-FP8 | ~29 GB, 32k context | **The default** ([D-029](DECISIONS.md#d-029)); the baseline column |
| `gemma-4-31b-fp8` | RedHatAI/gemma-4-31B-it-FP8-block | ~32 GB, 32k context | Another family at the same size and precision |
| `qwen3.5-9b` | Qwen/Qwen3.5-9B | 18 GB, 64k context | Smaller and faster; the base V6 fine-tunes |
| `claude` | `anthropic:claude-sonnet-5-5` | API | Reference point, if a key is set; never used for training ([D-022](DECISIONS.md#d-022)) |

Serving details, memory limits and every preset: [SERVING.md](SERVING.md).

## Outputs

| Output | Where |
|---|---|
| One run per model | `eval/runs/v5-<model>-<split>-<tag>/` (`results.jsonl`, `summary.json`, `config.json`) |
| Comparison | `eval/reports/v5-models-<split>-<tag>.{md,html,json}`: metrics with intervals, differences vs the default, fixed vs regressed questions, the first failing stage per failure |

Earlier runs, before the renumbering ([D-028](DECISIONS.md#d-028)): `v4-research-qwen3.5-9b` (9B) and `v6-research-qwen3.6-27b-fp8` (27B), compared in `eval/reports/v4-v5-v6.md`.

| Metric (test, 31 questions) | Qwen3.5-9B | Qwen3.6-27B-FP8 |
|---|---|---|
| Answer correctness | 95.2% | 100% |
| Citation accuracy | 95.9% | 97.7% |
| Unnecessary calls / question | 3.10 | 1.94 |
| Mean latency | 120 s | 178 s |

None of these differences is significant at p < 0.05 with 31 questions; read them as direction, not proof.

## Things to try

| Try | Question it answers |
|---|---|
| `TENK_OPENAI_THINKING=1` (or `true`) for one model | Does thinking help tool use enough to pay for its tokens and latency? |
| `SPLIT=all` | Narrower intervals: 100 questions instead of 31 (but `dev` was used for tuning) |
| Look at the "Regressed" table in the report | Which questions one model gets and the other doesn't, and the stage where it broke |

## Gotchas

- **Run each preset against its own server.** `run.sh` sets `TENK_MODEL` from the preset, and vLLM rejects requests for a model it isn't serving, so a mismatch fails on the first question.
- **32k context for the 27B and 31B** ([I-003](DECISIONS.md#i-003)). Long agent runs can hit it; those show as errors, reported apart from the rates ([D-017](DECISIONS.md#d-017)).
- **GPU memory.** The eval process keeps the embedder and reranker on the GPU too ([I-002](DECISIONS.md#i-002)); `serve_vllm.sh` leaves room for them.
- **Cost** for open models is latency × `gpu_hourly_usd` ([D-020](DECISIONS.md#d-020)); for Claude it's the API price. Comparable, not identical.
