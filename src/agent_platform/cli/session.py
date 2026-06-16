from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_platform.application.mission_service import MissionService
from agent_platform.config.loader import load_settings
from agent_platform.contracts.serialization import to_api_response
from agent_platform.domain.models import MissionRequest
from agent_platform.infrastructure.logging import configure_logging
from agent_platform.infrastructure.trace_store import TraceStore


@dataclass(slots=True)
class ChatDefaults:
    db_path: str
    preferred_model: str | None = None
    allowed_models: list[str] | None = None
    web_enabled: bool = True
    db_mutation_enabled: bool = True
    output_schema: dict[str, Any] | None = None


class ChatSession:
    def __init__(self, defaults: ChatDefaults) -> None:
        self._defaults = defaults
        self._settings = load_settings()
        configure_logging(self._settings.logging)
        self._service = MissionService(self._settings)
        self._trace_store = TraceStore(self._settings.traces)

    @property
    def defaults(self) -> ChatDefaults:
        return self._defaults

    def run_prompt(self, prompt: str) -> tuple[Any, Any]:
        request = MissionRequest(
            prompt=prompt,
            db_path=self._defaults.db_path,
            output_schema=self._defaults.output_schema,
            preferred_model=self._defaults.preferred_model,
            allowed_models=self._defaults.allowed_models,
            web_enabled=self._defaults.web_enabled,
            db_mutation_enabled=self._defaults.db_mutation_enabled,
        )
        result = self._service.run_sync(request)
        response = to_api_response(result)
        trace = self._trace_store.read_trace(result.trace_id)
        return response, trace


def load_output_schema(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    payload = Path(path).read_text(encoding="utf-8")
    return json.loads(payload)
