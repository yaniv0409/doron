from __future__ import annotations

import asyncio
import json
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from agent_platform.api.app import create_app
from agent_platform.application.session_service import SessionService
from agent_platform.config.settings import AppSettings, SessionSettings
from agent_platform.contracts.session import SessionChatRequest, SessionGraphResponse, SessionOpenRequest, SessionUpdateRequest
from agent_platform.domain.enums import MissionStatus, ResultFormat
from agent_platform.domain.models import CompressionEvent, ExecutionTrace, MissionRequest, MissionResult, utc_now
from agent_platform.infrastructure.session_store import SessionStore


class FakeTraceStore:
    def read_trace(self, trace_id: str) -> ExecutionTrace:
        return ExecutionTrace(
            trace_id=trace_id,
            request=MissionRequest(prompt="p", db_path="/tmp/db.kuzu"),
            model_sequence=["openai/gpt-4.1-mini"],
            tool_calls=[],
            db_mutations=[],
            docs_lookups=[],
            web_artifacts=[],
            compression_events=[
                CompressionEvent(
                    trigger="auto",
                    summarizer_model="openai/gpt-5.2",
                    reason="reduce context",
                    size_before=1000,
                    size_after=400,
                    preview="compressed",
                )
            ],
            runtime_events=[],
            started_at=utc_now(),
            completed_at=utc_now(),
        )


class FakeMissionService:
    def __init__(self) -> None:
        self.trace_store = FakeTraceStore()
        self.requests: list[MissionRequest] = []

    async def run(self, request: MissionRequest, *, event_hook=None) -> MissionResult:
        self.requests.append(request)
        trace_id = f"trace-{len(self.requests)}"
        if event_hook is not None:
            event_hook({"event": "mission.started", "data": {"trace_id": trace_id, "model": "openai/gpt-4.1-mini"}})
            event_hook(
                {
                    "event": "tool.started",
                    "data": {
                        "trace_id": trace_id,
                        "name": "browser_open",
                        "parameters": {"urls": ["https://example.com"], "reason": "research"},
                    },
                }
            )
            event_hook(
                {
                    "event": "tool.completed",
                    "data": {
                        "trace_id": trace_id,
                        "name": "browser_open",
                        "parameters": {"urls": ["https://example.com"], "reason": "research"},
                        "ok": True,
                        "result_summary": "opened",
                    },
                }
            )
        return MissionResult(
            status=MissionStatus.COMPLETED,
            result="assistant reply",
            result_format=ResultFormat.TEXT,
            final_model="openai/gpt-4.1-mini",
            trace_id=trace_id,
            started_at=utc_now(),
            completed_at=utc_now(),
        )


class FakeGraphSnapshotService:
    def build_snapshot(self, session_id: str, db_path: str) -> SessionGraphResponse:
        return SessionGraphResponse(
            session_id=session_id,
            db_path=db_path,
            generated_at=utc_now().isoformat(),
            node_count=1,
            edge_count=0,
            nodes=[{"id": "n1", "label": "Company", "kind": "Company", "properties": {"ticker": "NVDA"}}],
            edges=[],
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
    return events


def test_open_resume_and_update_session(tmp_path: Path) -> None:
    settings = AppSettings()
    settings.sessions = SessionSettings(
        directory=tmp_path / "sessions-open",
        db_directory=tmp_path / "dbs-open",
        shared_db_path=tmp_path / "dbs-open" / "shared.kuzu",
    )
    mission_service = FakeMissionService()
    service = SessionService(settings, mission_service, SessionStore(settings.sessions))

    first = service.open(SessionOpenRequest(name="Market Research", use_dedicated_db=True, web_tool_call_limit=4))
    second = service.open(SessionOpenRequest(name=" market   research ", use_dedicated_db=False))

    assert first.session_id == second.session_id
    assert first.web_tool_call_limit == 4
    assert first.db_path.endswith(".kuzu")

    updated = service.update(first.session_id, SessionUpdateRequest(web_tool_call_limit=9))

    assert updated.web_tool_call_limit == 9


def test_session_chat_persists_and_uses_web_limit(tmp_path: Path) -> None:
    settings = AppSettings()
    settings.sessions = SessionSettings(
        directory=tmp_path / "sessions-chat",
        db_directory=tmp_path / "dbs-chat",
        shared_db_path=tmp_path / "dbs-chat" / "shared.kuzu",
    )
    mission_service = FakeMissionService()
    service = SessionService(settings, mission_service, SessionStore(settings.sessions))
    session = service.open(SessionOpenRequest(name="Session A", web_tool_call_limit=3))

    updated, trace_id, assistant_message, error, web_limit = asyncio.run(
        service.run_chat(session.session_id, SessionChatRequest(message="Research NVIDIA", web_tool_call_limit=7))
    )

    assert trace_id == "trace-1"
    assert assistant_message == "assistant reply"
    assert error is None
    assert web_limit == 7
    assert mission_service.requests[0].web_tool_call_limit == 7
    assert len(updated.turns) == 2
    assert updated.summary.last_trace_id == "trace-1"


def test_session_routes_stream_and_graph(tmp_path: Path) -> None:
    async def scenario() -> tuple[list[tuple[str, dict[str, object]]], dict[str, object], dict[str, object]]:
        app = create_app()
        settings = AppSettings()
        settings.sessions = SessionSettings(
            directory=tmp_path / "sessions-routes",
            db_directory=tmp_path / "dbs-routes",
            shared_db_path=tmp_path / "dbs-routes" / "shared.kuzu",
        )
        mission_service = FakeMissionService()
        store = SessionStore(settings.sessions)
        app.state.session_service = SessionService(settings, mission_service, store)
        app.state.graph_snapshot_service = FakeGraphSnapshotService()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            opened = await client.post("/sessions/open", json={"name": "Live Session", "web_tool_call_limit": 5})
            session_id = opened.json()["session_id"]
            async with client.stream(
                "POST",
                f"/sessions/{session_id}/chat/stream",
                json={"message": "Hello", "web_tool_call_limit": 6},
            ) as response:
                lines = [line async for line in response.aiter_lines()]
            graph = await client.get(f"/sessions/{session_id}/graph")
            detail = await client.get(f"/sessions/{session_id}")

        return _parse_sse(lines), graph.json(), detail.json()

    events, graph_payload, detail_payload = asyncio.run(scenario())
    event_names = [name for name, _ in events]
    assert "session.started" in event_names
    assert "mission.started" in event_names
    assert "tool.completed" in event_names
    assert "session.message.completed" in event_names
    assert "session.graph.updated" in event_names
    assert "session.response" in event_names
    assert graph_payload["node_count"] == 1
    assert detail_payload["web_tool_call_limit"] == 5
    assert detail_payload["turns"][-1]["web_tool_call_limit_used"] == 6
