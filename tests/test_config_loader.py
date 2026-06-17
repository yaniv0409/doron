import json
from pathlib import Path

from agent_platform.config.loader import apply_environment
from agent_platform.config.settings import AppSettings


def test_apply_environment_loads_model_file_and_overrides_settings(tmp_path: Path) -> None:
    models_path = tmp_path / "models.json"
    models_path.write_text(
        json.dumps(
            [
                {
                    "name": "openai/gpt-4.1-mini",
                    "rank": 10,
                    "is_default": False,
                },
                {
                    "name": "openai/gpt-5.2",
                    "rank": 20,
                    "is_default": True,
                },
            ]
        ),
        encoding="utf-8",
    )
    environ = {
        "OPENROUTER_API_KEY": "key-123",
        "OPENROUTER_EMBEDDING_MODEL": "openai/text-embedding-3-large",
        "AGENT_PLATFORM_MODELS_FILE": str(models_path),
        "AGENT_PLATFORM_BROWSER_HEADLESS": "false",
        "AGENT_PLATFORM_BROWSER_TIMEOUT_MS": "15000",
        "AGENT_PLATFORM_BROWSER_NAVIGATION_TIMEOUT_MS": "15500",
        "AGENT_PLATFORM_BROWSER_NETWORK_IDLE_TIMEOUT_MS": "4000",
        "AGENT_PLATFORM_BROWSER_MAX_LINKS_PER_PAGE": "50",
        "AGENT_PLATFORM_BROWSER_LOCALE": "en-GB",
        "AGENT_PLATFORM_BROWSER_TIMEZONE_ID": "Europe/London",
        "AGENT_PLATFORM_BROWSER_VIEWPORT_WIDTH": "1600",
        "AGENT_PLATFORM_BROWSER_VIEWPORT_HEIGHT": "960",
        "AGENT_PLATFORM_BROWSER_USER_AGENT": "test-agent",
        "AGENT_PLATFORM_AGENT_RUN_TIMEOUT_SECONDS": "25",
        "AGENT_PLATFORM_DISABLE_COMPRESSION_TOOL": "true",
        "AGENT_PLATFORM_LOG_DIR": "var/logs",
    }

    settings = AppSettings()
    apply_environment(settings, environ)

    assert settings.openrouter.api_key == "key-123"
    assert settings.openrouter.embedding_model == "openai/text-embedding-3-large"
    assert settings.browser.headless is False
    assert settings.browser.default_timeout_ms == 15000
    assert settings.browser.navigation_timeout_ms == 15500
    assert settings.browser.network_idle_timeout_ms == 4000
    assert settings.browser.max_links_per_page == 50
    assert settings.browser.locale == "en-GB"
    assert settings.browser.timezone_id == "Europe/London"
    assert settings.browser.viewport_width == 1600
    assert settings.browser.viewport_height == 960
    assert settings.browser.user_agent == "test-agent"
    assert settings.runtime.agent_run_timeout_seconds == 25
    assert settings.debug.disable_compression_tool is True
    assert settings.logging.directory == Path("var/logs")
    assert [item.name for item in settings.models] == [
        "openai/gpt-4.1-mini",
        "openai/gpt-5.2",
    ]
    assert settings.models[1].is_default is True
