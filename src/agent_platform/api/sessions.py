from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from agent_platform.application.graph_snapshot_service import GraphSnapshotService
from agent_platform.application.session_service import SessionService
from agent_platform.contracts.api import MissionRunError
from agent_platform.contracts.session import (
    SessionChatRequest,
    SessionChatResponse,
    SessionDetailResponse,
    SessionGraphResponse,
    SessionStopRequest,
    SessionSummaryResponse,
    SessionOpenRequest,
    SessionTurnResponse,
    SessionTurnPageResponse,
    SessionUpdateRequest,
)
from agent_platform.domain.models import ResearchSession, ResearchSessionSummary, utc_now

router = APIRouter(tags=["sessions"])


@router.get("/sessions", response_model=list[SessionSummaryResponse])
async def list_sessions(http_request: Request) -> list[SessionSummaryResponse]:
    service: SessionService = http_request.app.state.session_service
    return [_session_summary(item) for item in service.list_session_summaries()]


@router.post("/sessions/open", response_model=SessionDetailResponse)
async def open_session(request: SessionOpenRequest, http_request: Request) -> SessionDetailResponse:
    service: SessionService = http_request.app.state.session_service
    return _session_detail(service.open(request), turn_limit=12)


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session(
    session_id: str,
    http_request: Request,
    turn_limit: int = Query(default=12, ge=0),
) -> SessionDetailResponse:
    service: SessionService = http_request.app.state.session_service
    session = service.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "session not found"})
    return _session_detail(session, turn_limit=turn_limit)


@router.get("/sessions/{session_id}/turns", response_model=SessionTurnPageResponse)
async def get_session_turns(
    session_id: str,
    http_request: Request,
    limit: int = Query(default=12, ge=0),
    before: str | None = None,
) -> SessionTurnPageResponse:
    service: SessionService = http_request.app.state.session_service
    try:
        session, turns, has_more = service.get_turn_page(session_id, limit=limit, before_message_id=before)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": str(exc)}) from exc
    return _turn_page(session, turns, has_more)


