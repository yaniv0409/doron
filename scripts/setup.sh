#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
CONFIG_DIR="${CONFIG_DIR:-$ROOT_DIR/config}"
MODELS_FILE="${MODELS_FILE:-$CONFIG_DIR/models.json}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}"
OPENROUTER_BASE_URL="${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}"
OPENROUTER_APP_URL="${OPENROUTER_APP_URL:-}"
OPENROUTER_APP_TITLE="${OPENROUTER_APP_TITLE:-agent-platform}"
OPENROUTER_EMBEDDING_MODEL="${OPENROUTER_EMBEDDING_MODEL:-openai/text-embedding-3-small}"
AGENT_PLATFORM_LOG_DIR="${AGENT_PLATFORM_LOG_DIR:-logs}"
AGENT_PLATFORM_LOG_MAX_BYTES="${AGENT_PLATFORM_LOG_MAX_BYTES:-2000000}"
AGENT_PLATFORM_LOG_BACKUP_COUNT="${AGENT_PLATFORM_LOG_BACKUP_COUNT:-5}"
AGENT_PLATFORM_TRACE_DIR="${AGENT_PLATFORM_TRACE_DIR:-traces}"
AGENT_PLATFORM_CHECKPOINT_DIR="${AGENT_PLATFORM_CHECKPOINT_DIR:-traces/checkpoints}"
AGENT_PLATFORM_BROWSER_HEADLESS="${AGENT_PLATFORM_BROWSER_HEADLESS:-true}"
AGENT_PLATFORM_BROWSER_TIMEOUT_MS="${AGENT_PLATFORM_BROWSER_TIMEOUT_MS:-20000}"
AGENT_PLATFORM_KUZU_REFERENCE_PATH="${AGENT_PLATFORM_KUZU_REFERENCE_PATH:-docs/kuzu-notes.md}"
INSTALL_PLAYWRIGHT="${INSTALL_PLAYWRIGHT:-yes}"
INSTALL_DEV_DEPS="${INSTALL_DEV_DEPS:-yes}"
PLAYWRIGHT_BROWSER="${PLAYWRIGHT_BROWSER:-chromium}"
DEFAULT_MODEL_NAME="${DEFAULT_MODEL_NAME:-openai/gpt-4.1-mini}"
MODEL_NAMES_CSV="${MODEL_NAMES_CSV:-openai/gpt-4.1-mini,openai/gpt-5.2}"

log() {
  printf '[setup] %s\n' "$1"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf '[setup] missing required command: %s\n' "$1" >&2
    exit 1
  fi
}

load_existing_config() {
  if [[ -f "$ENV_FILE" ]]; then
    log "loading existing environment from $ENV_FILE"
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi

  OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}"
  OPENROUTER_BASE_URL="${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}"
  OPENROUTER_APP_URL="${OPENROUTER_APP_URL:-}"
  OPENROUTER_APP_TITLE="${OPENROUTER_APP_TITLE:-agent-platform}"
  OPENROUTER_EMBEDDING_MODEL="${OPENROUTER_EMBEDDING_MODEL:-openai/text-embedding-3-small}"
  AGENT_PLATFORM_LOG_DIR="${AGENT_PLATFORM_LOG_DIR:-logs}"
  AGENT_PLATFORM_LOG_MAX_BYTES="${AGENT_PLATFORM_LOG_MAX_BYTES:-2000000}"
  AGENT_PLATFORM_LOG_BACKUP_COUNT="${AGENT_PLATFORM_LOG_BACKUP_COUNT:-5}"
  AGENT_PLATFORM_TRACE_DIR="${AGENT_PLATFORM_TRACE_DIR:-traces}"
  AGENT_PLATFORM_CHECKPOINT_DIR="${AGENT_PLATFORM_CHECKPOINT_DIR:-traces/checkpoints}"
  AGENT_PLATFORM_BROWSER_HEADLESS="${AGENT_PLATFORM_BROWSER_HEADLESS:-true}"
  AGENT_PLATFORM_BROWSER_TIMEOUT_MS="${AGENT_PLATFORM_BROWSER_TIMEOUT_MS:-20000}"
  AGENT_PLATFORM_KUZU_REFERENCE_PATH="${AGENT_PLATFORM_KUZU_REFERENCE_PATH:-docs/kuzu-notes.md}"
  INSTALL_PLAYWRIGHT="${INSTALL_PLAYWRIGHT:-yes}"
  INSTALL_DEV_DEPS="${INSTALL_DEV_DEPS:-yes}"
  PLAYWRIGHT_BROWSER="${PLAYWRIGHT_BROWSER:-chromium}"

  if [[ -f "$MODELS_FILE" ]]; then
    load_models_from_file
  fi
}

