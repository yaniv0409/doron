from __future__ import annotations

import uuid
from dataclasses import dataclass

from agent_platform.config.settings import AppSettings
from agent_platform.application.context_compression import ContextCompressor
from agent_platform.application.live_events import emit_runtime_event
from agent_platform.domain.models import MissionRequest, RuntimeContext, utc_now
from agent_platform.infrastructure.browser import PlaywrightBrowserEngine
from agent_platform.infrastructure.docs_loader import DocumentationRepository
from agent_platform.infrastructure.kuzu_client import KuzuGateway
from agent_platform.infrastructure.model_catalog import ModelCatalog
from agent_platform.infrastructure.openrouter_client import OpenRouterChatClient, OpenRouterEmbeddingClient
from agent_platform.infrastructure.trace_store import TraceStore


@dataclass(slots=True)
class RuntimeServices:
    settings: AppSettings
    model_catalog: ModelCatalog
    trace_store: TraceStore
    docs_repository: DocumentationRepository
    embedding_client: OpenRouterEmbeddingClient
    chat_client: OpenRouterChatClient
    context_compressor: ContextCompressor


@dataclass(slots=True)
class MissionRuntime:
    context: RuntimeContext
    db: KuzuGateway
    browser: PlaywrightBrowserEngine
    services: RuntimeServices


class RuntimeBuilder:
    def __init__(self, settings: AppSettings) -> None:
        self._services = RuntimeServices(
            settings=settings,
            model_catalog=ModelCatalog(settings),
            trace_store=TraceStore(settings.traces),
            docs_repository=DocumentationRepository(settings.docs),
            embedding_client=OpenRouterEmbeddingClient(settings.openrouter),
            chat_client=OpenRouterChatClient(settings.openrouter),
            context_compressor=ContextCompressor(settings.compression),
        )

    def build(self, request: MissionRequest) -> MissionRuntime:
        allowed_models = self._services.model_catalog.resolve_allowed(request)
        current_model = self._services.model_catalog.choose_initial(request)
        context = RuntimeContext(
            trace_id=str(uuid.uuid4()),
            mission_request=request,
            started_at=utc_now(),
            current_model=current_model,
            allowed_models=allowed_models,
        )
        db = KuzuGateway(request.db_path)
        browser = PlaywrightBrowserEngine(
            self._services.settings.browser,
            telemetry_hook=lambda event: _record_browser_event(context, event),
        )
        return MissionRuntime(
            context=context,
            db=db,
            browser=browser,
            services=self._services,
        )

    @property
    def services(self) -> RuntimeServices:
        return self._services


def _record_browser_event(context: RuntimeContext, event) -> None:
    emit_runtime_event(
        context,
        event.stage,
        event.message,
        dict(event.metadata),
        stream_event="mission.progress",
    )
