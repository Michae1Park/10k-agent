#!/usr/bin/env bash
# V0 · Data: download the 24 10-Ks, split them into sections and chunks, check the result.
# No GPU, no model. Needs SEC_USER_AGENT="Your Name you@example.com".
#
#   versions/v0_data/run.sh            # fetch (cached) + ingest + check
#   versions/v0_data/run.sh explore    # then look inside: one filing, its sections and chunks
source "$(dirname "$0")/../common.sh"

case "${1:-all}" in
  all)
    : "${SEC_USER_AGENT:?export SEC_USER_AGENT=\"Your Name you@example.com\" (SEC requires a contact)}"
    tenk fetch
    tenk ingest
    tenk check
    ;;
  explore)
    taskset -c "$CPUS" env -u PYTHONPATH uv run python versions/v0_data/ingest.py --find "Total net sales"
    ;;
  *) echo "usage: run.sh [all|explore]" >&2; exit 2 ;;
esac
