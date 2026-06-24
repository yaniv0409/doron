import asyncio
from pathlib import Path
from types import SimpleNamespace

from agent_platform.application.mission_service import MissionService
from agent_platform.config.settings import AppSettings
from agent_platform.domain.exceptions import ModelError
from agent_platform.domain.models import CompletionMetadata, ModelDescriptor, MissionRequest, RuntimeContext, utc_now


class HangingAgent:
    async def run(self, prompt: str, deps: object):
        await asyncio.sleep(3600)


class FakeAgentFactory:
    def create(self, runtime):
        return SimpleNamespace(runtime=runtime, agent=HangingAgent(), tool_names=["skill_search"])


class FakeTraceStore:
    def write_request_snapshot(self, *args, **kwargs) -> None:
        pass

    def write_trace_skeleton(self, *args, **kwargs) -> None:
        pass

    def write_trace(self, *args, **kwargs) -> None:
        pass

    def write_progress(self, *args, **kwargs) -> None:
        pass

    def read_raw_trace_text(self, trace_id: str) -> str:
        return '{"trace_id":"parent-trace","tool_calls":[]}'

    def trace_path(self, trace_id: str):
        return Path("/tmp") / trace_id


class FakeChatClient:
    async def complete_json(self, *, model: str, system_prompt: str, user_prompt: str):
        return {
            "notes": ["compressed note"],
            "db_findings": [],
            "web_findings": [],
            "tool_summaries": ["compressed summary"],
            "tool_outcomes": [],
            "unresolved_goals": [],
            "notice": "compressed after overflow",
        }


class CapturingAgent:
    def __init__(self, prompts: list[object]) -> None:
        self._prompts = prompts

    async def run(self, prompt: object, deps: object):
        self._prompts.append(prompt)
        return SimpleNamespace(output="final answer")


class CapturingAgentFactory:
    def __init__(self, prompts: list[object]) -> None:
        self._prompts = prompts

    def create(self, runtime):
        return SimpleNamespace(runtime=runtime, agent=CapturingAgent(self._prompts), tool_names=["skill_search"])


def test_mission_service_times_out_and_writes_progress(tmp_path: Path) -> None:
    settings = AppSettings()
    settings.runtime.agent_run_timeout_seconds = 1
    settings.memory.enabled = False
    settings.memory.maintenance_enabled = False
    settings.traces.directory = tmp_path / "traces"
    settings.traces.checkpoint_directory = tmp_path / "checkpoints"
    service = MissionService(settings)
    service._agent_factory = FakeAgentFactory()
    service._runtime_builder.build = lambda request: SimpleNamespace(
        context=SimpleNamespace(
            trace_id="trace-timeout",
            mission_request=request,
            started_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            current_model=SimpleNamespace(name="openai/gpt-4.1-mini"),
            allowed_models=[],
            pending_context_refresh_reason=None,
            pending_model_switch=None,
            db_mutations=[],
            docs_lookups=[],
            compression_events=[],
            runtime_events=[],
            web_artifacts=[],
            tool_calls=[],
            reasoning_notes=[],
            web_findings=[],
            estimate_working_memory_size=lambda: 123,
            compressed_memory=None,
        ),
        browser=SimpleNamespace(close=lambda: asyncio.sleep(0)),
        services=service._runtime_builder.services,
    )
    request = MissionRequest(prompt="hang", db_path="/tmp/db.kuzu")

    result = asyncio.run(service.run(request))

    assert result.status.value == "failed"
    assert result.error is not None
    assert result.error.code == "agent_run_timeout"
    progress_path = settings.traces.directory / "trace-timeout" / "progress.json"
    assert progress_path.exists()


