from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from agent_platform.config.settings import SessionSettings
from agent_platform.domain.models import ResearchSession


class SessionStore:
    def __init__(self, settings: SessionSettings) -> None:
        self._settings = settings
        self._settings.directory.mkdir(parents=True, exist_ok=True)
        self._settings.db_directory.mkdir(parents=True, exist_ok=True)
        self._settings.shared_db_path.parent.mkdir(parents=True, exist_ok=True)
        self._adapter = TypeAdapter(ResearchSession)

    def save(self, session: ResearchSession) -> Path:
        path = self.path(session.session_id)
        payload = self._adapter.dump_python(session, mode="json")
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def load(self, session_id: str) -> ResearchSession | None:
        path = self.path(session_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return self._adapter.validate_python(payload)

    def list_sessions(self) -> list[ResearchSession]:
        sessions: list[ResearchSession] = []
        for path in sorted(self._settings.directory.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            sessions.append(self._adapter.validate_python(payload))
        return sessions

    def find_by_normalized_name(self, normalized_name: str) -> ResearchSession | None:
        for session in self.list_sessions():
            if session.normalized_name == normalized_name:
                return session
        return None

    def path(self, session_id: str) -> Path:
        return self._settings.directory / f"{session_id}.json"
