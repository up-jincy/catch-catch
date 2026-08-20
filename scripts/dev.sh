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

backend_host="${BACKEND_HOST:-127.0.0.1}"
backend_port="${BACKEND_PORT:-8000}"
frontend_host="${FRONTEND_HOST:-127.0.0.1}"
frontend_port="${FRONTEND_PORT:-3000}"

env_file="${ENV_FILE:-}"
if [[ -n "$env_file" ]]; then
  [[ "$env_file" = /* ]] || env_file="$repository_dir/$env_file"
  if [[ ! -f "$env_file" ]]; then
    echo "ENV_FILE이 존재하지 않습니다: $env_file" >&2
    exit 2
  fi
elif [[ -f "$repository_dir/.env" ]]; then
  env_file="$repository_dir/.env"
else
  common_dir="$(git -C "$repository_dir" rev-parse --git-common-dir)"
  [[ "$common_dir" = /* ]] || common_dir="$repository_dir/$common_dir"
  main_checkout="$(cd "$(dirname "$common_dir")" && pwd -P)"
  if [[ -f "$main_checkout/.env" ]]; then
    env_file="$main_checkout/.env"
  fi
fi

env_args=()
if [[ -n "$env_file" ]]; then
  env_args=(--env-file "$env_file")
fi

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

echo "Backend $mode mode · http://$backend_host:$backend_port"
AGENT_MODE="$mode" \
  API_HOST="$backend_host" API_PORT="$backend_port" \
  FRONTEND_ORIGIN="http://$frontend_host:$frontend_port" \
  uv run --project backend uvicorn customer_signal.api:create_app --factory \
  "${env_args[@]}" --host "$backend_host" --port "$backend_port" &
backend_pid=$!

echo "Frontend · http://$frontend_host:$frontend_port"
NEXT_PUBLIC_API_BASE_URL="${NEXT_PUBLIC_API_BASE_URL:-http://$backend_host:$backend_port}" \
  npm --prefix frontend run dev -- --hostname "$frontend_host" --port "$frontend_port" &
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
