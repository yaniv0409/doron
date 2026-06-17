from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter, ValidationError

from agent_platform.agent import factory as factory_module
from agent_platform.agent.factory import AgentFactory
from agent_platform.agent.prompts import build_handoff_prompt, build_output_repair_prompt, build_system_prompt
from agent_platform.application.mission_service import MissionService
from agent_platform.application.output_schema import build_output_type
from agent_platform.config.settings import AppSettings
from agent_platform.domain.enums import ResultFormat
from agent_platform.domain.models import MissionRequest, ModelDescriptor, RuntimeContext, utc_now


def _build_runtime(request: MissionRequest) -> RuntimeContext:
    model = ModelDescriptor(name="openai/gpt-4.1-mini", rank=10, context_window=4000)
    return RuntimeContext(
        trace_id="trace-1",
        mission_request=request,
        started_at=utc_now(),
        current_model=model,
        allowed_models=[model],
    )


def _build_runtime_wrapper(request: MissionRequest, settings: AppSettings) -> SimpleNamespace:
    context = _build_runtime(request)
    return SimpleNamespace(
        context=context,
        services=SimpleNamespace(settings=settings),
    )


def test_build_output_type_returns_structured_dict() -> None:
    schema = {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "score": {"type": "number"},
        },
        "required": ["answer", "score"],
        "additionalProperties": False,
    }
    output_type = build_output_type(schema)

    assert output_type is not None
    assert getattr(output_type, "__is_model_like__", False) is True
    assert TypeAdapter(output_type).validate_python({"answer": "ok", "score": 1.5}) == {
        "answer": "ok",
        "score": 1.5,
    }

    with pytest.raises(ValidationError):
        TypeAdapter(output_type).validate_python(["not", "an", "object"])


def test_agent_factory_passes_structured_output_type() -> None:
    schema = {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
        },
        "required": ["answer"],
        "additionalProperties": False,
    }
    settings = AppSettings()
    settings.openrouter.api_key = "test-key"
    settings.openrouter.app_url = "https://example.test"
    settings.openrouter.app_title = "wbi"
    runtime = _build_runtime_wrapper(MissionRequest(prompt="Return the answer", db_path="/tmp/db.kuzu", output_schema=schema), settings)

    captured: dict[str, object] = {}

    class FakeProvider:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    class FakeModel:
        def __init__(self, name, provider) -> None:
            self.name = name
            self.provider = provider

    class FakeAgent:
        def __init__(self, model, **kwargs) -> None:
            captured["model"] = model
            captured["kwargs"] = kwargs
            self.model = model
            self.kwargs = kwargs

        def tool(self, fn):
            return fn

    original_agent = factory_module.Agent
    original_model = factory_module.OpenRouterModel
    original_provider = factory_module.OpenRouterProvider
    try:
        factory_module.Agent = FakeAgent
        factory_module.OpenRouterModel = FakeModel
        factory_module.OpenRouterProvider = FakeProvider
        session = AgentFactory().create(runtime)
    finally:
        factory_module.Agent = original_agent
        factory_module.OpenRouterModel = original_model
        factory_module.OpenRouterProvider = original_provider

    assert isinstance(session.runtime.context, RuntimeContext)
    assert "output_type" in captured["kwargs"]
    output_type = captured["kwargs"]["output_type"]
    assert getattr(output_type, "__is_model_like__", False) is True
    assert TypeAdapter(output_type).validate_python({"answer": "ok"}) == {"answer": "ok"}


def test_prompt_builders_keep_structured_output_hint_after_refresh() -> None:
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    request = MissionRequest(prompt="Return the answer", db_path="/tmp/db.kuzu", output_schema=schema)
    context = _build_runtime(request)
    schema_json = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True)

    assert "structured and will be validated" in build_system_prompt(context)
    assert "structured and will be validated" in build_handoff_prompt(context)
    repair_prompt = build_output_repair_prompt(context, '{"wrong":"field"}', "answer is required")
    assert schema_json in repair_prompt
    assert "answer is required" in repair_prompt
    assert '{"wrong":"field"}' in repair_prompt


def test_mission_service_repairs_invalid_json_then_returns_structured_result() -> None:
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    settings = AppSettings()
    service = MissionService(settings)

    class FakeTraceStore:
        def write_request_snapshot(self, *args, **kwargs) -> None:
            return None

        def write_progress(self, *args, **kwargs) -> None:
            return None

    service._runtime_builder._services = SimpleNamespace(  # type: ignore[attr-defined]
        settings=settings,
        trace_store=FakeTraceStore(),
        model_catalog=SimpleNamespace(next_stronger=lambda *args, **kwargs: None),
    )

    prompts: list[str] = []

    async def fake_run_once(runtime, prompt: str) -> str:
        prompts.append(prompt)
        if len(prompts) == 1:
            return '{"wrong":"field"}'
        return '{"answer":"ok"}'

    async def fake_close() -> None:
        return None

    def build_runtime(request: MissionRequest):
        context = _build_runtime(request)
        return SimpleNamespace(
            context=context,
            browser=SimpleNamespace(close=fake_close),
            services=service._runtime_builder.services,
        )

    service._runtime_builder.build = build_runtime
    service._run_once = fake_run_once
    service._persist_trace = lambda *args, **kwargs: None

    request = MissionRequest(prompt="Return the answer", db_path="/tmp/db.kuzu", output_schema=schema)
    result = asyncio.run(service.run(request))

    assert result.status.value == "completed"
    assert result.result == {"answer": "ok"}
    assert result.result_format is ResultFormat.JSON_SCHEMA
    assert result.final_model == "openai/gpt-4.1-mini"
    assert len(prompts) == 2
    assert "Output schema:" in prompts[1]
    assert '"answer"' in prompts[1]