load_models_from_file() {
  mapfile -t model_lines < <(
    "$PYTHON_BIN" - <<'PY' "$MODELS_FILE"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
for item in data:
    print(item["name"])
default_item = next((item["name"] for item in data if item.get("is_default")), data[0]["name"])
print(f"DEFAULT={default_item}")
PY
  )

  local names=()
  local line
  for line in "${model_lines[@]}"; do
    if [[ "$line" == DEFAULT=* ]]; then
      DEFAULT_MODEL_NAME="${line#DEFAULT=}"
      continue
    fi
    names+=("$line")
  done
  if [[ "${#names[@]}" -gt 0 ]]; then
    MODEL_NAMES_CSV="$(IFS=,; printf '%s' "${names[*]}")"
  fi
}

prompt_value() {
  local var_name="$1"
  local prompt_text="$2"
  local current_value="$3"
  local secret="${4:-no}"
  local input=""

  if [[ "$secret" == "yes" ]]; then
    read -r -s -p "$prompt_text [$current_value]: " input
    printf '\n'
  else
    read -r -p "$prompt_text [$current_value]: " input
  fi
  if [[ -z "$input" ]]; then
    printf -v "$var_name" '%s' "$current_value"
    return
  fi
  printf -v "$var_name" '%s' "$input"
}

prompt_yes_no() {
  local var_name="$1"
  local prompt_text="$2"
  local current_value="$3"
  local input=""

  read -r -p "$prompt_text [$current_value]: " input
  if [[ -z "$input" ]]; then
    input="$current_value"
  fi
  input="$(printf '%s' "$input" | tr '[:upper:]' '[:lower:]')"
  case "$input" in
    y|yes|true|1) printf -v "$var_name" 'yes' ;;
    n|no|false|0) printf -v "$var_name" 'no' ;;
    *)
      printf '[setup] expected yes or no for %s\n' "$prompt_text" >&2
      exit 1
      ;;
  esac
}

normalize_boolean_env() {
  local value="$1"
  value="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"
  if [[ "$value" == "yes" || "$value" == "true" || "$value" == "1" ]]; then
    printf 'true'
  else
    printf 'false'
  fi
}

quote_env_value() {
  local value="$1"
  value="${value//\'/\'\\\'\'}"
  printf "'%s'" "$value"
}

collect_configuration() {
  printf '\nAgent Platform setup\n\n'
  prompt_value OPENROUTER_API_KEY "OpenRouter API key" "$OPENROUTER_API_KEY" "yes"
  prompt_value OPENROUTER_BASE_URL "OpenRouter base URL" "$OPENROUTER_BASE_URL"
  prompt_value OPENROUTER_APP_TITLE "Application title sent to OpenRouter" "$OPENROUTER_APP_TITLE"
  prompt_value OPENROUTER_APP_URL "Application URL sent to OpenRouter" "$OPENROUTER_APP_URL"
  prompt_value OPENROUTER_EMBEDDING_MODEL "Embedding model" "$OPENROUTER_EMBEDDING_MODEL"
  prompt_value MODEL_NAMES_CSV "Chat models ordered from weaker to stronger, comma-separated" "$MODEL_NAMES_CSV"
  prompt_value DEFAULT_MODEL_NAME "Default chat model name" "$DEFAULT_MODEL_NAME"
  prompt_yes_no INSTALL_DEV_DEPS "Install dev dependencies" "$INSTALL_DEV_DEPS"
  prompt_yes_no INSTALL_PLAYWRIGHT "Install Playwright browser" "$INSTALL_PLAYWRIGHT"
  prompt_value PLAYWRIGHT_BROWSER "Playwright browser to install" "$PLAYWRIGHT_BROWSER"
  prompt_yes_no AGENT_PLATFORM_BROWSER_HEADLESS "Run browser headless by default" "$AGENT_PLATFORM_BROWSER_HEADLESS"
  prompt_value AGENT_PLATFORM_BROWSER_TIMEOUT_MS "Browser timeout in milliseconds" "$AGENT_PLATFORM_BROWSER_TIMEOUT_MS"
  prompt_value AGENT_PLATFORM_LOG_DIR "Log directory" "$AGENT_PLATFORM_LOG_DIR"
  prompt_value AGENT_PLATFORM_LOG_MAX_BYTES "Log file max bytes" "$AGENT_PLATFORM_LOG_MAX_BYTES"
  prompt_value AGENT_PLATFORM_LOG_BACKUP_COUNT "Log backup count" "$AGENT_PLATFORM_LOG_BACKUP_COUNT"
  prompt_value AGENT_PLATFORM_TRACE_DIR "Trace directory" "$AGENT_PLATFORM_TRACE_DIR"
  prompt_value AGENT_PLATFORM_CHECKPOINT_DIR "Checkpoint directory" "$AGENT_PLATFORM_CHECKPOINT_DIR"
  prompt_value AGENT_PLATFORM_KUZU_REFERENCE_PATH "Kuzu reference doc path" "$AGENT_PLATFORM_KUZU_REFERENCE_PATH"
  prompt_value VENV_DIR "Virtualenv directory" "$VENV_DIR"
}

