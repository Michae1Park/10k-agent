#!/usr/bin/env bash
# V2 · RAG (Ask mode): V1's top chunks + one model call → cited claims.
# The model must be served first: scripts/serve_vllm.sh qwen3.6-27b-fp8 (or TENK_MODEL=anthropic:<id>).
#
#   versions/v2_rag/run.sh
#   TENK_MODEL=openai:Qwen/Qwen3.5-9B versions/v2_rag/run.sh
source "$(dirname "$0")/../common.sh"

label="v2-ask-$(model_slug)-$SPLIT-$TAG"
eval_run "$label" ask
tenk eval report "V2=$label" --out "eval/reports/v2-$SPLIT-$TAG.md"