@router.patch("/sessions/{session_id}", response_model=SessionDetailResponse)
async def update_session(session_id: str, request: SessionUpdateRequest, http_request: Request) -> SessionDetailResponse:
    service: SessionService = http_request.app.state.session_service
    try:
        session = service.update(session_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": str(exc)}) from exc
    return _session_detail(session, turn_limit=12)


@router.post("/sessions/{session_id}/stop", response_model=SessionDetailResponse)
async def stop_session(session_id: str, request: SessionStopRequest, http_request: Request) -> SessionDetailResponse:
    service: SessionService = http_request.app.state.session_service
    try:
        session = service.stop(session_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": str(exc)}) from exc
    return _session_detail(session, turn_limit=12)


@router.post("/sessions/{session_id}/resume", response_model=SessionDetailResponse)
async def resume_session(session_id: str, http_request: Request) -> SessionDetailResponse:
    service: SessionService = http_request.app.state.session_service
    try:
        session = service.resume(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": str(exc)}) from exc
    return _session_detail(session, turn_limit=12)


@router.post("/sessions/{session_id}/chat", response_model=SessionChatResponse)
async def chat(session_id: str, request: SessionChatRequest, http_request: Request) -> SessionChatResponse:
    service: SessionService = http_request.app.state.session_service
    try:
        session, trace_id, assistant_message, error, web_limit, completion, result_format = await service.run_chat(
            session_id,
            request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": str(exc)}) from exc
    return SessionChatResponse(
        session_id=session.session_id,
        trace_id=trace_id,
        status="failed" if error else "completed",
        assistant_message=assistant_message,
        result_format=result_format,
        web_tool_call_limit_used=web_limit,
        completion=completion,
        error=_mission_error(error),
        updated_at=session.updated_at.isoformat(),
    )


@router.post("/sessions/{session_id}/chat/stream")
async def chat_stream(session_id: str, request: SessionChatRequest, http_request: Request):
    service: SessionService = http_request.app.state.session_service
    return StreamingResponse(
        _stream_chat(service, session_id, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/sessions/{session_id}/graph", response_model=SessionGraphResponse)
async def graph(session_id: str, http_request: Request) -> SessionGraphResponse:
    session_service: SessionService = http_request.app.state.session_service
    graph_service: GraphSnapshotService = http_request.app.state.graph_snapshot_service
    session = session_service.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "session not found"})
    return graph_service.build_snapshot(session.session_id, session.db_path)


async def _stream_chat(service: SessionService, session_id: str, request: SessionChatRequest):
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def event_hook(event: dict[str, Any]) -> None:
        queue.put_nowait(event)

    async def runner() -> None:
        try:
            session, trace_id, assistant_message, error, web_limit, completion, result_format = await service.run_chat(
                session_id,
                request,
                event_hook=event_hook,
            )
        except ValueError as exc:
            queue.put_nowait(
                {
                    "event": "session.message.failed",
                    "data": {
                        "session_id": session_id,
                        "error": {"code": "not_found", "message": str(exc)},
                        "created_at": utc_now().isoformat(),
                    },
                }
            )
        else:
            queue.put_nowait(
                {
                    "event": "session.response",
                    "data": {
                        "session_id": session.session_id,
                        "trace_id": trace_id,
                        "assistant_message": assistant_message,
                        "status": "failed" if error else "completed",
                        "result_format": result_format.value,
                        "web_tool_call_limit_used": web_limit,
                        "completion": completion.model_dump(mode="json") if completion else None,
                        "error": _error_payload(error),
                        "updated_at": session.updated_at.isoformat(),
                        "created_at": utc_now().isoformat(),
                    },
                }
            )
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(runner())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield _format_sse(item["event"], item["data"])
    finally:
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def _format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _session_summary(session: ResearchSession) -> SessionSummaryResponse:
    if isinstance(session, ResearchSessionSummary):
        return SessionSummaryResponse(
            session_id=session.session_id,
            name=session.name,
            uses_dedicated_db=session.uses_dedicated_db,
            db_path=session.db_path,
            web_tool_call_limit=session.web_tool_call_limit,
            updated_at=session.updated_at.isoformat(),
            created_at=session.created_at.isoformat(),
            last_trace_id=session.last_trace_id,
            stop_mode=session.stop_mode,
            stop_reason=session.stop_reason,
            stop_requested_at=session.stop_requested_at.isoformat() if session.stop_requested_at else None,
            stopped_at=session.stopped_at.isoformat() if session.stopped_at else None,
            is_closed=getattr(session, "is_closed", False),
        )
    return SessionSummaryResponse(
        session_id=session.session_id,
        name=session.name,
        uses_dedicated_db=session.uses_dedicated_db,
        db_path=session.db_path,
        web_tool_call_limit=session.web_tool_call_limit,
        updated_at=session.updated_at.isoformat(),
        created_at=session.created_at.isoformat(),
        last_trace_id=session.summary.last_trace_id,
        stop_mode=session.stop_mode,
        stop_reason=session.stop_reason,
        stop_requested_at=session.stop_requested_at.isoformat() if session.stop_requested_at else None,
        stopped_at=session.stopped_at.isoformat() if session.stopped_at else None,
        is_closed=session.is_closed,
    )


def _session_detail(session: ResearchSession, *, turn_limit: int) -> SessionDetailResponse:
    turns, has_more = _session_turn_page(session.turns, turn_limit)
    return SessionDetailResponse(
        **_session_summary(session).model_dump(),
        preferred_model=session.preferred_model,
        allowed_models=session.allowed_models,
        output_schema=session.output_schema,
        web_enabled=session.web_enabled,
        db_mutation_enabled=session.db_mutation_enabled,
        notes=session.summary.notes,
        recent_tools=session.summary.recent_tools,
        compression_notice=session.summary.compression_notice,
        turns=[_turn_response(item) for item in turns],
        turn_count=len(session.turns),
        has_more_turns=has_more,
        oldest_turn_message_id=turns[0].message_id if turns else None,
        newest_turn_message_id=turns[-1].message_id if turns else None,
        last_error=_mission_error(session.last_error),
    )


def _turn_page(session: ResearchSession, turns, has_more: bool) -> SessionTurnPageResponse:
    return SessionTurnPageResponse(
        session_id=session.session_id,
        turns=[_turn_response(item) for item in turns],
        turn_count=len(session.turns),
        has_more_turns=has_more,
        oldest_turn_message_id=turns[0].message_id if turns else None,
        newest_turn_message_id=turns[-1].message_id if turns else None,
    )


def _session_turn_page(turns, limit: int):
    if limit <= 0:
        return list(turns), False
    start_index = max(0, len(turns) - limit)
    return list(turns[start_index:]), start_index > 0


def _turn_response(item) -> SessionTurnResponse:
    return SessionTurnResponse(
        message_id=item.message_id,
        role=item.role,
        content=item.content,
        created_at=item.created_at.isoformat(),
        trace_id=item.trace_id,
        status=item.status,
        result_format=item.result_format,
        web_tool_call_limit_used=item.web_tool_call_limit_used,
        completion=item.completion,
    )


def _mission_error(error) -> MissionRunError | None:
    if error is None:
        return None
    return MissionRunError(code=error.code, message=error.message, details=error.details)


def _error_payload(error) -> dict[str, Any] | None:
    if error is None:
        return None
    return {"code": error.code, "message": error.message, "details": error.details}
