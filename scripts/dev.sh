#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
API_BASE_URL="${VITE_API_BASE_URL:-http://${BACKEND_HOST}:${BACKEND_PORT}}"

resolve_python() {
  local candidate
  for candidate in \
    "${PYTHON_BIN:-}" \
    "$ROOT_DIR/.venv/bin/python" \
    "$ROOT_DIR/../.venv/bin/python"
  do
    if [[ -n "${candidate:-}" && -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi

  printf 'Could not find a Python interpreter. Set PYTHON_BIN or create a virtualenv.\n' >&2
  exit 1
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  fi
}

cleanup() {
  if [[ -n "${FRONTEND_PID:-}" ]] && kill -0 "$FRONTEND_PID" >/dev/null 2>&1; then
    kill "$FRONTEND_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "${BACKEND_PID:-}" ]] && kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
    kill "$BACKEND_PID" >/dev/null 2>&1 || true
  fi
}

trap cleanup INT TERM EXIT

PYTHON_BIN="$(resolve_python)"
require_command npm

printf '[dev] using python: %s\n' "$PYTHON_BIN"
printf '[dev] backend:  http://%s:%s\n' "$BACKEND_HOST" "$BACKEND_PORT"
printf '[dev] frontend: http://%s:%s\n' "$FRONTEND_HOST" "$FRONTEND_PORT"
printf '[dev] api base: %s\n' "$API_BASE_URL"

"$PYTHON_BIN" -m uvicorn agent_platform.api.app:create_app --factory --reload --host "$BACKEND_HOST" --port "$BACKEND_PORT" &
BACKEND_PID="$!"

(
  cd "$ROOT_DIR/web"
  VITE_API_BASE_URL="$API_BASE_URL" npm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT"
) &
FRONTEND_PID="$!"

wait -n "$BACKEND_PID" "$FRONTEND_PID"
STATUS=$?

cleanup
wait "$BACKEND_PID" "$FRONTEND_PID" >/dev/null 2>&1 || true
exit "$STATUS"
