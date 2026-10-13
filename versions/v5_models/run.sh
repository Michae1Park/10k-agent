#!/usr/bin/env bash
# V5 · Model comparison: the same V4 system (verified agent) with each model, on the same
# questions, then one report with intervals and paired tests against the default model.
# One model fits on the GPU at a time, so serve each one, run it, and move on:
#
#   scripts/serve_vllm.sh qwen3.6-27b-fp8   &&  versions/v5_models/run.sh qwen3.6-27b-fp8   # the default
#   scripts/serve_vllm.sh gemma-4-31b-fp8   &&  versions/v5_models/run.sh gemma-4-31b-fp8
#   scripts/serve_vllm.sh qwen3.5-9b        &&  versions/v5_models/run.sh qwen3.5-9b        # V6's fine-tune base
#   ANTHROPIC_API_KEY=... versions/v5_models/run.sh claude                                    # optional baseline
#   scripts/serve_vllm.sh gpt-oss-20b       &&  versions/v5_models/run.sh judge             # judge metrics, all runs
#   versions/v5_models/run.sh compare                                                        # report, runs found so far
#
# Use the same TAG for every step (default: today's date).
source "$(dirname "$0")/../common.sh"

# preset -> TENK_MODEL (presets: scripts/serve_vllm.sh)
declare -A MODELS=(
  [qwen3.6-27b-fp8]=openai:Qwen/Qwen3.6-27B-FP8
  [gemma-4-31b-fp8]=openai:RedHatAI/gemma-4-31B-it-FP8-block
  [qwen3.5-9b]=openai:Qwen/Qwen3.5-9B
  [claude]=${CLAUDE_MODEL:-anthropic:claude-sonnet-5-5}
)
ORDER=(qwen3.6-27b-fp8 gemma-4-31b-fp8 qwen3.5-9b claude)  # the first is the baseline
label() { echo "v5-$1-$SPLIT-$TAG"; }
finished() { [ -f "eval/runs/$(label "$1")/results.jsonl" ]; }

case "${1:?usage: run.sh <preset>|judge|compare}" in
  judge)
    labels=()
    for m in "${ORDER[@]}"; do finished "$m" && labels+=("$(label "$m")"); done
    tenk eval judge "${labels[@]}"
    ;;
  compare)
    columns=()
    for m in "${ORDER[@]}"; do finished "$m" && columns+=("$m=$(label "$m")"); done
    [ ${#columns[@]} -ge 2 ] || { echo "need at least two finished models (have: ${columns[*]:-none})" >&2; exit 1; }
    tenk eval compare "${columns[@]}" --out "eval/reports/v5-models-$SPLIT-$TAG"
    ;;
  *)
    preset=$1
    [ -n "${MODELS[$preset]:-}" ] || { echo "unknown model $preset (${ORDER[*]})" >&2; exit 2; }
    [ "$preset" != claude ] || : "${ANTHROPIC_API_KEY:?the Claude baseline needs ANTHROPIC_API_KEY}"
    TENK_MODEL=${MODELS[$preset]} eval_run "$(label "$preset")" research
    ;;
esac
