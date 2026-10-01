#!/usr/bin/env bash
# Serve an open tool-calling model with vLLM's OpenAI-compatible API (V6).
#
#   scripts/serve_vllm.sh qwen3.5-9b            # then: TENK_MODEL=openai:Qwen/Qwen3.5-9B
#   scripts/serve_vllm.sh qwen3.6-27b-fp8
#   scripts/serve_vllm.sh qwen3.5-9b-v7      # V7: then TENK_MODEL=openai:tenk/qwen3.5-9b-v7
#
# Everything must fit on one L40S (48 GB). The tenk process also keeps the embedder and
# reranker on the GPU (3–6 GB per process), so vLLM gets 72% of memory by default.
# VLLM_BIN points at the vLLM executable (default: `vllm` on PATH).
# On this machine the i9-14900K's P-cores crash under load; CPUS pins the server to E-cores.
set -euo pipefail

preset=${1:?usage: serve_vllm.sh <preset> [extra vllm args]}
shift
VLLM_BIN=${VLLM_BIN:-vllm}
PORT=${PORT:-8001}
GPU_UTIL=${GPU_UTIL:-0.72}
MAX_LEN=${MAX_LEN:-65536}  # agent trajectories reach 20–40k tokens
CPUS=${CPUS:-16-31}

case "$preset" in
  qwen3.5-9b)
    model=Qwen/Qwen3.5-9B
    args=(--dtype bfloat16 --tool-call-parser qwen3_coder --reasoning-parser qwen3)
    ;;
  qwen3.5-9b-v7)
    # The merged V7 fine-tune (scripts/finetune.sh merge); same settings as its base.
    model=${V7_MODEL:-data/finetune/v7/merged}
    name=tenk/qwen3.5-9b-v7
    args=(--dtype bfloat16 --tool-call-parser qwen3_coder --reasoning-parser qwen3)
    ;;
  qwen3.6-27b-fp8)
    model=Qwen/Qwen3.6-27B-FP8
    # 28.5 GB of weights: skip CUDA graphs and the unused vision encoder, or the KV cache
    # allocation runs out of memory on a 48 GB card. An fp8 KV cache would double context
    # but makes FlashInfer JIT-compile kernels, which needs a CUDA toolkit (nvcc).
    MAX_LEN=${MAX_LEN_27B:-32768}
    args=(--tool-call-parser qwen3_coder --reasoning-parser qwen3
      --enforce-eager --limit-mm-per-prompt '{"image":0,"video":0}')
    ;;
  qwen3-1.7b)
    model=Qwen/Qwen3-1.7B
    args=(--dtype bfloat16 --tool-call-parser hermes --reasoning-parser qwen3)
    ;;
  *)
    echo "unknown preset $preset (qwen3.5-9b, qwen3.5-9b-v7, qwen3.6-27b-fp8, qwen3-1.7b)" >&2
    exit 2
    ;;
esac

exec taskset -c "$CPUS" "$VLLM_BIN" serve "$model" \
  --served-model-name "${name:-$model}" \
  --port "$PORT" \
  --gpu-memory-utilization "$GPU_UTIL" \
  --max-model-len "$MAX_LEN" \
  --enable-prefix-caching \
  --enable-auto-tool-choice \
  "${args[@]}" "$@"
