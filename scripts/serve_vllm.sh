#!/usr/bin/env bash
# Serve an open tool-calling model with vLLM's OpenAI-compatible API.
#
#   scripts/serve_vllm.sh qwen3.6-27b-fp8       # the default model (config.yaml llm, generation)
#   scripts/serve_vllm.sh gemma-4-31b-fp8       # another model: --model openai:RedHatAI/gemma-4-31B-it-FP8-block
#   scripts/serve_vllm.sh qwen3.5-9b            # a smaller one: --model openai:Qwen/Qwen3.5-9B
#   scripts/serve_vllm.sh gpt-oss-20b           # the V2 judge, on port 8002 (experiments/v2/judge.py)
#
# Everything must fit on one L40S (48 GB). The tenk process also keeps the embedder and
# reranker on the GPU (3–6 GB per process), so vLLM gets 72% of memory by default (the 27B 80%).
# VLLM_BIN points at the vLLM executable (default: `vllm` on PATH, else the vllm_py311 conda env).
# On this machine the i9-14900K's P-cores crash under load; CPUS pins the server to E-cores.
set -euo pipefail

preset=${1:?usage: serve_vllm.sh <preset> [extra vllm args]}
shift
VLLM_BIN=${VLLM_BIN:-$(command -v vllm || echo ~/miniconda3/envs/vllm_py311/bin/vllm)}
PORT=${PORT:-8001}
GPU_UTIL=${GPU_UTIL:-0.72}
MAX_LEN=${MAX_LEN:-65536}  # agent trajectories reach 20–40k tokens
CPUS=${CPUS:-16-31}

case "$preset" in
  qwen3.5-9b)
    model=Qwen/Qwen3.5-9B
    args=(--dtype bfloat16 --tool-call-parser qwen3_coder --reasoning-parser qwen3)
    ;;
  qwen3.6-27b-fp8)
    model=Qwen/Qwen3.6-27B-FP8
    # 28.5 GB of weights and no vision encoder. CUDA graphs (~17 tok/s vs ~11.5 eager) fit
    # only with few sequences: the default 256 makes startup profiling fill the card. Graphs
    # cost 0.08 GiB, but compiling leaves 1.6 GiB of KV cache at 72% (under one 32k request);
    # 80% gives 3.2 requests at 32k and leaves ~8 GB for the embedder and reranker. An fp8 KV
    # cache would double context but makes FlashInfer JIT-compile kernels, which needs nvcc.
    MAX_LEN=${MAX_LEN_27B:-32768}
    GPU_UTIL=${GPU_UTIL_27B:-0.80}
    args=(--tool-call-parser qwen3_coder --reasoning-parser qwen3
      --max-num-seqs "${MAX_SEQS_27B:-4}" --limit-mm-per-prompt '{"image":0,"video":0}')
    ;;
  gemma-4-31b-fp8)
    # Gemma 4 31B in bf16 is 62 GB; this FP8 build is ~32 GB, the same precision as the
    # Qwen 27B. Memory limits as qwen3.6-27b-fp8 had (I-003): eager mode, no vision, 32k.
    # Its 30.4 GiB of weights leave 0.4 GiB of KV cache at 72%; 86% gives 6.6 GiB (1.4
    # requests at 32k) and leaves ~6 GB for the eval's embedder and reranker.
    model=RedHatAI/gemma-4-31B-it-FP8-block
    MAX_LEN=${MAX_LEN_31B:-32768}
    GPU_UTIL=${GPU_UTIL_31B:-0.86}
    args=(--tool-call-parser gemma4 --reasoning-parser gemma4
      --enforce-eager --limit-mm-per-prompt '{"image":0,"video":0}')
    ;;
  gpt-oss-20b)
    # The fixed LLM judge (D-029): another family than every model under test, ~13 GB.
    # Port 8002 so it can run beside a small model; stop a 27B/31B model first.
    model=openai/gpt-oss-20b
    PORT=${JUDGE_PORT:-8002}
    GPU_UTIL=${JUDGE_GPU_UTIL:-0.45}
    MAX_LEN=${MAX_LEN_JUDGE:-32768}
    args=(--reasoning-parser openai_gptoss)
    ;;
  qwen3-1.7b)
    model=Qwen/Qwen3-1.7B
    args=(--dtype bfloat16 --tool-call-parser hermes --reasoning-parser qwen3)
    ;;
  *)
    echo "unknown preset $preset (qwen3.6-27b-fp8, gemma-4-31b-fp8, qwen3.5-9b, gpt-oss-20b, qwen3-1.7b)" >&2
    exit 2
    ;;
esac

# Tool calling only for presets with a parser: vLLM refuses auto tool choice without one (the judge).
[[ " ${args[*]} " == *" --tool-call-parser "* ]] && args+=(--enable-auto-tool-choice)

exec taskset -c "$CPUS" "$VLLM_BIN" serve "$model" \
  --served-model-name "${name:-$model}" \
  --port "$PORT" \
  --gpu-memory-utilization "$GPU_UTIL" \
  --max-model-len "$MAX_LEN" \
  --enable-prefix-caching \
  "${args[@]}" "$@"
