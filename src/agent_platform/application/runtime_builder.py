from __future__ import annotations

import uuid
from dataclasses import dataclass

from agent_platform.config.settings import AppSettings
from agent_platform.domain.models import MissionRequest, RuntimeContext, utc_now
from agent_platform.infrastructure.browser import PlaywrightBrowserEngine
from agent_platform.infrastructure.docs_loader import DocumentationRepository
from agent_platform.infrastructure.kuzu_client import KuzuGateway
from agent_platform.infrastructure.model_catalog import ModelCatalog
from agent_platform.infrastructure.openrouter_client import OpenRouterEmbeddingClient
from agent_platform.infrastructure.trace_store import TraceStore


@dataclass(slots=True)
class RuntimeServices:
    settings: AppSettings
    model_catalog: ModelCatalog
    trace_store: TraceStore
    docs_repository: DocumentationRepository
    embedding_client: OpenRouterEmbeddingClient


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
        browser = PlaywrightBrowserEngine(self._services.settings.browser)
        return MissionRuntime(
            context=context,
            db=db,
            browser=browser,
            services=self._services,
        )

    @property
    def services(self) -> RuntimeServices:
        return self._services
