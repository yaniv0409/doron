from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from agent_platform.application.mission_service import MissionService
from agent_platform.config.loader import load_settings
from agent_platform.contracts.api import MissionRunRequest
from agent_platform.contracts.serialization import to_api_response
from agent_platform.domain.enums import LogCategory
from agent_platform.domain.exceptions import AgentPlatformError
from agent_platform.domain.models import MissionRequest
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
    app.state.logger = get_logger(LogCategory.API.value)
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
    async def run_mission(request: MissionRunRequest) -> JSONResponse:
        mission_request = MissionRequest(**request.model_dump())
        result = await app.state.mission_service.run(mission_request)
        payload = to_api_response(result)
        return JSONResponse(payload.model_dump(mode="json"))

    @app.exception_handler(AgentPlatformError)
    async def handle_platform_error(_, exc: AgentPlatformError) -> JSONResponse:
        app.state.logger.error(str(exc), extra={"trace_id": "-"})
        return JSONResponse(
            status_code=400,
            content={"error": {"code": type(exc).__name__, "message": str(exc)}},
        )