def test_mission_service_compresses_and_retries_on_context_overflow(tmp_path: Path) -> None:
    settings = AppSettings()
    settings.memory.enabled = False
    settings.memory.maintenance_enabled = False
    settings.traces.directory = tmp_path / "traces"
    settings.traces.checkpoint_directory = tmp_path / "checkpoints"
    service = MissionService(settings)
    service._runtime_builder.services.trace_store = FakeTraceStore()
    service._runtime_builder.services.chat_client = FakeChatClient()
    service._runtime_builder.services.model_catalog = SimpleNamespace(
        strongest_allowed=lambda allowed: allowed[-1],
    )

    request = MissionRequest(prompt="hello", db_path="/tmp/db.kuzu")
    current_model = ModelDescriptor(name="openai/gpt-4.1-mini", rank=10, context_window=4000)
    stronger_model = ModelDescriptor(name="openai/gpt-5.2", rank=100, context_window=32000)
    context = RuntimeContext(
        trace_id="trace-overflow",
        mission_request=request,
        started_at=utc_now(),
        current_model=current_model,
        allowed_models=[current_model, stronger_model],
    )
    runtime = SimpleNamespace(
        context=context,
        db=SimpleNamespace(),
        browser=SimpleNamespace(close=lambda: asyncio.sleep(0)),
        services=service._runtime_builder.services,
    )
    service._runtime_builder.build = lambda _: runtime

    prompts: list[str] = []

    async def fake_run_once(runtime, prompt: str):
        prompts.append(prompt)
        if len(prompts) == 1:
            raise ModelError(
                "chat completion request failed: {'message': 'This endpoint's maximum context length is 400000 tokens. However, you requested about 859555 tokens (858946 of text input, 609 of tool input). Please reduce the length of either one, or use the context-compression plugin to compress your prompt automatically.', 'code': 400}"
            )
        return "final answer"

    service._run_once = fake_run_once

    result = asyncio.run(service.run(request))

    assert result.status.value == "completed"
    assert result.result == "final answer"
    assert len(prompts) == 2
    assert prompts[0] == "hello"
    assert "Continue this mission from a previous model handoff." in prompts[1]
    assert context.compressed_memory is not None
    assert context.compression_events
    assert context.reasoning_notes[-1].startswith("Context was refreshed and compressed:")


def test_mission_service_does_not_compress_on_other_model_errors(tmp_path: Path) -> None:
    settings = AppSettings()
    settings.memory.enabled = False
    settings.memory.maintenance_enabled = False
    settings.traces.directory = tmp_path / "traces"
    settings.traces.checkpoint_directory = tmp_path / "checkpoints"
    service = MissionService(settings)
    service._runtime_builder.services.trace_store = FakeTraceStore()
    service._runtime_builder.services.chat_client = FakeChatClient()
    service._runtime_builder.services.model_catalog = SimpleNamespace(
        strongest_allowed=lambda allowed: allowed[-1],
    )

    request = MissionRequest(prompt="hello", db_path="/tmp/db.kuzu")
    current_model = ModelDescriptor(name="openai/gpt-4.1-mini", rank=10, context_window=4000)
    stronger_model = ModelDescriptor(name="openai/gpt-5.2", rank=100, context_window=32000)
    context = RuntimeContext(
        trace_id="trace-other-model-error",
        mission_request=request,
        started_at=utc_now(),
        current_model=current_model,
        allowed_models=[current_model, stronger_model],
    )
    runtime = SimpleNamespace(
        context=context,
        db=SimpleNamespace(),
        browser=SimpleNamespace(close=lambda: asyncio.sleep(0)),
        services=service._runtime_builder.services,
    )
    service._runtime_builder.build = lambda _: runtime

    prompts: list[str] = []

    async def fake_run_once(runtime, prompt: str):
        prompts.append(prompt)
        raise ModelError("chat completion request failed: upstream provider returned a bad request")

    service._run_once = fake_run_once

    result = asyncio.run(service.run(request))

    assert result.status.value == "failed"
    assert result.error is not None
    assert result.error.code == "ModelError"
    assert len(prompts) == 1
    assert context.compressed_memory is None
    assert not context.compression_events


