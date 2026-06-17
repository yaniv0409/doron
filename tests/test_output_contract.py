from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from agent_platform.agent.prompts import build_handoff_prompt, build_output_repair_prompt, build_system_prompt
from agent_platform.application.mission_service import MissionService
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


def test_prompt_builders_keep_output_schema_visible_after_refresh() -> None:
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    request = MissionRequest(prompt="Return the answer", db_path="/tmp/db.kuzu", output_schema=schema)
    context = _build_runtime(request)
    schema_json = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True)

    assert schema_json in build_system_prompt(context)
    assert schema_json in build_handoff_prompt(context)
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
