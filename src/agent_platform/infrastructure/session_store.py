from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from agent_platform.config.settings import SessionSettings
from agent_platform.domain.models import ResearchSession, ResearchSessionSummary


class SessionStore:
    def __init__(self, settings: SessionSettings) -> None:
        self._settings = settings
        self._settings.directory.mkdir(parents=True, exist_ok=True)
        self._settings.db_directory.mkdir(parents=True, exist_ok=True)
        self._settings.shared_db_path.parent.mkdir(parents=True, exist_ok=True)
        self._adapter = TypeAdapter(ResearchSession)
        self._summary_adapter = TypeAdapter(ResearchSessionSummary)

    def save(self, session: ResearchSession) -> Path:
        path = self.path(session.session_id)
        payload = self._adapter.dump_python(session, mode="json")
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.summary_path(session.session_id).write_text(
            json.dumps(self._summary_adapter.dump_python(_build_summary(session), mode="json"), indent=2),
            encoding="utf-8",
        )
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
            if path.name.endswith(".context.json") or path.name.endswith(".summary.json"):
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            sessions.append(self._adapter.validate_python(payload))
        return sessions

    def list_summaries(self) -> list[ResearchSessionSummary]:
        summaries: dict[str, ResearchSessionSummary] = {}
        for path in sorted(self._settings.directory.glob("*.summary.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            summary = self._summary_adapter.validate_python(payload)
            summaries[summary.session_id] = summary

        for session in self.list_sessions():
            if session.session_id in summaries:
                continue
            summary = _build_summary(session)
            self.summary_path(session.session_id).write_text(
                json.dumps(self._summary_adapter.dump_python(summary, mode="json"), indent=2),
                encoding="utf-8",
            )
            summaries[session.session_id] = summary
        return sorted(summaries.values(), key=lambda item: item.updated_at, reverse=True)

    def find_by_normalized_name(self, normalized_name: str, session_group_id: str | None = None) -> ResearchSession | None:
        for summary in self.list_summaries():
            if summary.normalized_name != normalized_name:
                continue
            if summary.session_group_id != session_group_id:
                continue
            session = self.load(summary.session_id)
            if session is not None:
                return session
        return None

    def find_by_group_id(self, session_group_id: str) -> ResearchSession | None:
        for summary in self.list_summaries():
            if summary.session_group_id != session_group_id:
                continue
            session = self.load(summary.session_id)
            if session is not None:
                return session
        return None

    def path(self, session_id: str) -> Path:
        return self._settings.directory / f"{session_id}.json"

    def summary_path(self, session_id: str) -> Path:
        return self._settings.directory / f"{session_id}.summary.json"


def _build_summary(session: ResearchSession) -> ResearchSessionSummary:
    return ResearchSessionSummary(
        session_id=session.session_id,
        name=session.name,
        normalized_name=session.normalized_name,
        session_group_id=session.session_group_id,
        session_group_name=session.session_group_name,
        uses_dedicated_db=session.uses_dedicated_db,
        db_path=session.db_path,
        web_tool_call_limit=session.web_tool_call_limit,
        created_at=session.created_at,
        updated_at=session.updated_at,
        last_trace_id=session.summary.last_trace_id,
        stop_mode=session.stop_mode,
        stop_reason=session.stop_reason,
        stop_requested_at=session.stop_requested_at,
        stopped_at=session.stopped_at,
        is_closed=session.is_closed,
    )
