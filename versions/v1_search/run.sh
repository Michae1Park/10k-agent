#!/usr/bin/env bash
# V1 · Search: retrieval only, no LLM. Embeds the chunks once, then scores Recall@5 and MRR
# on the gold set. Local embedder and reranker (GPU if any).
#
#   versions/v1_search/run.sh
#   SPLIT=dev versions/v1_search/run.sh
source "$(dirname "$0")/../common.sh"

tenk embed  # skips chunks that already have vectors
label="v1-retrieval-$SPLIT-$TAG"
eval_run "$label" retrieval
tenk eval report "V1=$label" --out "eval/reports/v1-$SPLIT-$TAG.md"