def test_mission_service_continues_when_finish_reason_is_length(tmp_path: Path) -> None:
    settings = AppSettings()
    settings.memory.enabled = False
    settings.memory.maintenance_enabled = False
    settings.traces.directory = tmp_path / "traces"
    settings.traces.checkpoint_directory = tmp_path / "checkpoints"
    service = MissionService(settings)
    service._runtime_builder.services.trace_store = FakeTraceStore()
    service._runtime_builder.services.chat_client = FakeChatClient()
    service._runtime_builder.services.model_catalog = SimpleNamespace(
        strongest_allowed=lambda allowed: allowed[-1],
    )

    request = MissionRequest(prompt="hello", db_path="/tmp/db.kuzu")
    current_model = ModelDescriptor(name="openai/gpt-4.1-mini", rank=10, context_window=4000)
    stronger_model = ModelDescriptor(name="openai/gpt-5.2", rank=100, context_window=32000)
    context = RuntimeContext(
        trace_id="trace-length",
        mission_request=request,
        started_at=utc_now(),
        current_model=current_model,
        allowed_models=[current_model, stronger_model],
    )
    runtime = SimpleNamespace(
        context=context,
        db=SimpleNamespace(),
        browser=SimpleNamespace(close=lambda: asyncio.sleep(0)),
        services=service._runtime_builder.services,
    )
    service._runtime_builder.build = lambda _: runtime

    prompts: list[str] = []

    async def fake_run_once(runtime, prompt: str):
        prompts.append(prompt)
        if len(prompts) == 1:
            return "partial answer", CompletionMetadata(finish_reason="length", usage={"output_tokens": 100})
        return "final answer", CompletionMetadata(finish_reason="stop", usage={"output_tokens": 20})

    service._run_once = fake_run_once

    result = asyncio.run(service.run(request))

    assert result.status.value == "completed"
    assert result.result == "final answer"
    assert result.completion is not None
    assert result.completion.finish_reason == "stop"
    assert len(prompts) == 2
    assert prompts[0] == "hello"
    assert "finish_reason=length" in prompts[1]
    assert "Previous partial answer:" in prompts[1]


def test_skill_maintenance_run_attaches_parent_trace(tmp_path: Path) -> None:
    settings = AppSettings()
    settings.memory.enabled = False
    settings.memory.maintenance_enabled = False
    settings.traces.directory = tmp_path / "traces"
    settings.traces.checkpoint_directory = tmp_path / "checkpoints"
    service = MissionService(settings)
    service._runtime_builder.services.trace_store = FakeTraceStore()
    service._runtime_builder.services.chat_client = FakeChatClient()
    service._runtime_builder.services.model_catalog = SimpleNamespace(
        strongest_allowed=lambda allowed: allowed[-1],
    )
    service._runtime_builder.services.memory_manager = SimpleNamespace(ensure_schema=lambda db: None)

    captured_prompts: list[object] = []
    service._agent_factory = CapturingAgentFactory(captured_prompts)

    request = MissionRequest(
        prompt="maintain skills",
        db_path="/tmp/db.kuzu",
        mission_metadata={"mission_kind": "skill_maintenance", "parent_trace_id": "parent-trace"},
        web_enabled=False,
    )
    current_model = ModelDescriptor(name="openai/gpt-4.1-mini", rank=10, context_window=4000)
    stronger_model = ModelDescriptor(name="openai/gpt-5.2", rank=100, context_window=32000)
    context = RuntimeContext(
        trace_id="trace-maintenance",
        mission_request=request,
        started_at=utc_now(),
        current_model=current_model,
        allowed_models=[current_model, stronger_model],
    )
    runtime = SimpleNamespace(
        context=context,
        db=SimpleNamespace(),
        browser=SimpleNamespace(close=lambda: asyncio.sleep(0)),
        services=service._runtime_builder.services,
    )
    service._runtime_builder.build = lambda _: runtime
    service._maybe_schedule_skill_maintenance = lambda trace: None

    result = asyncio.run(service.run(request))

    assert result.status.value == "completed"
    assert result.result == "final answer"
    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    assert isinstance(prompt, list)
    assert prompt[0] == "maintain skills"
    attachment = prompt[1]
    assert attachment.media_type == "text/plain"
    assert attachment.url.startswith("data:text/plain;base64,")
    assert attachment.identifier == "parent-trace"
