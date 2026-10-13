#!/usr/bin/env bash
# V6 · Fine-tune + ablation. Two experiments, both measured against V4 on the same questions:
#
# Fine-tune (docs/V6_FINETUNE_ABLATION.md): does a LoRA-tuned Qwen3.5-9B close the gap to the untuned 27B?
#   versions/v6_finetune_ablation/run.sh finetune data|rollouts|dataset|train|merge   # scripts/finetune.sh
#   scripts/serve_vllm.sh qwen3.5-9b-v6    && versions/v6_finetune_ablation/run.sh tuned
#   (rank-64 variant: CONFIG=versions/v6_finetune_ablation/config-r64.yaml for train and merge,
#    then TUNED_MODEL=data/finetune/v6-r64/merged TUNED_NAME=tenk/qwen3.5-9b-v6-r64 for serve and tuned)
#   versions/v6_finetune_ablation/run.sh compare      # base 9B vs tuned vs the 27B (V5's runs)
#
# Ablation (ablation.sh): change one thing at a time on the default model.
#   versions/v6_finetune_ablation/run.sh ablation     # ROWS="full closed-book ..." to pick rows
source "$(dirname "$0")/../common.sh"
here=versions/v6_finetune_ablation
TUNED=${TUNED_NAME:-tenk/qwen3.5-9b-v6}
tuned_slug=${TUNED##*/}

case "${1:?usage: run.sh finetune <step>|tuned|compare|ablation}" in
  finetune)
    shift
    scripts/finetune.sh "$@"
    ;;
  tuned)
    # Identical settings to the base 9B (D-025): thinking off, same prompt, tools, budget.
    TENK_MODEL="openai:$TUNED" TENK_OPENAI_THINKING=0 eval_run "v6-$tuned_slug-$SPLIT-$TAG" research
    ;;
  compare)
    columns=()
    for pair in "base-9b=v5-qwen3.5-9b" "$tuned_slug=v6-$tuned_slug" "27b=v5-qwen3.6-27b-fp8"; do
      label="${pair#*=}-$SPLIT-$TAG"
      [ -f "eval/runs/$label/results.jsonl" ] && columns+=("${pair%%=*}=$label")
    done
    [ ${#columns[@]} -ge 2 ] || { echo "need at least two finished runs (have: ${columns[*]:-none}); base and 27B come from versions/v5_models/run.sh" >&2; exit 1; }
    tenk eval compare "${columns[@]}" --out "eval/reports/v6-finetune-$SPLIT-$TAG"
    ;;
  ablation)
    "$here/ablation.sh"
    ;;
  *) echo "usage: run.sh finetune <step>|tuned|compare|ablation" >&2; exit 2 ;;
esac
