from __future__ import annotations

from contextlib import asynccontextmanager
from contextlib import suppress
import asyncio
import json
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

from agent_platform.api.db import router as db_router
from agent_platform.application.db_snapshot_service import DbSnapshotService
from agent_platform.application.mission_service import MissionService
from agent_platform.config.loader import load_settings
from agent_platform.contracts.api import MissionRunRequest, MissionStreamEvent
from agent_platform.contracts.serialization import to_api_response
from agent_platform.domain.enums import LogCategory
from agent_platform.domain.exceptions import AgentPlatformError
from agent_platform.domain.models import MissionRequest, utc_now
from agent_platform.infrastructure.logging import configure_logging, get_logger


def create_app() -> FastAPI:
    settings = load_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        configure_logging(settings.logging)
        yield

    app = FastAPI(title="Agent Platform", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.mission_service = MissionService(settings)
    app.state.db_contents_service = DbSnapshotService()
    app.state.logger = get_logger(LogCategory.API.value)
    app.include_router(db_router)
    _register_routes(app)
    return app


def _register_routes(app: FastAPI) -> None:
    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/config/models")
    async def list_models() -> dict[str, list[str]]:
        models = [item.name for item in app.state.settings.models]
        return {"models": models}

    @app.post("/missions/run")
    async def run_mission(request: MissionRunRequest):
        mission_request = MissionRequest(**request.model_dump(exclude={"stream"}))
        if not request.stream:
            result = await app.state.mission_service.run(mission_request)
            payload = to_api_response(result)
            return JSONResponse(payload.model_dump(mode="json"))
        return StreamingResponse(
            _stream_mission(app, mission_request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.exception_handler(AgentPlatformError)
    async def handle_platform_error(_, exc: AgentPlatformError) -> JSONResponse:
        app.state.logger.error(str(exc), extra={"trace_id": "-"})
        return JSONResponse(
            status_code=400,
            content={"error": {"code": type(exc).__name__, "message": str(exc)}},
        )


async def _stream_mission(app: FastAPI, mission_request: MissionRequest):
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def runner() -> None:
        try:
            result = await app.state.mission_service.run(
                mission_request,
                event_hook=queue.put_nowait,
            )
        except Exception as exc:  # pragma: no cover
            queue.put_nowait(
                {
                    "event": "mission.failed",
                    "data": {
                        "trace_id": getattr(getattr(app.state, "mission_service", None), "trace_id", None),
                        "error": {"code": type(exc).__name__, "message": str(exc)},
                        "created_at": utc_now().isoformat(),
                    },
                }
            )
        else:
            response = to_api_response(result)
            event_name = "mission.completed" if result.status.value == "completed" else "mission.failed"
            queue.put_nowait(
                MissionStreamEvent(
                    event=event_name,
                    data={
                        **response.model_dump(mode="json"),
                        "created_at": utc_now().isoformat(),
                    },
                ).model_dump(mode="json")
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
