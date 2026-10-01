# Serving: models, API, UI, traces

## Commands

Up to three terminals on the workstation, each from the repo root.

| Terminal | Starts | Ready when it prints |
|---|---|---|
| 1 (open models only) | vLLM on :8001 | `Application startup complete` (~2 min for 9B) |
| 2 | API on :8000 | `Uvicorn running on http://127.0.0.1:8000` |
| 3 | UI on :3000 | `Ready` |

```bash
# 1 · an open model (skip for Claude)
VLLM_BIN=~/miniconda3/envs/vllm_py311/bin/vllm scripts/serve_vllm.sh qwen3.5-9b

# 2 · API: Claude (ANTHROPIC_API_KEY) by default, or the open model from terminal 1
source .venv/bin/activate
tenk serve
TENK_MODEL=openai:Qwen/Qwen3.5-9B TENK_OPENAI_THINKING=0 TENK_LOCAL_DEVICE=cpu tenk serve   # open model

# 3 · UI, then open http://localhost:3000
cd web && npm install && npm run dev
```

## The model layer

Every model call goes through `Model.complete(messages, tools)` (`src/tenk_agent/models.py`); providers translate one neutral message format ([D-013](DECISIONS.md#d-013)).

| `model.name` | Provider | Notes |
|---|---|---|
| `anthropic:claude-sonnet-5` | Anthropic SDK | Baseline. Needs `ANTHROPIC_API_KEY`. Thinking blocks are replayed unchanged between turns |
| `openai:<model>` | OpenAI-compatible server at `model.base_url` | vLLM or Ollama; `temperature 0`; `<think>` blocks stripped |

## vLLM presets

`scripts/serve_vllm.sh <preset>`, pinned to the E-cores ([I-001](DECISIONS.md#i-001)), port 8001.

| Preset | Model | Weights | Context | KV cache | Notes |
|---|---|---|---|---|---|
| `qwen3.5-9b` | Qwen/Qwen3.5-9B | 18 GB BF16 | 64k | 124k tokens at `GPU_UTIL=0.80` | V2–V5 runs |
| `qwen3.6-27b-fp8` | Qwen/Qwen3.6-27B-FP8 | 28.5 GB FP8 | 32k | 27k tokens at `GPU_UTIL=0.80` | eager mode, no vision, bf16 KV ([I-003](DECISIONS.md#i-003)); use `--workers 2` |
| `qwen3-1.7b` | Qwen/Qwen3-1.7B | 3.4 GB | 64k | | smoke tests |

| Variable | Default | What it does |
|---|---|---|
| `GPU_UTIL` | 0.72 | GPU memory share; leaves room for the embedder and reranker ([I-002](DECISIONS.md#i-002)) |
| `MAX_LEN` / `MAX_LEN_27B` | 65536 / 32768 | Context length; trajectories reach 20–40k tokens per request |
| `PORT`, `CPUS`, `VLLM_BIN` | 8001, 16-31, `vllm` | |

**What fits on one L40S (48 GB, ~44 usable):** serving ≤ 14B in BF16 comfortably, ~30B in FP8 / 4-bit tightly, 70B not at all. LoRA fine-tuning ≤ 8B; QLoRA ≤ 14B ([D-022](DECISIONS.md#d-022)).

## API

`tenk serve` (`src/tenk_agent/api.py`). Loads `config.yaml` once at startup.

| Endpoint | Returns |
|---|---|
| `GET /api/health` | model, retriever, number of embedded chunks |
| `GET /api/corpus` | the 24 filings |
| `POST /api/ask` `{"question"}` | the answer JSON ([STAGE3_ANSWER.md](STAGE3_ANSWER.md#how-it-works)) |
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

- **GPU memory.** vLLM + an eval + the API don't all fit: `TENK_LOCAL_DEVICE=cpu` for the API, or stop one ([I-002](DECISIONS.md#i-002)).
- **After `tenk embed` with another model**, restart the API: it checks the embedding model at startup.
- **Thinking mode.** Qwen models think by default; `TENK_OPENAI_THINKING=0` for interactive use (7 s instead of minutes).
