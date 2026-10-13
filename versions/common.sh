# Shared by versions/*/run.sh and scripts/eval_all.sh: sourced, not run.
#
# SPLIT (test) · TAG (today) · WORKERS (4) · EXTRA (more `tenk eval run` args) · CPUS (16-31)
# Run labels are <version>-<system>[-<model>]-<split>-<tag>; an interrupted run resumes.
# Draft (unverified) gold questions are excluded unless EXTRA includes --include-unverified.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."  # repo root, wherever the caller was started

SPLIT=${SPLIT:-test}
TAG=${TAG:-$(date +%Y%m%d)}
WORKERS=${WORKERS:-4}
EXTRA=${EXTRA:-}
CPUS=${CPUS:-16-31}  # the P-cores crash Python under load (I-001)

tenk() { taskset -c "$CPUS" env -u PYTHONPATH uv run tenk "$@"; }

# The model under test as a label part, e.g. qwen3.6-27b-fp8 (from TENK_MODEL or config.yaml).
model_slug() {
  env -u PYTHONPATH uv run python -c \
    "from tenk_agent.config import Settings; print(Settings.load().model.split(':', 1)[1].split('/')[-1].lower())"
}

# eval_run <label> <tenk eval run args...>
eval_run() {
  local label=$1
  shift
  local resume=()
  [ -f "eval/runs/$label/results.jsonl" ] && resume=(--resume)
  # shellcheck disable=SC2086
  tenk eval run "$@" --split "$SPLIT" --workers "$WORKERS" --label "$label" "${resume[@]}" $EXTRA
}
