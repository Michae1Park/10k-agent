# Serving: models, API, UI, traces

## Commands

Up to three terminals on the workstation, each from the repo root.

| Terminal | Starts | Ready when it prints |
|---|---|---|
| 1 | vLLM on :8001 | `Application startup complete` (~2 min for 9B, longer for 27B) |
| 2 | API on :8000 | `Uvicorn running on http://127.0.0.1:8000` |
| 3 | UI on :3000 | `Ready` |

```bash
# 1 · the default open model (config.yaml model.name)
scripts/serve_vllm.sh qwen3.6-27b-fp8

# 2 · API on the model from terminal 1 (local embedder/reranker on the CPU: the GPU is full)
source .venv/bin/activate
TENK_LOCAL_DEVICE=cpu tenk serve
TENK_MODEL=anthropic:claude-sonnet-5 tenk serve        # optional: Claude (ANTHROPIC_API_KEY)

# 3 · UI, then open http://localhost:3000
cd web && npm install && npm run dev
```

## The model layer

Every model call goes through `Model.complete(messages, tools)` (`src/tenk_agent/models.py`); providers translate one neutral message format ([D-013](../../docs/DECISIONS.md#d-013)).

| `model.name` | Provider | Notes |
|---|---|---|
| `openai:<model>` | OpenAI-compatible server at `model.base_url` | **Default** (`openai:Qwen/Qwen3.6-27B-FP8`, [D-029](../../docs/DECISIONS.md#d-029)). vLLM or Ollama; `temperature 0`; `<think>` blocks stripped; thinking off (`model.thinking: false`) |
| `anthropic:<model id>` | Anthropic SDK | Optional comparison column (V5). Needs `ANTHROPIC_API_KEY`. Thinking blocks are replayed unchanged between turns |

## vLLM presets

`scripts/serve_vllm.sh <preset>`, pinned to the E-cores ([I-001](../../docs/DECISIONS.md#i-001)), port 8001 (the judge: 8002). vLLM lives in its own conda env; the script finds `~/miniconda3/envs/vllm_py311/bin/vllm` when `vllm` isn't on `PATH` (tested with vLLM 0.20.0).

| Preset | Model | Weights | Context | KV cache | Notes |
|---|---|---|---|---|---|
| `qwen3.5-9b` | Qwen/Qwen3.5-9B | 18 GB BF16 | 64k | 124k tokens at `GPU_UTIL=0.80` | V2–V4 runs, V6 fine-tune base |
| `qwen3.5-9b-v6` | `data/finetune/v6/merged` (`TUNED_MODEL`), served as `tenk/qwen3.5-9b-v6` | 18 GB BF16 | 64k | | V6 ([V6 guide](V6_FINETUNE_ABLATION.md)); no MTP head, so no speculative decoding |
| `qwen3.6-27b-fp8` | Qwen/Qwen3.6-27B-FP8 | 28.5 GB FP8 | 32k | 27k tokens at `GPU_UTIL=0.80` | eager mode, no vision, bf16 KV ([I-003](../../docs/DECISIONS.md#i-003)); use `--workers 2` |
| `gemma-4-31b-fp8` | RedHatAI/gemma-4-31B-it-FP8-block | 30.4 GiB FP8 | 32k | 6.6 GiB at `GPU_UTIL=0.86` (its default; 0.72 leaves 0.4 GiB and vLLM refuses to start) | V5 comparison. bf16 (62 GB) doesn't fit; same limits as the 27B; `gemma4` tool and reasoning parsers |
| `gpt-oss-20b` | openai/gpt-oss-20b | ~13 GB MXFP4 | 32k | | **The LLM judge** ([D-029](../../docs/DECISIONS.md#d-029)), port 8002, `GPU_UTIL=0.45`. Fits beside the 9B, not beside the 27B/31B: judge those runs afterwards with `tenk eval judge` |
| `qwen3-1.7b` | Qwen/Qwen3-1.7B | 3.4 GB | 64k | | smoke tests |

| Variable | Default | What it does |
|---|---|---|
| `GPU_UTIL` | 0.72 | GPU memory share; leaves room for the embedder and reranker ([I-002](../../docs/DECISIONS.md#i-002)) |
| `MAX_LEN` / `MAX_LEN_27B` / `MAX_LEN_31B` / `MAX_LEN_JUDGE` | 65536 / 32768 / 32768 / 32768 | Context length; trajectories reach 20–40k tokens per request |
| `PORT`, `CPUS`, `VLLM_BIN` | 8001, 16-31, `vllm` (else the conda env above) | |
| `JUDGE_PORT`, `JUDGE_GPU_UTIL` | 8002, 0.45 | the `gpt-oss-20b` preset |

**What fits on one L40S (48 GB, ~44 usable):** serving ≤ 14B in BF16 comfortably, ~30B in FP8 / 4-bit tightly, 70B not at all. LoRA fine-tuning of the 9B in BF16 up to 38k-token samples (tested, 41.3 GiB); QLoRA ≤ 14B ([D-026](DECISIONS.md#d-026)).

## API

`tenk serve` (`src/tenk_agent/api.py`). Loads `config.yaml` once at startup.

| Endpoint | Returns |
|---|---|
| `GET /api/health` | model, retriever, number of embedded chunks |
| `GET /api/corpus` | the 24 filings |
| `POST /api/ask` `{"question"}` | the answer JSON ([the V2 guide](V2_RAG.md#how-it-works)) |
| `POST /api/research` `{"question"}` | Server-Sent Events: `step`, `thought`, then `answer` (or `error`) |
| `GET /api/traces?limit=50` · `GET /api/traces/<id>` | recent traces · one trace with every span |
| `GET /api/chunks/<id>` | a chunk with its filing, section title and EDGAR link |

```bash
curl -s localhost:8000/api/health
curl -sN -X POST localhost:8000/api/research -H 'content-type: application/json' \
     -d '{"question": "What was Google'"'"'s capital expenditure in 2025?"}'
```

## UI

`web/` (Next.js). Talks to `NEXT_PUBLIC_API_URL` (default `http://127.0.0.1:8000`).

| Part | What it does |
|---|---|
| **Ask / Research** toggle | One question box; Research streams its steps live |
| Demo tasks | Six representative questions (each also a gold question), one click each |
| Answer | `[n]` markers open the cited passage: company, fiscal year, section, period, **Open filing on EDGAR** |
| Evidence | Every claim with its status: verified · calculated · cited · unverified |
| **View trace** | Every model turn and tool call with inputs, outputs, tokens, latency, cost |

## Traces

Every Ask and Research request is traced (`src/tenk_agent/tracing.py`): model turns (tokens, latency, cost), retrieval, tool calls (inputs, outputs, errors). Stored in the `traces` table; feed the UI's trace view and the eval's agent and cost metrics. With `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` set (`LANGFUSE_HOST` optional), each trace is also exported to Langfuse.

## Watching from a laptop

| How you reach the workstation | Open |
|---|---|
| Cursor / VS Code Remote-SSH | `http://localhost:3000` (the editor forwards 3000 and 8000: Ports tab) |
| Plain SSH | `ssh -L 3000:localhost:3000 -L 8000:localhost:8000 <user>@<workstation-ip>`, then `http://localhost:3000` |

The UI calls the API from the browser, so port 8000 must be forwarded too.

## Gotchas

- **GPU memory.** vLLM + an eval + the API don't all fit: `TENK_LOCAL_DEVICE=cpu` for the API, or stop one ([I-002](../../docs/DECISIONS.md#i-002)).
- **After `tenk embed` with another model**, restart the API: it checks the embedding model at startup.
- **Thinking mode.** Qwen models think by default; `TENK_OPENAI_THINKING=0` for interactive use (7 s instead of minutes).
