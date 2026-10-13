#!/usr/bin/env bash
# V6 ablation: take the V4 system (full verified agent) and remove or add one thing at a time.
# Every row runs on the same questions with the same model, then one comparison report shows
# each row against the full system, with intervals and paired tests (docs/EVAL.md).
#
#   versions/v6_finetune_ablation/ablation.sh                 # all rows, TENK_MODEL, test split
#   ROWS="full closed-book no-rerank" versions/v6_finetune_ablation/ablation.sh
#   SPLIT=dev TAG=try1 versions/v6_finetune_ablation/ablation.sh
#
# The model must already be served (scripts/serve_vllm.sh). Interrupted rows resume.
# Judge metrics afterwards: serve gpt-oss-20b, then `tenk eval judge <labels>`.
set -euo pipefail

cd "$(dirname "$0")/../.."
source versions/common.sh
ROWS=${ROWS:-full closed-book rag-only no-rerank keyword no-slots no-verify budget-8 xbrl}
model=$(model_slug)

# row -> what changes against the full system
declare -A ARGS=(
  [full]="research"                                   # V4: the reference row
  [closed-book]="closed-book"                         # no documents at all: what retrieval adds
  [rag-only]="ask"                                    # one call over retrieved chunks: what the agent adds
  [no-rerank]="research --reranker none"              # dense candidates, no cross-encoder
  [keyword]="research --mode keyword --reranker none" # BM25 only, no embeddings
  [no-slots]="research --no-slot-search"              # one search, not one per company and filing
  [no-verify]="research --no-verify"                  # V3: no verification, no repair round
  [budget-8]="research --max-tool-calls 8"            # about half the tool budget
  [xbrl]="research --structured-data"                 # + the XBRL tool (D-021)
)

columns=()
for row in $ROWS; do
  [ -n "${ARGS[$row]:-}" ] || { echo "unknown row $row (${!ARGS[*]})" >&2; exit 2; }
  label="abl6-$row-$model-$SPLIT-$TAG"
  # shellcheck disable=SC2086
  eval_run "$label" ${ARGS[$row]}
  columns+=("$row=$label")
done

# The full system is the baseline when it ran; otherwise the first row.
tenk eval compare "${columns[@]}" --out "eval/reports/ablation-$model-$SPLIT-$TAG"
