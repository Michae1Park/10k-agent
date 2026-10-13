#!/usr/bin/env bash
# V4 · Verified agent: V3 + number verification and one repair round, then the LLM judge.
# One GPU can't hold the model and the judge, so it is two passes:
#
#   versions/v4_verified_agent/run.sh          # answers: model served (scripts/serve_vllm.sh qwen3.6-27b-fp8)
#   versions/v4_verified_agent/run.sh judge    # same TAG, after: stop it, scripts/serve_vllm.sh gpt-oss-20b
#
# The whole ladder V1–V4 in one table: scripts/eval_all.sh.
source "$(dirname "$0")/../common.sh"

# Labels come from TENK_MODEL (or config.yaml): use the same TENK_MODEL and TAG for both passes.
model=$(model_slug)
v3="v3-research-$model-$SPLIT-$TAG"
v4="v4-research-$model-$SPLIT-$TAG"

case "${1:-answers}" in
  answers)
    eval_run "$v3" research --no-verify
    eval_run "$v4" research
    ;;
  judge)
    tenk eval judge "$v3" "$v4"
    ;;
  *) echo "usage: run.sh [answers|judge]" >&2; exit 2 ;;
esac
tenk eval compare "V3=$v3" "V4=$v4" --out "eval/reports/v4-$model-$SPLIT-$TAG"
