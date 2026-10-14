#!/usr/bin/env bash
# V7 · Advanced retrieval: parent-child index, row records, hybrid search, Item filter,
# cell-level verification. One change per row, each scored on the main test split and the
# stress set (eval/gold/stress.jsonl) against the naive V1/V2/V4 runs.
#
#   versions/v7_advanced_retrieval/run.sh index      # ingest + embed data/tenk-parent-child.db (GPU free)
#   versions/v7_advanced_retrieval/run.sh retrieval  # rows r1–r4, no model
#   versions/v7_advanced_retrieval/run.sh ask        # rows r1–r4, Ask mode (model served)
#   versions/v7_advanced_retrieval/run.sh research   # r4 in Research mode (model served)
#   versions/v7_advanced_retrieval/run.sh judge      # after: stop the model, serve gpt-oss-20b
#   versions/v7_advanced_retrieval/run.sh compare    # reports in eval/reports/
#
# Draft gold questions need EXTRA=--include-unverified. BASE_TAG names the naive runs to compare
# against (default: TAG).
source "$(dirname "$0")/../common.sh"

export TENK_LAYOUT=parent_child
BASE_TAG=${BASE_TAG:-$TAG}
model=$(model_slug)
GOLDS=("$SPLIT:eval/gold/questions.jsonl" "stress:eval/gold/stress.jsonl")

# Row name -> environment, each adding one change to the row before.
declare -A ROWS=(
  [r1-parents]="TENK_SEARCH_CHILDREN=0 TENK_RETRIEVAL=dense TENK_ITEM_FILTER=0"
  [r2-children]="TENK_SEARCH_CHILDREN=1 TENK_RETRIEVAL=dense TENK_ITEM_FILTER=0"
  [r3-hybrid]="TENK_SEARCH_CHILDREN=1 TENK_RETRIEVAL=hybrid TENK_ITEM_FILTER=0"
  [r4-items]="TENK_SEARCH_CHILDREN=1 TENK_RETRIEVAL=hybrid TENK_ITEM_FILTER=1"
)
ORDER=(r1-parents r2-children r3-hybrid r4-items)

# label <row> <system> <set>, e.g. v7-r4-items-ask-qwen3.6-27b-fp8-stress-20261014
label() {
  local system=$2
  [ "$system" != retrieval ] && system="$system-$model"
  echo "v7-$1-$system-$3-$TAG"
}

# run_row <row> <system> [args...]: one row on both gold sets
run_row() {
  local row=$1 system=$2
  shift 2
  for entry in "${GOLDS[@]}"; do
    local set=${entry%%:*} file=${entry#*:}
    (
      # shellcheck disable=SC2086
      export ${ROWS[$row]}
      [ "$set" = stress ] && SPLIT=test  # the stress set is all test
      eval_run "$(label "$row" "$system" "$set")" "$system" --gold "$file" "$@"
    )
  done
}

case "${1:-}" in
  index)
    tenk ingest
    tenk embed
    ;;
  retrieval)
    for row in "${ORDER[@]}"; do run_row "$row" retrieval; done
    ;;
  ask)
    for row in "${ORDER[@]}"; do run_row "$row" ask; done
    ;;
  research)
    run_row r4-items research
    ;;
  judge)
    labels=()
    for set in "$SPLIT" stress; do
      for row in "${ORDER[@]}"; do labels+=("$(label "$row" ask "$set")"); done
      labels+=("$(label r4-items research "$set")")
    done
    tenk eval judge "${labels[@]}"
    ;;
  compare)
    for set in "$SPLIT" stress; do
      base_ask="v2-ask-$model-$set-$BASE_TAG"
      base_research="v4-research-$model-$set-$BASE_TAG"
      args=("V2=$base_ask")
      for row in "${ORDER[@]}"; do args+=("V7-${row%%-*}=$(label "$row" ask "$set")"); done
      tenk eval compare "${args[@]}" --out "eval/reports/v7-ask-$model-$set-$TAG"
      tenk eval compare "V4=$base_research" "V7=$(label r4-items research "$set")" \
        --out "eval/reports/v7-research-$model-$set-$TAG"
    done
    ;;
  *) sed -n '2,15p' "$0" >&2; exit 2 ;;
esac
