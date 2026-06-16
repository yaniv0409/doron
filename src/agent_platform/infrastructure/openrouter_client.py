from __future__ import annotations

from typing import Any

import httpx

from agent_platform.config.settings import OpenRouterSettings
from agent_platform.domain.exceptions import ConfigurationError, ModelError


class OpenRouterEmbeddingClient:
    def __init__(self, settings: OpenRouterSettings) -> None:
        self._settings = settings

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self._require_api_key()
        payload = {
            "input": texts,
            "model": self._settings.embedding_model,
        }
        headers = _build_headers(self._settings)
        async with httpx.AsyncClient(base_url=self._settings.base_url, timeout=30) as client:
            response = await client.post("/embeddings", json=payload, headers=headers)
        if response.is_error:
            raise ModelError(f"embedding request failed: {response.text}")
        data = response.json()
        return [item["embedding"] for item in data.get("data", [])]

    def _require_api_key(self) -> None:
        if not self._settings.api_key:
            raise ConfigurationError("OPENROUTER_API_KEY is not configured")


def _build_headers(settings: OpenRouterSettings) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
    }
    if settings.app_url:
        headers["HTTP-Referer"] = settings.app_url
    if settings.app_title:
        headers["X-OpenRouter-Title"] = settings.app_title
    return headers
