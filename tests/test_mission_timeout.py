import asyncio
from pathlib import Path
from types import SimpleNamespace

from agent_platform.application.mission_service import MissionService
from agent_platform.config.settings import AppSettings
from agent_platform.domain.models import MissionRequest


class HangingAgent:
    async def run(self, prompt: str, deps: object):
        await asyncio.sleep(3600)


class FakeAgentFactory:
    def create(self, runtime):
        return SimpleNamespace(runtime=runtime, agent=HangingAgent(), tool_names=["graph_read"])


def test_mission_service_times_out_and_writes_progress(tmp_path: Path) -> None:
    settings = AppSettings()
    settings.runtime.agent_run_timeout_seconds = 1
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

    result = service.run_sync(request)

    assert result.status.value == "failed"
    assert result.error is not None
    assert result.error.code == "agent_run_timeout"
    progress_path = settings.traces.directory / "trace-timeout" / "progress.json"
    assert progress_path.exists()
