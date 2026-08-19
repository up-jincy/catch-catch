#!/usr/bin/env bash

set -Eeuo pipefail

mode="${1:-auto}"
case "$mode" in
  auto|fixture|gemini) ;;
  *)
    echo "지원하지 않는 AGENT_MODE: $mode" >&2
    exit 2
    ;;
esac

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_dir="$(cd "$script_dir/.." && pwd)"
cd "$repository_dir"

backend_pid=""
frontend_pid=""

cleanup() {
  status=$?
  trap - EXIT INT TERM

  for pid in "$backend_pid" "$frontend_pid"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done
  for pid in "$backend_pid" "$frontend_pid"; do
    if [[ -n "$pid" ]]; then
      wait "$pid" 2>/dev/null || true
    fi
  done
  exit "$status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "Backend $mode mode · http://127.0.0.1:8000"
AGENT_MODE="$mode" \
  uv run --project backend uvicorn customer_signal.api:create_app --factory \
  --host 127.0.0.1 --port 8000 &
backend_pid=$!

echo "Frontend · http://127.0.0.1:3000"
NEXT_PUBLIC_API_BASE_URL="${NEXT_PUBLIC_API_BASE_URL:-http://127.0.0.1:8000}" \
  npm --prefix frontend run dev -- --port 3000 &
frontend_pid=$!

status=0
while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
  sleep 1
done

if ! kill -0 "$backend_pid" 2>/dev/null; then
  wait "$backend_pid" || status=$?
else
  wait "$frontend_pid" || status=$?
fi
exit "$status"
