from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Callable

from agent_platform.application.mission_service import MissionService
from agent_platform.config.settings import AppSettings
from agent_platform.contracts.session import SessionChatRequest, SessionOpenRequest, SessionUpdateRequest
from agent_platform.domain.models import ExecutionTrace, MissionError, MissionRequest, ResearchSession, SessionSummary, SessionTurn, utc_now
from agent_platform.infrastructure.kuzu_client import KuzuGateway
from agent_platform.infrastructure.session_store import SessionStore


class SessionService:
    def __init__(self, settings: AppSettings, mission_service: MissionService, store: SessionStore) -> None:
        self._settings = settings
        self._mission_service = mission_service
        self._store = store

    def open(self, request: SessionOpenRequest) -> ResearchSession:
        normalized_name = normalize_session_name(request.name)
        existing = self._store.find_by_normalized_name(normalized_name)
        if existing is not None:
            return existing

        session_id = str(uuid.uuid4())
        db_path = self._resolve_db_path(request.name, session_id, request.use_dedicated_db)
        _ensure_db_path(db_path)
        now = utc_now()
        session = ResearchSession(
            session_id=session_id,
            name=request.name.strip(),
            normalized_name=normalized_name,
            uses_dedicated_db=request.use_dedicated_db,
            db_path=db_path,
            preferred_model=request.preferred_model,
            allowed_models=request.allowed_models,
            output_schema=request.output_schema,
            web_enabled=request.web_enabled,
            db_mutation_enabled=request.db_mutation_enabled,
            web_tool_call_limit=request.web_tool_call_limit,
            created_at=now,
            updated_at=now,
        )
        self._store.save(session)
        return session

    def list_sessions(self) -> list[ResearchSession]:
        return sorted(self._store.list_sessions(), key=lambda item: item.updated_at, reverse=True)

    def get(self, session_id: str) -> ResearchSession | None:
        return self._store.load(session_id)

    def update(self, session_id: str, request: SessionUpdateRequest) -> ResearchSession:
        session = self._require(session_id)
        if request.preferred_model is not None:
            session.preferred_model = request.preferred_model
        if request.allowed_models is not None:
            session.allowed_models = request.allowed_models
        if request.output_schema is not None:
            session.output_schema = request.output_schema
        if request.web_enabled is not None:
            session.web_enabled = request.web_enabled
        if request.db_mutation_enabled is not None:
            session.db_mutation_enabled = request.db_mutation_enabled
        session.web_tool_call_limit = request.web_tool_call_limit
        session.updated_at = utc_now()
        self._store.save(session)
        return session

    async def run_chat(
        self,
        session_id: str,
        request: SessionChatRequest,
        *,
        event_hook: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[ResearchSession, str, str, MissionError | None, int | None]:
        session = self._require(session_id)
        message_id = str(uuid.uuid4())
        user_turn = SessionTurn(
            message_id=message_id,
            role="user",
            content=request.message.strip(),
            web_tool_call_limit_used=self._resolve_web_tool_limit(session, request),
        )
        session.turns.append(user_turn)
        session.updated_at = utc_now()
        self._store.save(session)
        if event_hook is not None:
            event_hook(
                {
                    "event": "session.started",
                    "data": {
                        "session_id": session.session_id,
                        "message_id": message_id,
                        "name": session.name,
                        "created_at": utc_now().isoformat(),
                    },
                }
            )

        def wrapped_event_hook(event: dict[str, Any]) -> None:
            if event_hook is None:
                return
            payload = dict(event)
            payload["data"] = {
                **event.get("data", {}),
                "session_id": session.session_id,
                "message_id": message_id,
            }
            event_hook(payload)

        mission_request = self._build_mission_request(session, request)
        result = await self._mission_service.run(
            mission_request,
            event_hook=wrapped_event_hook if event_hook is not None else None,
        )
        assistant_message = _stringify_result(result.result if result.error is None else result.error.message)
        session.turns.append(
            SessionTurn(
                message_id=str(uuid.uuid4()),
                role="assistant",
                content=assistant_message,
                trace_id=result.trace_id,
                status=result.status.value,
                web_tool_call_limit_used=mission_request.web_tool_call_limit,
            )
        )
        session.last_error = result.error
        session.updated_at = utc_now()
        trace = self._read_trace(result.trace_id)
        session.summary = self._build_summary(trace, session)
        self._store.save(session)
        if event_hook is not None:
            event_hook(
                {
                    "event": "session.message.completed" if result.error is None else "session.message.failed",
                    "data": {
                        "session_id": session.session_id,
                        "message_id": message_id,
                        "trace_id": result.trace_id,
                        "assistant_message": assistant_message,
                        "status": result.status.value,
                        "web_tool_call_limit_used": mission_request.web_tool_call_limit,
                        "error": _error_payload(result.error),
                        "created_at": utc_now().isoformat(),
                    },
                }
            )
            event_hook(
                {
                    "event": "session.graph.updated",
                    "data": {
                        "session_id": session.session_id,
                        "message_id": message_id,
                        "trace_id": result.trace_id,
                        "created_at": utc_now().isoformat(),
                    },
                }
            )
        return session, result.trace_id, assistant_message, result.error, mission_request.web_tool_call_limit

    def _build_mission_request(self, session: ResearchSession, request: SessionChatRequest) -> MissionRequest:
        return MissionRequest(
            prompt=self._build_prompt(session, request.message.strip()),
            db_path=session.db_path,
            output_schema=request.output_schema if request.output_schema is not None else session.output_schema,
            preferred_model=request.preferred_model if request.preferred_model is not None else session.preferred_model,
            allowed_models=request.allowed_models if request.allowed_models is not None else session.allowed_models,
            mission_metadata={"session_id": session.session_id, "session_name": session.name},
            web_enabled=session.web_enabled if request.web_enabled is None else request.web_enabled,
            db_mutation_enabled=(
                session.db_mutation_enabled if request.db_mutation_enabled is None else request.db_mutation_enabled
            ),
            web_tool_call_limit=self._resolve_web_tool_limit(session, request),
        )

    def _build_prompt(self, session: ResearchSession, message: str) -> str:
        lines = [
            f"You are continuing a research session named {session.name}.",
            "Build on prior findings and keep the work cumulative.",
        ]
        if session.summary.notes:
            lines.append("Session notes:")
            lines.extend(f"- {item}" for item in session.summary.notes)
        if session.summary.recent_tools:
            lines.append("Recent tool outcomes:")
            lines.extend(f"- {item}" for item in session.summary.recent_tools)
        if session.summary.compression_notice:
            lines.append(f"Compression notice: {session.summary.compression_notice}")
        recent_turns = session.turns[-self._settings.sessions.history_turn_limit :]
        if recent_turns:
            lines.append("Recent conversation:")
            lines.extend(f"{turn.role.title()}: {turn.content}" for turn in recent_turns)
        lines.append(f"User: {message}")
        return "\n".join(lines)

    def _build_summary(self, trace: ExecutionTrace | None, session: ResearchSession) -> SessionSummary:
        if trace is None:
            return SessionSummary(last_trace_id=session.summary.last_trace_id)
        recent_tools = [
            f"{item.name}: {item.result_summary}"
            for item in trace.tool_calls[-self._settings.sessions.summary_tool_limit :]
        ]
        notes = [turn.content[:300] for turn in session.turns if turn.role == "assistant"][-4:]
        compression_notice = trace.compression_events[-1].preview if trace.compression_events else None
        return SessionSummary(
            notes=notes,
            recent_tools=recent_tools,
            compression_notice=compression_notice,
            last_trace_id=trace.trace_id,
        )

    def _read_trace(self, trace_id: str) -> ExecutionTrace | None:
        try:
            return self._mission_service.trace_store.read_trace(trace_id)
        except Exception:
            return None

    def _resolve_web_tool_limit(self, session: ResearchSession, request: SessionChatRequest) -> int | None:
        if request.web_tool_call_limit is not None:
            return request.web_tool_call_limit
        if session.web_tool_call_limit is not None:
            return session.web_tool_call_limit
        return self._settings.browser.web_tool_call_budget

    def _require(self, session_id: str) -> ResearchSession:
        session = self._store.load(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        return session

    def _resolve_db_path(self, name: str, session_id: str, use_dedicated_db: bool) -> str:
        if not use_dedicated_db:
            return str(self._settings.sessions.shared_db_path)
        slug = slugify_session_name(name)
        if not slug:
            slug = "session"
        return str(self._settings.sessions.db_directory / f"{slug}-{session_id[:8]}.kuzu")


def normalize_session_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def slugify_session_name(name: str) -> str:
    value = normalize_session_name(name)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def _ensure_db_path(db_path: str) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    KuzuGateway(str(path))


def _stringify_result(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    return json.dumps(result, indent=2, ensure_ascii=False)


def _error_payload(error: MissionError | None) -> dict[str, Any] | None:
    if error is None:
        return None
    return error.model_dump(mode="json")
