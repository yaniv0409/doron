from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from agent_platform.agent.prompts import build_system_prompt
from agent_platform.application.mission_service import MissionService
from agent_platform.config.settings import AppSettings
from agent_platform.domain.models import DurableMemoryRecord, MissionRequest, ModelDescriptor, RuntimeContext, utc_now


def _build_fake_runtime(request: MissionRequest, settings: AppSettings, *, memory_manager) -> SimpleNamespace:
    model = ModelDescriptor(name="openai/gpt-4.1-mini", rank=10, context_window=4000)
    context = RuntimeContext(
        trace_id="trace-1",
        mission_request=request,
        started_at=utc_now(),
        current_model=model,
        allowed_models=[model],
    )
    return SimpleNamespace(
        context=context,
        db=SimpleNamespace(),
        browser=SimpleNamespace(close=lambda: asyncio.sleep(0)),
        services=SimpleNamespace(
            settings=settings,
            memory_manager=memory_manager,
            trace_store=SimpleNamespace(
                write_request_snapshot=lambda *args, **kwargs: None,
                write_progress=lambda *args, **kwargs: None,
                write_trace=lambda trace: None,
                write_trace_skeleton=lambda *args, **kwargs: None,
                trace_path=lambda trace_id: settings.traces.directory / trace_id / "trace.json",
                read_raw_trace_text=lambda trace_id: "{\"trace_id\": \"parent\", \"tool_calls\": []}",
                read_trace_head=lambda trace_id, chars: "{\"trace_id\": \"parent\"}"[:chars],
            ),
            model_catalog=SimpleNamespace(resolve_allowed=lambda req: [model], strongest_allowed=lambda allowed: model),
        ),
    )


def test_main_prompt_includes_preflight_skill_context() -> None:
    async def fake_preflight(runtime) -> None:
        runtime.context.retrieved_skills = [
            DurableMemoryRecord(
                id="skill-1",
                kind="skill",
                title="Use source packs first",
                body="Prefer learned sources before browsing.",
            )
        ]

    settings = AppSettings()
    service = MissionService(settings)
    memory_manager = SimpleNamespace(preflight=fake_preflight)

    captured_prompts: list[str] = []

    async def fake_run_once(runtime, prompt: str) -> str:
        captured_prompts.append(build_system_prompt(runtime.context))
        return "ok"

    def build_runtime(request: MissionRequest):
        return _build_fake_runtime(request, settings, memory_manager=memory_manager)

    service._run_once = fake_run_once
    service._runtime_builder.build = build_runtime
    service._persist_trace = lambda *args, **kwargs: SimpleNamespace(  # type: ignore[assignment]
        request=args[0].context.mission_request,
        trace_id="trace-1",
        model_dump=lambda mode="json": {},
    )
    service._maybe_schedule_skill_maintenance = lambda trace: None

    result = asyncio.run(service.run(MissionRequest(prompt="research nVent", db_path="/tmp/demo.kuzu")))

    assert result.status.value == "completed"
    assert "Learned skill context" in captured_prompts[0]
    assert "Use source packs first" in captured_prompts[0]
    assert "Empty skills are not a stop signal" in captured_prompts[0]


def test_mission_service_schedules_background_skill_maintenance(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = AppSettings()
        settings.traces.directory = tmp_path / "traces"
        settings.traces.checkpoint_directory = tmp_path / "checkpoints"
        settings.memory.enabled = False
        service = MissionService(settings)

        scheduled: list[object] = []

        def build_runtime(request: MissionRequest):
            return _build_fake_runtime(
                request,
                settings,
                memory_manager=SimpleNamespace(preflight=lambda runtime: None, ensure_schema=lambda db: None),
            )

        async def fake_run_once(runtime, prompt: str) -> str:
            return "ok"

        service.attach_maintenance_runner(SimpleNamespace(enqueue=lambda trace: scheduled.append(trace)))
        service._runtime_builder.build = build_runtime
        service._run_once = fake_run_once
        await service.run(MissionRequest(prompt="research", db_path="/tmp/demo.kuzu"))

        assert len(scheduled) == 1

    asyncio.run(scenario())


def test_maintenance_run_writes_trace_skeleton(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = AppSettings()
        settings.traces.directory = tmp_path / "traces"
        settings.traces.checkpoint_directory = tmp_path / "checkpoints"
        service = MissionService(settings)

        trace_store = SimpleNamespace(
            write_request_snapshot=lambda *args, **kwargs: None,
            write_progress=lambda *args, **kwargs: None,
            write_trace=lambda trace: None,
            trace_path=lambda trace_id: settings.traces.directory / trace_id / "trace.json",
            write_trace_skeleton=lambda trace_id, payload: captured.append((trace_id, payload)),
            read_raw_trace_text=lambda trace_id: "{\"trace_id\": \"parent-1\", \"tool_calls\": []}",
            read_trace_head=lambda trace_id, chars: "{\"trace_id\": \"parent-1\", \"tool_calls\": []}"[:chars],
        )
        captured: list[tuple[str, dict[str, object]]] = []

        async def fake_related_for_maintenance(db, trace_text: str):
            return []

        def build_runtime(request: MissionRequest):
            runtime = _build_fake_runtime(
                request,
                settings,
                memory_manager=SimpleNamespace(
                    preflight=lambda runtime: None,
                    ensure_schema=lambda db: None,
                    related_for_maintenance=fake_related_for_maintenance,
                ),
            )
            runtime.services.trace_store = trace_store
            return runtime

        async def fake_run_once(runtime, prompt: str) -> str:
            return "maintenance summary"

        request = MissionRequest(
            prompt="maintain skills",
            db_path="/tmp/demo.kuzu",
            mission_metadata={"mission_kind": "skill_maintenance", "parent_trace_id": "parent-1"},
            web_enabled=False,
        )
        service._runtime_builder.build = build_runtime
        service._run_once = fake_run_once
        service._persist_trace = lambda *args, **kwargs: SimpleNamespace(request=request, trace_id="trace-maint")  # type: ignore[assignment]
        service._maybe_schedule_skill_maintenance = lambda trace: None

        await service.run(request)

        assert captured
        assert captured[0][1]["mission_kind"] == "skill_maintenance"
        assert captured[0][1]["parent_trace_id"] == "parent-1"
        assert captured[0][1]["status"] == "started"

    asyncio.run(scenario())


def test_skill_maintenance_prompt_uses_trace_head_not_full_trace(tmp_path: Path) -> None:
    settings = AppSettings()
    settings.traces.directory = tmp_path / "traces"
    settings.traces.checkpoint_directory = tmp_path / "checkpoints"
    settings.memory.maintenance_trace_head_chars = 18
    service = MissionService(settings)

    service._runtime_builder._services.trace_store = SimpleNamespace(  # type: ignore[attr-defined]
        read_trace_head=lambda trace_id, chars: "0123456789ABCDEFGHIJ"[:chars],
    )

    trace = SimpleNamespace(trace_id="parent-trace")
    prompt = service._build_skill_maintenance_prompt(trace)  # type: ignore[arg-type]

    assert "Parent trace ID: parent-trace" in prompt
    assert "0123456789ABCDEFGH" in prompt
    assert "harden Doron's skills" in prompt
