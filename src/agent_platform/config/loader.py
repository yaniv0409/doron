from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from agent_platform.config.settings import AppSettings, ModelSettings


def load_settings() -> AppSettings:
    load_dotenv()
    settings = AppSettings()
    apply_environment(settings, os.environ)
    return settings


def apply_environment(settings: AppSettings, environ: dict[str, str] | os._Environ[str]) -> None:
    _set_if_present(environ, "OPENROUTER_API_KEY", lambda value: _setattr(settings.openrouter, "api_key", value))
    _set_if_present(environ, "OPENROUTER_BASE_URL", lambda value: _setattr(settings.openrouter, "base_url", value))
    _set_if_present(environ, "OPENROUTER_APP_URL", lambda value: _setattr(settings.openrouter, "app_url", value))
    _set_if_present(environ, "OPENROUTER_APP_TITLE", lambda value: _setattr(settings.openrouter, "app_title", value))
    _set_if_present(
        environ,
        "OPENROUTER_EMBEDDING_MODEL",
        lambda value: _setattr(settings.openrouter, "embedding_model", value),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_LOG_DIR",
        lambda value: _setattr(settings.logging, "directory", Path(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_LOG_MAX_BYTES",
        lambda value: _setattr(settings.logging, "max_bytes", int(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_LOG_BACKUP_COUNT",
        lambda value: _setattr(settings.logging, "backup_count", int(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_TRACE_DIR",
        lambda value: _setattr(settings.traces, "directory", Path(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_CHECKPOINT_DIR",
        lambda value: _setattr(settings.traces, "checkpoint_directory", Path(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_BROWSER_HEADLESS",
        lambda value: _setattr(settings.browser, "headless", _to_bool(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_BROWSER_TIMEOUT_MS",
        lambda value: _setattr(settings.browser, "default_timeout_ms", int(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_BROWSER_NAVIGATION_TIMEOUT_MS",
        lambda value: _setattr(settings.browser, "navigation_timeout_ms", int(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_BROWSER_NETWORK_IDLE_TIMEOUT_MS",
        lambda value: _setattr(settings.browser, "network_idle_timeout_ms", int(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_BROWSER_CONTENT_TEXT_MAX_CHARS",
        lambda value: _setattr(settings.browser, "content_text_max_chars", int(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_BROWSER_MAX_LINKS_PER_PAGE",
        lambda value: _setattr(settings.browser, "max_links_per_page", int(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_BROWSER_EXTRACT_MAIN_CONTENT_ONLY",
        lambda value: _setattr(settings.browser, "extract_main_content_only", _to_bool(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_BROWSER_LOCALE",
        lambda value: _setattr(settings.browser, "locale", value),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_BROWSER_TIMEZONE_ID",
        lambda value: _setattr(settings.browser, "timezone_id", value),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_BROWSER_VIEWPORT_WIDTH",
        lambda value: _setattr(settings.browser, "viewport_width", int(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_BROWSER_VIEWPORT_HEIGHT",
        lambda value: _setattr(settings.browser, "viewport_height", int(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_BROWSER_USER_AGENT",
        lambda value: _setattr(settings.browser, "user_agent", value),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_KUZU_REFERENCE_PATH",
        lambda value: _setattr(settings.docs, "kuzu_reference_path", Path(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_COMPRESSION_ENABLED",
        lambda value: _setattr(settings.compression, "enabled", _to_bool(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_COMPRESSION_TOOL_ENABLED",
        lambda value: _setattr(settings.compression, "tool_enabled", _to_bool(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_COMPRESSION_THRESHOLD_RATIO",
        lambda value: _setattr(settings.compression, "threshold_ratio", float(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_COMPRESSION_FALLBACK_BUDGET_CHARS",
        lambda value: _setattr(settings.compression, "fallback_budget_chars", int(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_COMPRESSION_MIN_GROWTH_CHARS",
        lambda value: _setattr(settings.compression, "min_growth_chars", int(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_COMPRESSION_MAX_NOTES",
        lambda value: _setattr(settings.compression, "max_notes", int(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_COMPRESSION_MAX_FINDINGS",
        lambda value: _setattr(settings.compression, "max_findings", int(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_COMPRESSION_MAX_TOOL_SUMMARIES",
        lambda value: _setattr(settings.compression, "max_tool_summaries", int(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_COMPRESSION_TIMEOUT_SECONDS",
        lambda value: _setattr(settings.compression, "timeout_seconds", int(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_AGENT_RUN_TIMEOUT_SECONDS",
        lambda value: _setattr(settings.runtime, "agent_run_timeout_seconds", int(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_DISABLE_BROWSER_TOOLS",
        lambda value: _setattr(settings.debug, "disable_browser_tools", _to_bool(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_DISABLE_MODEL_SWITCH_TOOL",
        lambda value: _setattr(settings.debug, "disable_model_switch_tool", _to_bool(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_DISABLE_COMPRESSION_TOOL",
        lambda value: _setattr(settings.debug, "disable_compression_tool", _to_bool(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_DISABLE_DB_WRITE_TOOL",
        lambda value: _setattr(settings.debug, "disable_db_write_tool", _to_bool(value)),
    )
    _set_if_present(
        environ,
        "AGENT_PLATFORM_MODELS_FILE",
        lambda value: _set_models_from_file(settings, Path(value)),
    )


def _set_models_from_file(settings: AppSettings, path: Path) -> None:
    if not path.exists():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    settings.models = [ModelSettings.model_validate(item) for item in payload]


def _set_if_present(
    environ: dict[str, str] | os._Environ[str],
    key: str,
    setter: Any,
) -> None:
    value = environ.get(key)
    if value:
        setter(value)


def _setattr(target: Any, name: str, value: Any) -> None:
    setattr(target, name, value)


def _to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
