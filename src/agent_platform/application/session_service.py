from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent_platform.application.context_compression import ContextCompressor
from agent_platform.application.mission_service import MissionService
from agent_platform.config.settings import AppSettings
from agent_platform.contracts.session import (
    SessionChatRequest,
    SessionForkRequest,
    SessionOpenRequest,
    SessionSteerRequest,
    SessionStopRequest,
    SessionUpdateRequest,
)
from agent_platform.domain.enums import ResultFormat, SessionStopMode
from agent_platform.domain.exceptions import RequestValidationError
from agent_platform.domain.models import (
    CompletionMetadata,
    CompressedMemory,
    ExecutionTrace,
    MissionError,
    MissionRequest,
    ModelDescriptor,
    ResearchSession,
    ResearchSessionSummary,
    RuntimeContext,
    SessionAgentContext,
    SessionSummary,
    SessionTurn,
    utc_now,
)
from agent_platform.infrastructure.model_catalog import ModelCatalog
from agent_platform.infrastructure.kuzu_client import KuzuGateway
from agent_platform.infrastructure.session_context_store import SessionContextStore
from agent_platform.infrastructure.session_store import SessionStore


@dataclass(slots=True)
class _SessionCompressionRuntime:
    context: RuntimeContext
    services: Any


@dataclass(slots=True)
class _ActiveSessionRun:
    task: asyncio.Task[Any]
    message_id: str
    pending_steers: list[SessionTurn] = field(default_factory=list)
    restart_requested: bool = False
    event_hook: Callable[[dict[str, Any]], None] | None = None


