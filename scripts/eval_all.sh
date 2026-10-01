#!/usr/bin/env bash
# Run the full eval (V1–V4) on one split and write the results table.
#
#   scripts/eval_all.sh                  # test split, TENK_MODEL (Claude baseline by default)
#   SPLIT=dev scripts/eval_all.sh
#   TENK_MODEL=openai:Qwen/Qwen3.5-9B TENK_OPENAI_THINKING=0 EXTRA="--no-judge" scripts/eval_all.sh
#
# Draft (unverified) gold questions are excluded unless EXTRA includes --include-unverified.
# Runs resume after an interruption: re-run the script with the same TAG.
set -euo pipefail

SPLIT=${SPLIT:-test}
TAG=${TAG:-$(date +%Y%m%d)}
WORKERS=${WORKERS:-4}
EXTRA=${EXTRA:-}
model=$(uv run python -c "from tenk_agent.config import Settings, load_dotenv; load_dotenv(); print(Settings().model.split(':', 1)[1].split('/')[-1])")

run() {
  local label=$1
  shift
  local resume=()
  [ -f "eval/runs/$label/results.jsonl" ] && resume=(--resume)
  # shellcheck disable=SC2086
  uv run tenk eval run "$@" --split "$SPLIT" --workers "$WORKERS" --label "$label" "${resume[@]}" $EXTRA
}

run "v1-retrieval-$SPLIT-$TAG" retrieval
run "v2-ask-$model-$SPLIT-$TAG" ask
run "v3-research-$model-$SPLIT-$TAG" research --no-repair
run "v4-research-$model-$SPLIT-$TAG" research

uv run tenk eval report \
  "V1=v1-retrieval-$SPLIT-$TAG" \
  "V2=v2-ask-$model-$SPLIT-$TAG" \
  "V3=v3-research-$model-$SPLIT-$TAG" \
  "V4=v4-research-$model-$SPLIT-$TAG" \
  --out "eval/results-$SPLIT-$TAG.md"
