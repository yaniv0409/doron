from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from agent_platform.cli.api_client import MissionApiClient
from agent_platform.contracts.api import MissionRunRequest, MissionStreamEvent


@dataclass(slots=True)
class ChatDefaults:
    db_path: str
    api_url: str = "http://127.0.0.1:8000"
    preferred_model: str | None = None
    allowed_models: list[str] | None = None
    web_enabled: bool = True
    db_mutation_enabled: bool = True
    output_schema: dict[str, Any] | None = None
    start_server: bool = False
    server_ready_timeout_seconds: int = 20


class ChatSession:
    def __init__(self, defaults: ChatDefaults) -> None:
        self._defaults = defaults
        self._client = MissionApiClient(
            defaults.api_url,
            start_server=defaults.start_server,
            server_ready_timeout_seconds=defaults.server_ready_timeout_seconds,
        )

    @property
    def defaults(self) -> ChatDefaults:
        return self._defaults

    def close(self) -> None:
        self._client.close()

    def stream_prompt(self, prompt: str) -> Iterator[MissionStreamEvent]:
        request = self._build_request(prompt)
        yield from self._client.stream_mission(request)

    def _build_request(self, prompt: str) -> MissionRunRequest:
        return MissionRunRequest(
            prompt=prompt,
            db_path=self._defaults.db_path,
            output_schema=self._defaults.output_schema,
            preferred_model=self._defaults.preferred_model,
            allowed_models=self._defaults.allowed_models,
            web_enabled=self._defaults.web_enabled,
            db_mutation_enabled=self._defaults.db_mutation_enabled,
            stream=True,
        )


def load_output_schema(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    payload = Path(path).read_text(encoding="utf-8")
    return json.loads(payload)
