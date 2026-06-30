from __future__ import annotations

import uuid
from dataclasses import dataclass

from agent_platform.application.memory_manager import MemoryManager
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
    memory_manager: MemoryManager


@dataclass(slots=True)
class MissionRuntime:
    context: RuntimeContext
    memory_db: KuzuGateway
    research_meta_db: KuzuGateway
    browser: PlaywrightBrowserEngine
    services: RuntimeServices


class RuntimeBuilder:
    def __init__(self, settings: AppSettings) -> None:
        embedding_client = OpenRouterEmbeddingClient(settings.openrouter)
        self._services = RuntimeServices(
            settings=settings,
            model_catalog=ModelCatalog(settings),
            trace_store=TraceStore(settings.traces),
            docs_repository=DocumentationRepository(settings.docs),
            embedding_client=embedding_client,
            chat_client=OpenRouterChatClient(settings.openrouter),
            context_compressor=ContextCompressor(settings.compression),
            memory_manager=MemoryManager(settings.memory, embedding_client),
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
            web_tool_call_budget=request.web_tool_call_limit or self._services.settings.browser.web_tool_call_budget,
        )
        memory_db = KuzuGateway(request.memory_db_path)
        research_meta_db = KuzuGateway(request.research_meta_db_path)
        browser = PlaywrightBrowserEngine(
            self._services.settings.browser,
            telemetry_hook=lambda event: _record_browser_event(context, event),
        )
        return MissionRuntime(
            context=context,
            memory_db=memory_db,
            research_meta_db=research_meta_db,
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
