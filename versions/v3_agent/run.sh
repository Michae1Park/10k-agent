#!/usr/bin/env bash
# V3 · Agent (Research mode): the tool loop, without V4's verification and repair.
# The model must be served first (scripts/serve_vllm.sh). Runs V2 too, for the side-by-side.
#
#   versions/v3_agent/run.sh
source "$(dirname "$0")/../common.sh"

model=$(model_slug)
v2="v2-ask-$model-$SPLIT-$TAG"
v3="v3-research-$model-$SPLIT-$TAG"
eval_run "$v2" ask
eval_run "$v3" research --no-verify
tenk eval compare "V2=$v2" "V3=$v3" --out "eval/reports/v3-$model-$SPLIT-$TAG"
