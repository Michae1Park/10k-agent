#!/usr/bin/env bash
# V7 fine-tuning, end to end (docs/FINETUNE.md). Each step resumes or overwrites safely.
#
#   scripts/finetune.sh data        # fetch, ingest, embed the training corpus; generate questions
#   scripts/finetune.sh rollouts    # the agent answers them (needs the base model on vLLM)
#   scripts/finetune.sh dataset     # passing attempts -> data/train/sft.jsonl
#   scripts/finetune.sh train       # LoRA (stop vLLM first: training needs the whole GPU)
#   scripts/finetune.sh merge       # adapter -> data/finetune/v7/merged, for vLLM
#
# Training-corpus steps run with TENK_CORPUS and TENK_DATA_DIR set, so they never touch the
# eval corpus. Pinned to the E-cores (I-001).
set -euo pipefail

step=${1:?usage: finetune.sh data|rollouts|dataset|train|merge [extra args]}
shift
CPUS=${CPUS:-16-31}
SAMPLES=${SAMPLES:-2}
TEMPERATURE=${TEMPERATURE:-0.7}
WORKERS=${WORKERS:-4}
CONFIG=${CONFIG:-finetune/config.yaml}

export TENK_CORPUS=finetune/corpus.yaml
export TENK_DATA_DIR=data/train
tenk() { taskset -c "$CPUS" env -u PYTHONPATH uv run tenk "$@"; }
# Long trajectories leave the allocator fragmented; expandable segments avoid false OOMs.
ft() {
  PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    taskset -c "$CPUS" env -u PYTHONPATH uv run --extra finetune python "$@"
}

case "$step" in
  data)
    tenk fetch
    tenk ingest
    tenk embed
    tenk finetune questions "$@"
    ;;
  rollouts)
    : "${TENK_MODEL:=openai:Qwen/Qwen3.5-9B}"
    : "${TENK_OPENAI_THINKING:=0}"
    export TENK_MODEL TENK_OPENAI_THINKING
    tenk finetune rollouts --samples "$SAMPLES" --temperature "$TEMPERATURE" --workers "$WORKERS" "$@"
    ;;
  dataset)
    tenk finetune dataset "$@"
    ;;
  train)
    ft finetune/train.py "$CONFIG" "$@"
    ;;
  merge)
    ft finetune/merge.py "$CONFIG" "$@"
    ;;
  *)
    echo "unknown step $step" >&2
    exit 2
    ;;
esac