validate_configuration() {
  if [[ -z "$OPENROUTER_API_KEY" ]]; then
    printf '[setup] OpenRouter API key is required\n' >&2
    exit 1
  fi
  if [[ -z "$MODEL_NAMES_CSV" ]]; then
    printf '[setup] at least one chat model is required\n' >&2
    exit 1
  fi
  if ! printf '%s' "$MODEL_NAMES_CSV" | tr ',' '\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | grep -Fx "$DEFAULT_MODEL_NAME" >/dev/null; then
    printf '[setup] default model must appear in the configured model list\n' >&2
    exit 1
  fi
}

write_env_file() {
  mkdir -p "$(dirname "$ENV_FILE")"
  cat >"$ENV_FILE" <<EOF
OPENROUTER_API_KEY=$(quote_env_value "$OPENROUTER_API_KEY")
OPENROUTER_BASE_URL=$(quote_env_value "$OPENROUTER_BASE_URL")
OPENROUTER_APP_URL=$(quote_env_value "$OPENROUTER_APP_URL")
OPENROUTER_APP_TITLE=$(quote_env_value "$OPENROUTER_APP_TITLE")
OPENROUTER_EMBEDDING_MODEL=$(quote_env_value "$OPENROUTER_EMBEDDING_MODEL")
AGENT_PLATFORM_MODELS_FILE=$(quote_env_value "$MODELS_FILE")
AGENT_PLATFORM_LOG_DIR=$(quote_env_value "$AGENT_PLATFORM_LOG_DIR")
AGENT_PLATFORM_LOG_MAX_BYTES=$AGENT_PLATFORM_LOG_MAX_BYTES
AGENT_PLATFORM_LOG_BACKUP_COUNT=$AGENT_PLATFORM_LOG_BACKUP_COUNT
AGENT_PLATFORM_TRACE_DIR=$(quote_env_value "$AGENT_PLATFORM_TRACE_DIR")
AGENT_PLATFORM_CHECKPOINT_DIR=$(quote_env_value "$AGENT_PLATFORM_CHECKPOINT_DIR")
AGENT_PLATFORM_BROWSER_HEADLESS=$(normalize_boolean_env "$AGENT_PLATFORM_BROWSER_HEADLESS")
AGENT_PLATFORM_BROWSER_TIMEOUT_MS=$AGENT_PLATFORM_BROWSER_TIMEOUT_MS
AGENT_PLATFORM_KUZU_REFERENCE_PATH=$(quote_env_value "$AGENT_PLATFORM_KUZU_REFERENCE_PATH")
EOF
  log "wrote $ENV_FILE"
}

write_models_file() {
  mkdir -p "$CONFIG_DIR"
  "$PYTHON_BIN" - <<'PY' "$MODELS_FILE" "$MODEL_NAMES_CSV" "$DEFAULT_MODEL_NAME"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
names = [item.strip() for item in sys.argv[2].split(",") if item.strip()]
default_name = sys.argv[3]
payload = []
for index, name in enumerate(names, start=1):
    payload.append(
        {
            "name": name,
            "rank": index * 10,
            "context_window": None,
            "cost_class": "standard",
            "supports_tools": True,
            "supports_structured_output": True,
            "is_default": name == default_name,
        }
    )
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
  log "wrote $MODELS_FILE"
}

create_venv() {
  if [[ ! -d "$VENV_DIR" ]]; then
    log "creating virtualenv at $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  else
    log "using existing virtualenv at $VENV_DIR"
  fi
}

install_python_deps() {
  local extras="."
  if [[ "$INSTALL_DEV_DEPS" == "yes" ]]; then
    extras=".[dev]"
  fi
  log "upgrading pip"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  log "installing dependencies: $extras"
  "$VENV_DIR/bin/pip" install -e "$extras"
}

install_playwright() {
  if [[ "$INSTALL_PLAYWRIGHT" != "yes" ]]; then
    log "skipping Playwright browser installation"
    return
  fi
  log "installing Playwright browser: $PLAYWRIGHT_BROWSER"
  "$VENV_DIR/bin/playwright" install "$PLAYWRIGHT_BROWSER"
}

print_summary() {
  cat <<EOF

[setup] complete

Configured files:
  .env: $ENV_FILE
  models: $MODELS_FILE
  virtualenv: $VENV_DIR

Next steps:
  1. Activate the virtualenv:
     source "$VENV_DIR/bin/activate"
  2. Start the API:
     uvicorn agent_platform.api.app:create_app --factory --reload
  3. Open FastAPI docs:
     http://127.0.0.1:8000/docs
EOF
}

main() {
  require_command "$PYTHON_BIN"
  load_existing_config
  collect_configuration
  validate_configuration
  write_env_file
  write_models_file
  create_venv
  install_python_deps
  install_playwright
  print_summary
}

main "$@"
