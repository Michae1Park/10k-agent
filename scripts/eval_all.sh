#!/usr/bin/env bash
# Run the full eval ladder (V1–V4) on one split and write the results table.
#
#   scripts/eval_all.sh                  # test split, TENK_MODEL (Qwen3.6-27B-FP8 on vLLM by default)
#   SPLIT=dev scripts/eval_all.sh
#   TENK_MODEL=openai:Qwen/Qwen3.5-9B scripts/eval_all.sh
#   JUDGE=1 scripts/eval_all.sh          # then the judge pass: stop the model, serve gpt-oss-20b first
#
# The model must already be served (scripts/serve_vllm.sh). The LLM judge runs as a second
# pass because one GPU can't hold both models: run once without JUDGE, swap servers, run
# again with JUDGE=1 and the same TAG (finished runs resume, so only the judge pass runs).
# SPLIT, TAG, WORKERS, EXTRA, CPUS: versions/common.sh. Each version also runs alone: versions/vN_*/run.sh.
set -euo pipefail

cd "$(dirname "$0")/.."
source versions/common.sh
JUDGE=${JUDGE:-0}
model=$(model_slug)

labels=(
  "v1-retrieval-$SPLIT-$TAG"
  "v2-ask-$model-$SPLIT-$TAG"
  "v3-research-$model-$SPLIT-$TAG"
  "v4-research-$model-$SPLIT-$TAG"
)

if [ "$JUDGE" != 1 ]; then
  eval_run "${labels[0]}" retrieval
  eval_run "${labels[1]}" ask
  eval_run "${labels[2]}" research --no-verify
  eval_run "${labels[3]}" research
else
  tenk eval judge "${labels[@]}"
fi

tenk eval report \
  "V1=${labels[0]}" "V2=${labels[1]}" "V3=${labels[2]}" "V4=${labels[3]}" \
  --out "eval/results-$SPLIT-$TAG.md"
