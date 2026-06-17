from __future__ import annotations

from agent_platform.config.settings import AppSettings
from agent_platform.domain.exceptions import ConfigurationError
from agent_platform.domain.models import ModelDescriptor, MissionRequest


class ModelCatalog:
    def __init__(self, settings: AppSettings) -> None:
        self._models = sorted(
            [
                ModelDescriptor(
                    name=item.name,
                    rank=item.rank,
                    context_window=item.context_window,
                    cost_class=item.cost_class,
                    supports_tools=item.supports_tools,
                    supports_structured_output=item.supports_structured_output,
                    is_default=item.is_default,
                )
                for item in settings.models
            ],
            key=lambda item: item.rank,
        )

    def resolve_allowed(self, request: MissionRequest) -> list[ModelDescriptor]:
        allowed = self._models
        if request.allowed_models:
            names = set(request.allowed_models)
            allowed = [item for item in self._models if item.name in names]
        if not allowed:
            raise ConfigurationError("no allowed models resolved from request")
        return allowed

    def choose_initial(self, request: MissionRequest) -> ModelDescriptor:
        allowed = self.resolve_allowed(request)
        if request.preferred_model:
            for model in allowed:
                if model.name == request.preferred_model:
                    return model
            raise ConfigurationError("preferred model is not allowed")
        for model in allowed:
            if model.is_default:
                return model
        return allowed[0]

    def next_stronger(
        self,
        current_model: str,
        allowed: list[ModelDescriptor],
    ) -> ModelDescriptor | None:
        current = None
        for model in allowed:
            if model.name == current_model:
                current = model
                break
        if current is None:
            return None
        stronger = [item for item in allowed if item.rank > current.rank]
        if not stronger:
            return None
        return stronger[0]

    def strongest_allowed(self, allowed: list[ModelDescriptor]) -> ModelDescriptor:
        return max(allowed, key=lambda item: item.rank)
