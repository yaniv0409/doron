from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent_platform.application.graph_snapshot_service import GraphSnapshotService
from agent_platform.application.session_service import SessionService
from agent_platform.contracts.api import MissionRunError
from agent_platform.contracts.session import (
    SessionChatRequest,
    SessionChatResponse,
    SessionDetailResponse,
    SessionGraphResponse,
    SessionOpenRequest,
    SessionSummaryResponse,
    SessionTurnResponse,
    SessionUpdateRequest,
)
from agent_platform.domain.models import ResearchSession, utc_now

router = APIRouter(tags=["sessions"])


@router.get("/sessions", response_model=list[SessionSummaryResponse])
async def list_sessions(http_request: Request) -> list[SessionSummaryResponse]:
    service: SessionService = http_request.app.state.session_service
    return [_session_summary(item) for item in service.list_sessions()]


@router.post("/sessions/open", response_model=SessionDetailResponse)
async def open_session(request: SessionOpenRequest, http_request: Request) -> SessionDetailResponse:
    service: SessionService = http_request.app.state.session_service
    return _session_detail(service.open(request))


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session(session_id: str, http_request: Request) -> SessionDetailResponse:
    service: SessionService = http_request.app.state.session_service
    session = service.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "session not found"})
    return _session_detail(session)


@router.patch("/sessions/{session_id}", response_model=SessionDetailResponse)
async def update_session(session_id: str, request: SessionUpdateRequest, http_request: Request) -> SessionDetailResponse:
    service: SessionService = http_request.app.state.session_service
    try:
        session = service.update(session_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": str(exc)}) from exc
    return _session_detail(session)


@router.post("/sessions/{session_id}/chat", response_model=SessionChatResponse)
async def chat(session_id: str, request: SessionChatRequest, http_request: Request) -> SessionChatResponse:
    service: SessionService = http_request.app.state.session_service
    try:
        session, trace_id, assistant_message, error, web_limit = await service.run_chat(session_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": str(exc)}) from exc
    return SessionChatResponse(
        session_id=session.session_id,
        trace_id=trace_id,
        status="failed" if error else "completed",
        assistant_message=assistant_message,
        web_tool_call_limit_used=web_limit,
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
            session, trace_id, assistant_message, error, web_limit = await service.run_chat(
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
                        "web_tool_call_limit_used": web_limit,
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
    return SessionSummaryResponse(
        session_id=session.session_id,
        name=session.name,
        uses_dedicated_db=session.uses_dedicated_db,
        db_path=session.db_path,
        web_tool_call_limit=session.web_tool_call_limit,
        updated_at=session.updated_at.isoformat(),
        created_at=session.created_at.isoformat(),
        last_trace_id=session.summary.last_trace_id,
    )


def _session_detail(session: ResearchSession) -> SessionDetailResponse:
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
        turns=[
            SessionTurnResponse(
                message_id=item.message_id,
                role=item.role,
                content=item.content,
                created_at=item.created_at.isoformat(),
                trace_id=item.trace_id,
                status=item.status,
                web_tool_call_limit_used=item.web_tool_call_limit_used,
            )
            for item in session.turns
        ],
        last_error=_mission_error(session.last_error),
    )


def _mission_error(error) -> MissionRunError | None:
    if error is None:
        return None
    return MissionRunError(code=error.code, message=error.message, details=error.details)


def _error_payload(error) -> dict[str, Any] | None:
    if error is None:
        return None
    return {"code": error.code, "message": error.message, "details": error.details}
