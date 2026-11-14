#!/usr/bin/env bash
# Start the whole web UI stack: model server (:8001), API (:8000) and UI dev server (:5173).
#
#   scripts/launch.sh                    # the default model, qwen3.6-27b-fp8
#   scripts/launch.sh qwen3.5-9b         # another serve_vllm.sh preset
#
#   HOST=127.0.0.1 scripts/launch.sh     # this machine only (default 0.0.0.0: the whole network)
#
# Logs: output/logs/{vllm,api}.log. Ctrl+C stops everything.
set -euo pipefail

# ERA_CPUSET (env or .env) optionally pins processes to those CPU cores, e.g. 16-31.
ERA_CPUSET=${ERA_CPUSET:-$(sed -n 's/^ERA_CPUSET=//p' "$(dirname "$0")/../.env" 2>/dev/null || true)}

preset=${1:-qwen3.6-27b-fp8}
HOST=${HOST:-0.0.0.0}
cd "$(dirname "$0")/.."
mkdir -p output/logs
pids=()

cleanup() {
  trap - EXIT INT TERM
  echo "Stopping..."
  for pid in "${pids[@]}"; do kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

wait_for() {  # url, name, pid, log
  echo "Waiting for $2..."
  until curl -sf "$1" >/dev/null; do
    kill -0 "$3" 2>/dev/null || { echo "$2 exited; see $4" >&2; tail -20 "$4" >&2; exit 1; }
    sleep 3
  done
  echo "$2 ready"
}

uv sync --extra web --extra local -q
[ -d ui/node_modules ] || (cd ui && npm install)

if curl -sf http://localhost:8001/v1/models >/dev/null; then
  echo "Model server already running on :8001"
else
  setsid scripts/serve_vllm.sh "$preset" >output/logs/vllm.log 2>&1 &
  pids+=($!)
  wait_for http://localhost:8001/v1/models "model server ($preset)" "$!" output/logs/vllm.log
fi

pin=(); [[ -n "$ERA_CPUSET" ]] && pin=(taskset -c "$ERA_CPUSET")
setsid "${pin[@]}" uv run --no-sync era serve --host "$HOST" >output/logs/api.log 2>&1 &
pids+=($!)
wait_for http://localhost:8000/api/companies "API" "$!" output/logs/api.log

ip=$(hostname -I | awk '{print $1}')
echo "UI: http://localhost:5173  (network: http://$ip:5173)"
(cd ui && npm run dev -- --host "$HOST")