class SessionService:
    def __init__(
        self,
        settings: AppSettings,
        mission_service: MissionService,
        store: SessionStore,
        context_store: SessionContextStore | None = None,
    ) -> None:
        self._settings = settings
        self._mission_service = mission_service
        self._store = store
        self._context_store = context_store or SessionContextStore(settings.sessions)
        self._model_catalog = ModelCatalog(settings)
        self._context_compressor = ContextCompressor(settings.compression)
        self._active_runs: dict[str, _ActiveSessionRun] = {}

    def open(self, request: SessionOpenRequest) -> ResearchSession:
        normalized_name = normalize_session_name(request.name)
        existing = self._store.find_by_normalized_name(normalized_name, request.session_group_id)
        if existing is not None:
            if self._context_store.load(existing.session_id) is None:
                self._context_store.save(
                    SessionAgentContext(
                        session_id=existing.session_id,
                        active_turns=list(existing.turns),
                        current_mission_message_id=self._latest_user_message_id(existing.turns),
                    )
                )
            return existing

        group_session = self._resolve_group_session(request.session_group_id)
        session_id = str(uuid.uuid4())
        db_path = group_session.db_path if group_session is not None else self._resolve_db_path(
            request.name,
            session_id,
            request.use_dedicated_db,
        )
        _ensure_db_path(db_path)
        now = utc_now()
        session = ResearchSession(
            session_id=session_id,
            name=request.name.strip(),
            normalized_name=normalized_name,
            session_group_id=group_session.session_group_id if group_session is not None else None,
            session_group_name=group_session.session_group_name if group_session is not None else None,
            uses_dedicated_db=group_session.uses_dedicated_db if group_session is not None else request.use_dedicated_db,
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
        self._context_store.save(
            SessionAgentContext(
                session_id=session.session_id,
                active_turns=[],
                current_mission_message_id=None,
            )
        )
        return session

    def fork(self, session_id: str, request: SessionForkRequest) -> ResearchSession:
        source = self._require(session_id)
        now = utc_now()
        group_id = source.session_group_id
        group_name = source.session_group_name
        if group_id is None:
            group_id = str(uuid.uuid4())
            group_name = (request.group_name or source.name).strip()
            source.session_group_id = group_id
            source.session_group_name = group_name
            source.updated_at = now
            self._store.save(source)
        forked_session_id = str(uuid.uuid4())
        forked = ResearchSession(
            session_id=forked_session_id,
            name=request.name.strip(),
            normalized_name=normalize_session_name(request.name),
            session_group_id=group_id,
            session_group_name=group_name,
            uses_dedicated_db=source.uses_dedicated_db,
            db_path=source.db_path,
            preferred_model=source.preferred_model if request.inherit_model_settings else None,
            allowed_models=list(source.allowed_models) if request.inherit_model_settings and source.allowed_models else None,
            output_schema=dict(source.output_schema) if request.inherit_output_schema and source.output_schema else None,
            web_enabled=source.web_enabled if request.inherit_runtime_settings else True,
            db_mutation_enabled=source.db_mutation_enabled if request.inherit_runtime_settings else True,
            web_tool_call_limit=source.web_tool_call_limit if request.inherit_runtime_settings else None,
            created_at=now,
            updated_at=now,
        )
        self._store.save(forked)
        if request.inherit_context:
            source_context = self._load_context(source)
            forked.turns = [turn.model_copy(deep=True) for turn in source.turns]
            self._store.save(forked)
            forked_context = source_context.model_copy(deep=True)
            forked_context.session_id = forked.session_id
            self._context_store.save(forked_context)
        else:
            self._context_store.save(
                SessionAgentContext(
                    session_id=forked.session_id,
                    active_turns=[],
                    current_mission_message_id=None,
                )
            )
        return forked

    def list_sessions(self) -> list[ResearchSession]:
        return sorted(self._store.list_sessions(), key=lambda item: item.updated_at, reverse=True)

    def list_session_summaries(self) -> list[ResearchSessionSummary]:
        return self._store.list_summaries()

    def get(self, session_id: str) -> ResearchSession | None:
        return self._store.load(session_id)

    def get_turn_page(
        self,
        session_id: str,
        *,
        limit: int,
        before_message_id: str | None = None,
    ) -> tuple[ResearchSession, list[SessionTurn], bool]:
        session = self._require(session_id)
        turns, has_more = self._slice_turns(session.turns, limit=limit, before_message_id=before_message_id)
        return session, turns, has_more

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

    def stop(self, session_id: str, request: SessionStopRequest) -> ResearchSession:
        session = self._require(session_id)
        now = utc_now()
        session.stop_mode = request.mode
        session.stop_reason = request.reason
        session.stop_requested_at = now
        if request.mode == SessionStopMode.HARD:
            session.is_closed = True
            session.stopped_at = now
            session.last_error = MissionError(
                code="cancelled",
                message=request.reason or "session hard-stopped",
            )
            active_run = self._active_runs.get(session_id)
            if active_run is not None and not active_run.task.done():
                active_run.task.cancel()
        session.updated_at = now
        self._store.save(session)
        return session

    def resume(self, session_id: str) -> ResearchSession:
        session = self._require(session_id)
        session.stop_mode = None
        session.stop_reason = None
        session.stop_requested_at = None
        session.stopped_at = None
        session.is_closed = False
        session.last_error = None
        session.updated_at = utc_now()
        self._store.save(session)
        return session

    def steer(self, session_id: str, request: SessionSteerRequest) -> ResearchSession:
        session = self._require(session_id)
        if session.is_closed:
            raise RequestValidationError("session is hard-stopped; resume it before steering")
        active_run = self._active_runs.get(session_id)
        if active_run is None or active_run.task.done():
            raise RequestValidationError("session is not actively running")
        steer_turn = SessionTurn(
            message_id=str(uuid.uuid4()),
            role="steer",
            content=request.message.strip(),
        )
        session.turns.append(steer_turn)
        session.updated_at = utc_now()
        self._store.save(session)
        context_state = self._load_context(session)
        context_state.active_turns.append(steer_turn)
        self._context_store.save(context_state)
        active_run.pending_steers.append(steer_turn)
        active_run.restart_requested = True
        if active_run.event_hook is not None:
            active_run.event_hook(
                {
                    "event": "session.steered",
                    "data": {
                        "session_id": session.session_id,
                        "message_id": active_run.message_id,
                        "steer_message_id": steer_turn.message_id,
                        "message": steer_turn.content,
                        "created_at": steer_turn.created_at.isoformat(),
                    },
                }
            )
        active_run.task.cancel()
        return session

    async def run_chat(
        self,
        session_id: str,
        request: SessionChatRequest,
        *,
        event_hook: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[ResearchSession, str, str, MissionError | None, int | None, CompletionMetadata | None, ResultFormat]:
        session = self._require(session_id)
        if session.is_closed:
            raise RequestValidationError("session is hard-stopped; resume it before sending more messages")
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
        context_state = self._load_context(session)
        context_state.active_turns.append(user_turn)
        context_state.current_mission_message_id = user_turn.message_id
        self._context_store.save(context_state)
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

        task = asyncio.current_task()
        if task is not None:
            self._active_runs[session_id] = _ActiveSessionRun(task=task, message_id=message_id, event_hook=event_hook)
        try:
            while True:
                mission_request = self._build_mission_request(session, request, context_state)
                context_state = await self._compact_context_if_needed(session, mission_request, context_state)
                self._context_store.save(context_state)
                mission_request = self._build_mission_request(session, request, context_state)
                try:
                    result = await self._mission_service.run(
                        mission_request,
                        event_hook=wrapped_event_hook if event_hook is not None else None,
                    )
                except asyncio.CancelledError:
                    active_run = self._active_runs.get(session_id)
                    if active_run is not None and active_run.restart_requested:
                        active_run.restart_requested = False
                        if task is not None:
                            task.uncancel()
                        session = self._require(session_id)
                        context_state = self._load_context(session)
                        if event_hook is not None:
                            event_hook(
                                {
                                    "event": "session.restarted",
                                    "data": {
                                        "session_id": session.session_id,
                                        "message_id": message_id,
                                        "steer_count": len(active_run.pending_steers),
                                        "created_at": utc_now().isoformat(),
                                    },
                                }
                            )
                        active_run.pending_steers.clear()
                        continue
                    raise
                assistant_message = _stringify_result(result.result if result.error is None else result.error.message)
                assistant_turn = SessionTurn(
                    message_id=str(uuid.uuid4()),
                    role="assistant",
                    content=assistant_message,
                    trace_id=result.trace_id,
                    status=result.status.value,
                    result_format=result.result_format,
                    web_tool_call_limit_used=mission_request.web_tool_call_limit,
                    completion=result.completion,
                )
                session.turns.append(assistant_turn)
                context_state.active_turns.append(assistant_turn)
                session.last_error = result.error
                session.updated_at = utc_now()
                trace = self._read_trace(result.trace_id)
                session.summary = self._build_summary(trace, session)
                if context_state.compression_notice:
                    session.summary.compression_notice = context_state.compression_notice
                self._store.save(session)
                context_state = await self._compact_context_if_needed(session, mission_request, context_state)
                if context_state.compression_notice:
                    session.summary.compression_notice = context_state.compression_notice
                    self._store.save(session)
                self._context_store.save(context_state)
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
                                "result_format": result.result_format.value,
                                "web_tool_call_limit_used": mission_request.web_tool_call_limit,
                                "completion": result.completion.model_dump(mode="json") if result.completion else None,
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
                return (
                    session,
                    result.trace_id,
                    assistant_message,
                    result.error,
                    mission_request.web_tool_call_limit,
                    result.completion,
                    result.result_format,
                )
        except asyncio.CancelledError:
            error = MissionError(code="cancelled", message=session.stop_reason or "session cancelled")
            session.last_error = error
            session.updated_at = utc_now()
            self._store.save(session)
            cancelled_trace_id = f"cancelled-{session.session_id}-{message_id}"
            if event_hook is not None:
                event_hook(
                    {
                        "event": "session.message.failed",
                        "data": {
                            "session_id": session.session_id,
                            "message_id": message_id,
                            "trace_id": cancelled_trace_id,
                            "assistant_message": session.stop_reason or "Session stopped.",
                            "status": "failed",
                            "result_format": ResultFormat.TEXT.value,
                            "web_tool_call_limit_used": mission_request.web_tool_call_limit,
                            "completion": None,
                            "error": _error_payload(error),
                            "created_at": utc_now().isoformat(),
                        },
                    }
                )
            return (
                session,
                cancelled_trace_id,
                session.stop_reason or "Session stopped.",
                error,
                mission_request.web_tool_call_limit,
                None,
                ResultFormat.TEXT,
            )
        finally:
            active_run = self._active_runs.get(session_id)
            if task is not None and active_run is not None and active_run.task is task:
                self._active_runs.pop(session_id, None)

    def _build_mission_request(
        self,
        session: ResearchSession,
        request: SessionChatRequest,
        context_state: SessionAgentContext,
    ) -> MissionRequest:
        output_schema = request.output_schema if request.output_schema is not None else session.output_schema
        return MissionRequest(
            prompt=self._build_prompt(session, context_state, request.message.strip(), output_schema=output_schema),
            db_path=session.db_path,
            output_schema=output_schema,
            preferred_model=request.preferred_model if request.preferred_model is not None else session.preferred_model,
            allowed_models=request.allowed_models if request.allowed_models is not None else session.allowed_models,
            mission_metadata={"session_id": session.session_id, "session_name": session.name},
            web_enabled=session.web_enabled if request.web_enabled is None else request.web_enabled,
            db_mutation_enabled=(
                session.db_mutation_enabled if request.db_mutation_enabled is None else request.db_mutation_enabled
            ),
            web_tool_call_limit=self._resolve_web_tool_limit(session, request),
        )

    def _build_prompt(
        self,
        session: ResearchSession,
        context_state: SessionAgentContext,
        message: str,
        *,
        output_schema: dict[str, Any] | None,
    ) -> str:
        current_message_id = context_state.current_mission_message_id
        historical_turns = [turn for turn in context_state.active_turns if turn.message_id != current_message_id]
        steer_turns = [turn for turn in historical_turns if turn.role == "steer"]
        historical_turns = [turn for turn in historical_turns if turn.role != "steer"]
        lines = [
            f"You are continuing a research session named {session.name}.",
            "Treat the newest user message as the current primary mission.",
            "Use prior context only as supporting background.",
            "Current mission:",
            message,
        ]
        if steer_turns:
            lines.append("Live steering updates:")
            lines.extend(f"- {turn.content}" for turn in steer_turns)
        if session.stop_mode is not None:
            lines.append("Session stop request:")
            if session.stop_mode == SessionStopMode.SOFT:
                lines.append(
                    "Wrap up the mission, summarize progress, and avoid starting new investigative branches unless needed to finish."
                )
                if session.stop_reason:
                    lines.append(f"Stop note: {session.stop_reason}")
            else:
                lines.append("This session has been hard-stopped and should not accept more work until it is resumed.")
        if output_schema is None:
            lines.append("Final answer format: markdown.")
        if context_state.compression_notice:
            lines.append(f"Compaction notice: {context_state.compression_notice}")
        lines.extend(self._build_compacted_context_lines(context_state.compressed_memory))
        if historical_turns:
            lines.append("Recent active turns:")
            lines.extend(f"{turn.role.title()}: {turn.content}" for turn in historical_turns)
        return "\n".join(lines)

    def _build_compacted_context_lines(self, compressed: CompressedMemory | None) -> list[str]:
        if compressed is None:
            return []
        lines: list[str] = []
        if compressed.notes:
            lines.append("Compacted notes:")
            lines.extend(f"- {item}" for item in compressed.notes)
        if compressed.db_findings:
            lines.append("Compacted database findings:")
            lines.extend(f"- {item}" for item in compressed.db_findings)
        if compressed.web_findings:
            lines.append("Compacted web findings:")
            lines.extend(f"- {item}" for item in compressed.web_findings)
        if compressed.tool_summaries:
            lines.append("Compacted tool summaries:")
            lines.extend(f"- {item}" for item in compressed.tool_summaries)
        if compressed.unresolved_goals:
            lines.append("Open threads from earlier turns:")
            lines.extend(f"- {item}" for item in compressed.unresolved_goals)
        return lines

    async def _compact_context_if_needed(
        self,
        session: ResearchSession,
        mission_request: MissionRequest,
        context_state: SessionAgentContext,
    ) -> SessionAgentContext:
        model = self._select_model(mission_request)
        budget = self._context_budget(model)
        keep_turns = max(1, self._settings.sessions.active_context_turn_limit)
        while self._estimate_context_size(session, context_state) > budget and len(context_state.active_turns) > keep_turns:
            compacted_turns = context_state.active_turns[:-keep_turns]
            kept_turns = context_state.active_turns[-keep_turns:]
            try:
                compressed = await self._compress_context_state(mission_request, context_state, compacted_turns, model)
            except Exception:
                break
            context_state.compressed_memory = compressed
            context_state.compression_notice = (
                compressed.notice
                or f"Session context was compacted for {model.name}."
            )
            context_state.active_turns = kept_turns
            context_state.historical_turn_count += len(compacted_turns)
            context_state.last_compaction_size = self._estimate_context_size(session, context_state)
            context_state.last_compacted_at = utc_now()
        return context_state

    async def _compress_context_state(
        self,
        mission_request: MissionRequest,
        context_state: SessionAgentContext,
        compacted_turns: list[SessionTurn],
        model: ModelDescriptor,
    ) -> CompressedMemory:
        runtime = self._build_session_compression_runtime(
            mission_request,
            context_state,
            compacted_turns,
            model,
        )
        result = await self._context_compressor.compress(
            runtime,
            trigger="session_context",
            reason="session context exceeded model budget",
        )
        if not result.ok or runtime.context.compressed_memory is None:
            raise ValueError(result.error_message or "session context compression failed")
        return runtime.context.compressed_memory

    def _build_session_compression_runtime(
        self,
        mission_request: MissionRequest,
        context_state: SessionAgentContext,
        compacted_turns: list[SessionTurn],
        model: ModelDescriptor,
    ) -> _SessionCompressionRuntime:
        allowed_models = self._model_catalog.resolve_allowed(mission_request)
        compression_request = MissionRequest(
            prompt=self._current_mission_text(context_state, mission_request.prompt),
            db_path=mission_request.db_path,
            output_schema=mission_request.output_schema,
            preferred_model=model.name,
            allowed_models=[item.name for item in allowed_models],
            mission_metadata=mission_request.mission_metadata,
            web_enabled=mission_request.web_enabled,
            db_mutation_enabled=mission_request.db_mutation_enabled,
            web_tool_call_limit=mission_request.web_tool_call_limit,
        )
        context = RuntimeContext(
            trace_id=f"session-context-{context_state.session_id}",
            mission_request=compression_request,
            started_at=utc_now(),
            current_model=model,
            allowed_models=allowed_models,
            reasoning_notes=self._seed_reasoning_notes(context_state, compacted_turns),
            db_findings=list(context_state.compressed_memory.db_findings if context_state.compressed_memory else []),
            web_findings=list(context_state.compressed_memory.web_findings if context_state.compressed_memory else []),
            tool_summaries=self._seed_tool_summaries(context_state),
            web_tool_call_budget=mission_request.web_tool_call_limit or self._settings.browser.web_tool_call_budget,
        )
        services = self._mission_service._runtime_builder.services
        return _SessionCompressionRuntime(context=context, services=services)

    def _seed_reasoning_notes(
        self,
        context_state: SessionAgentContext,
        compacted_turns: list[SessionTurn],
    ) -> list[str]:
        notes = list(context_state.compressed_memory.notes if context_state.compressed_memory else [])
        notes.extend(f"{turn.role.title()}: {turn.content}" for turn in compacted_turns)
        return notes

    def _seed_tool_summaries(self, context_state: SessionAgentContext) -> list[str]:
        summaries = list(context_state.compressed_memory.tool_summaries if context_state.compressed_memory else [])
        if context_state.compressed_memory is not None:
            summaries.extend(f"unresolved: {item}" for item in context_state.compressed_memory.unresolved_goals)
        return summaries

    def _load_context(self, session: ResearchSession) -> SessionAgentContext:
        context_state = self._context_store.load(session.session_id)
        if context_state is not None:
            return context_state
        active_turns = list(session.turns)
        context_state = SessionAgentContext(
            session_id=session.session_id,
            active_turns=active_turns,
            current_mission_message_id=self._latest_user_message_id(active_turns),
        )
        self._context_store.save(context_state)
        return context_state

    def _resolve_group_session(self, session_group_id: str | None) -> ResearchSession | None:
        if session_group_id is None:
            return None
        session = self._store.find_by_group_id(session_group_id)
        if session is None:
            raise RequestValidationError("session group not found")
        return session

    def _latest_user_message_id(self, turns: list[SessionTurn]) -> str | None:
        for turn in reversed(turns):
            if turn.role == "user":
                return turn.message_id
        return None

    def _current_mission_text(self, context_state: SessionAgentContext, fallback: str) -> str:
        current_message_id = context_state.current_mission_message_id
        if current_message_id is None:
            return fallback
        for turn in reversed(context_state.active_turns):
            if turn.message_id == current_message_id:
                return turn.content
        return fallback

    def _select_model(self, mission_request: MissionRequest) -> ModelDescriptor:
        return self._model_catalog.choose_initial(mission_request)

    def _context_budget(self, model: ModelDescriptor) -> int:
        if model.context_window is None:
            return self._settings.compression.fallback_budget_chars
        return max(
            int(model.context_window * self._settings.compression.threshold_ratio),
            self._settings.compression.fallback_budget_chars,
        )

    def _estimate_context_size(self, session: ResearchSession, context_state: SessionAgentContext) -> int:
        prompt = self._build_prompt(
            session,
            context_state,
            self._current_mission_text(context_state, ""),
            output_schema=session.output_schema,
        )
        return len(prompt)

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

    def _slice_turns(
        self,
        turns: list[SessionTurn],
        *,
        limit: int,
        before_message_id: str | None = None,
    ) -> tuple[list[SessionTurn], bool]:
        if limit <= 0:
            limit = len(turns) or 0
        end_index = len(turns)
        if before_message_id is not None:
            index = self._find_turn_index(turns, before_message_id)
            if index is not None:
                end_index = index
        start_index = max(0, end_index - limit) if limit > 0 else 0
        page = turns[start_index:end_index]
        has_more = start_index > 0
        return page, has_more

    def _find_turn_index(self, turns: list[SessionTurn], message_id: str) -> int | None:
        for index, turn in enumerate(turns):
            if turn.message_id == message_id:
                return index
        return None

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
