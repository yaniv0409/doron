from __future__ import annotations

import json
import asyncio

from httpx import ASGITransport, AsyncClient

from agent_platform.api.app import create_app
from agent_platform.domain.enums import MissionStatus, ResultFormat
from agent_platform.domain.models import MissionError, MissionRequest, MissionResult, utc_now


def _build_result(status: MissionStatus = MissionStatus.COMPLETED) -> MissionResult:
    return MissionResult(
        status=status,
        result={"answer": "ok"},
        result_format=ResultFormat.JSON_SCHEMA,
        final_model="openai/gpt-5.2",
        trace_id="trace-1",
        started_at=utc_now(),
        completed_at=utc_now(),
        error=None if status is MissionStatus.COMPLETED else MissionError(code="x", message="y"),
    )


def _parse_sse(lines: list[str]) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    current_event: str | None = None
    current_data: list[str] = []
    for line in lines:
        if not line:
            if current_event and current_data:
                events.append((current_event, json.loads("\n".join(current_data))))
            current_event = None
            current_data = []
            continue
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            current_data.append(line.split(":", 1)[1].strip())
    if current_event and current_data:
        events.append((current_event, json.loads("\n".join(current_data))))
    return events


def test_run_mission_streams_progress_and_final_event() -> None:
    async def scenario() -> list[tuple[str, dict[str, object]]]:
        app = create_app()

        async def fake_run(request: MissionRequest, *, event_hook=None):
            if event_hook is not None:
                event_hook({"event": "mission.started", "data": {"trace_id": "trace-1", "request": {"prompt": "hello"}, "model": "openai/gpt-4.1-mini"}})
                event_hook({"event": "mission.progress", "data": {"trace_id": "trace-1", "phase": "agent_run_started", "message": "started", "metadata": {}}})
                event_hook({"event": "tool.started", "data": {"trace_id": "trace-1", "name": "graph_schema", "arguments": {}}})
                event_hook(
                    {
                        "event": "tool.completed",
                        "data": {
                            "trace_id": "trace-1",
                            "name": "graph_schema",
                            "arguments": {},
                            "ok": False,
                            "error_type": "browser_runtime_error",
                            "error_message": "browser closed unexpectedly",
                        },
                    }
                )
            return _build_result()

        app.state.mission_service.run = fake_run

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/missions/run",
                json={"prompt": "hello", "db_path": "/tmp/db.kuzu", "stream": True},
            ) as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                lines = [line async for line in response.aiter_lines()]

        return _parse_sse(lines)

    events = asyncio.run(scenario())
    assert [event for event, _ in events] == [
        "mission.started",
        "mission.progress",
        "tool.started",
        "tool.completed",
        "mission.completed",
    ]
    assert events[-1][1]["trace_id"] == "trace-1"
    assert events[3][1]["error_message"] == "browser closed unexpectedly"


def test_run_mission_blocking_json_remains_available() -> None:
    async def scenario() -> dict[str, object]:
        app = create_app()

        async def fake_run(request: MissionRequest, *, event_hook=None):
            return _build_result()

        app.state.mission_service.run = fake_run

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/missions/run",
                json={"prompt": "hello", "db_path": "/tmp/db.kuzu"},
            )

        assert response.status_code == 200
        return response.json()

    payload = asyncio.run(scenario())
    assert payload["trace_id"] == "trace-1"
