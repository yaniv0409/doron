from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from agent_platform.config.settings import SessionSettings
from agent_platform.domain.models import SessionAgentContext


class SessionContextStore:
    def __init__(self, settings: SessionSettings) -> None:
        self._settings = settings
        self._settings.directory.mkdir(parents=True, exist_ok=True)
        self._adapter = TypeAdapter(SessionAgentContext)

    def save(self, context: SessionAgentContext) -> Path:
        path = self.path(context.session_id)
        payload = self._adapter.dump_python(context, mode="json")
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def load(self, session_id: str) -> SessionAgentContext | None:
        path = self.path(session_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return self._adapter.validate_python(payload)

    def path(self, session_id: str) -> Path:
        return self._settings.directory / f"{session_id}.context.json"
