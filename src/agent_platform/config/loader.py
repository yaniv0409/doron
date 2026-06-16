from __future__ import annotations

import os

from dotenv import load_dotenv

from agent_platform.config.settings import AppSettings


def load_settings() -> AppSettings:
    load_dotenv()
    settings = AppSettings()
    api_key = os.getenv("OPENROUTER_API_KEY")
    if api_key:
        settings.openrouter.api_key = api_key
    app_url = os.getenv("OPENROUTER_APP_URL")
    if app_url:
        settings.openrouter.app_url = app_url
    app_title = os.getenv("OPENROUTER_APP_TITLE")
    if app_title:
        settings.openrouter.app_title = app_title
    return settings
