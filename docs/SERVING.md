# Serving: models

The open models run on vLLM's OpenAI-compatible server, one terminal each, from the repo root.

```bash
scripts/serve_vllm.sh qwen3.6-27b-fp8    # the default model: V0 structure agent, agentic chunker, V2 answers (port 8001)
scripts/serve_vllm.sh gpt-oss-20b        # the V2 judge (port 8002); stop the 27B first
```

Ready when it prints `Application startup complete` (a few minutes for the 27B: it compiles CUDA graphs).

## The model layer

V2 answers and the judge go through `Model.complete(messages)` (`src/tenk_agent/models.py`); providers translate one neutral message format ([D-013](DECISIONS.md#d-013)). The V0 structure agent and agentic chunker call vLLM's OpenAI API directly, for schema-constrained decoding.

| `generation.model` | Provider | Notes |
|---|---|---|
| `openai:<model>` | OpenAI-compatible server at `generation.base_url` | **Default** (`openai:Qwen/Qwen3.6-27B-FP8`, [D-029](DECISIONS.md#d-029)). vLLM or Ollama; `temperature 0`; `<think>` blocks stripped; thinking off (`thinking: false`) |
| `anthropic:<model id>` | Anthropic SDK | Optional. Needs `ANTHROPIC_API_KEY` (environment or `.env`) |

## vLLM presets

`scripts/serve_vllm.sh <preset>`, pinned to the E-cores ([I-001](DECISIONS.md#i-001)), port 8001 (the judge: 8002). vLLM lives in its own conda env; the script finds `~/miniconda3/envs/vllm_py311/bin/vllm` when `vllm` isn't on `PATH` (tested with vLLM 0.20.0).

| Preset | Model | Weights | Context | Notes |
|---|---|---|---|---|
| `qwen3.6-27b-fp8` | Qwen/Qwen3.6-27B-FP8 | 28.5 GB FP8 | 32k | **The default.** CUDA graphs (~17 tok/s vs ~11.5 eager), at most 4 sequences, 80% of memory, no vision, bf16 KV ([I-003](DECISIONS.md#i-003)). Keep V2's `run.workers` ≤ 4 |
| `gpt-oss-20b` | openai/gpt-oss-20b | ~13 GB MXFP4 | 32k | **The V2 judge** ([D-029](DECISIONS.md#d-029)), port 8002, `GPU_UTIL=0.45`. Doesn't fit beside the 27B: judge a run afterwards with `experiments/v2/judge.py` |
| `qwen3.5-9b` | Qwen/Qwen3.5-9B | 18 GB BF16 | 64k | A smaller model to compare: `--model openai:Qwen/Qwen3.5-9B` |
| `gemma-4-31b-fp8` | RedHatAI/gemma-4-31B-it-FP8-block | 30.4 GiB FP8 | 32k | Another model to compare (`GPU_UTIL` 0.86) |
| `qwen3-1.7b` | Qwen/Qwen3-1.7B | 3.4 GB | 64k | Smoke tests |

| Variable | Default | What it does |
|---|---|---|
| `GPU_UTIL` | 0.72 (27B: 0.80) | GPU memory share; leaves room for the embedder and reranker ([I-002](DECISIONS.md#i-002)) |
| `MAX_LEN` / `MAX_LEN_27B` / `MAX_LEN_31B` / `MAX_LEN_JUDGE` | 65536 / 32768 / 32768 / 32768 | Context length |
| `MAX_SEQS_27B` | 4 | Sequences at once on the 27B; the vLLM default (256) fills the card at startup |
| `PORT`, `CPUS`, `VLLM_BIN` | 8001, 16-31, `vllm` (else the conda env above) | |
| `JUDGE_PORT`, `JUDGE_GPU_UTIL` | 8002, 0.45 | the `gpt-oss-20b` preset |

**What fits on one L40S (48 GB, ~44 usable):** serving ≤ 14B in BF16 comfortably, ~30B in FP8 / 4-bit tightly, 70B not at all.

## Gotchas

- **GPU memory.** The 27B leaves ~8 GB for the embedder and reranker that `tenk search`, `ask`, `chat` and the V1/V2 experiments load. Embedding a whole corpus (`--stage embed`) wants vLLM stopped, or `retrieval.device: cpu` ([I-002](DECISIONS.md#i-002)).
- **Thinking mode.** Qwen models think by default; `generation.thinking: false` keeps an answer to seconds instead of minutes.
- **One model per port.** The 27B and the judge don't fit together: generate first, then stop the 27B and serve the judge.
