from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from agent_platform.application.maintenance_runner import MaintenanceRunner
from agent_platform.config.settings import AppSettings, TraceSettings
from agent_platform.domain.enums import MaintenanceJobStatus, MissionStatus, ResultFormat
from agent_platform.domain.models import ExecutionTrace, MissionError, MissionRequest, MissionResult, utc_now
from agent_platform.infrastructure.maintenance_job_store import MaintenanceJobStore
from agent_platform.infrastructure.trace_store import TraceStore


def _build_parent_trace(trace_id: str = "parent-trace") -> ExecutionTrace:
    request = MissionRequest(prompt="research nVent", db_path="/tmp/demo.kuzu")
    return ExecutionTrace(
        trace_id=trace_id,
        request=request,
        model_sequence=["openai/gpt-4.1-mini"],
        tool_calls=[],
        db_mutations=[],
        docs_lookups=[],
        web_artifacts=[],
        memory_retrievals=[],
        memory_mutations=[],
        compression_events=[],
        runtime_events=[],
        result={"ok": True},
        started_at=utc_now(),
        completed_at=utc_now(),
    )


def test_maintenance_job_store_writes_queue_artifacts(tmp_path: Path) -> None:
    settings = TraceSettings(directory=tmp_path / "traces", checkpoint_directory=tmp_path / "checkpoints")
    trace_store = TraceStore(settings)
    job_store = MaintenanceJobStore(settings, trace_store)
    parent_trace = _build_parent_trace()
    request = MissionRequest(
        prompt="maintain skills",
        db_path="/tmp/demo.kuzu",
        mission_metadata={"mission_kind": "skill_maintenance", "parent_trace_id": parent_trace.trace_id},
        web_enabled=False,
    )

    record = job_store.enqueue(parent_trace, request)

    loaded = job_store.read(record.trace_id)
    assert loaded is not None
    assert loaded.status == MaintenanceJobStatus.PENDING
    assert json.loads((settings.directory / record.trace_id / "request.json").read_text(encoding="utf-8"))["prompt"] == "maintain skills"
    assert "status" in json.loads((settings.directory / record.trace_id / "trace.json").read_text(encoding="utf-8"))


def test_maintenance_runner_marks_cancelled_jobs(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = AppSettings()
        settings.traces.directory = tmp_path / "traces"
        settings.traces.checkpoint_directory = tmp_path / "checkpoints"
        trace_store = TraceStore(settings.traces)
        job_store = MaintenanceJobStore(settings.traces, trace_store)
        parent_trace = _build_parent_trace()
        gate = asyncio.Event()
        request = MissionRequest(
            prompt="maintain skills",
            db_path="/tmp/demo.kuzu",
            mission_metadata={"mission_kind": "skill_maintenance", "parent_trace_id": parent_trace.trace_id},
            web_enabled=False,
        )

        async def fake_run(_: MissionRequest) -> MissionResult:
            await gate.wait()
            return MissionResult(
                status=MissionStatus.COMPLETED,
                result={"ok": True},
                result_format=ResultFormat.JSON_SCHEMA,
                final_model="openai/gpt-5.2",
                trace_id="maintenance-trace",
                started_at=utc_now(),
                completed_at=utc_now(),
            )

        service = SimpleNamespace(
            build_skill_maintenance_request=lambda trace: request,
            run=fake_run,
        )
        runner = MaintenanceRunner(settings, service, job_store)
        await runner.start()
        record = runner.enqueue(parent_trace)
        await asyncio.sleep(0.05)
        await runner.stop()

        loaded = job_store.read(record.trace_id)
        assert loaded is not None
        assert loaded.status == MaintenanceJobStatus.CANCELLED
        trace_payload = json.loads((settings.traces.directory / record.trace_id / "trace.json").read_text(encoding="utf-8"))
        assert trace_payload["status"] == "cancelled"
        assert trace_payload["error"]["code"] == "cancelled"

    asyncio.run(scenario())
