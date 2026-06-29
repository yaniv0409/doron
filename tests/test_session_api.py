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
from agent_platform.domain.models import (
    CompressionEvent,
    CompressedMemory,
    CompletionMetadata,
    ExecutionTrace,
    MissionRequest,
    MissionResult,
    utc_now,
)
from agent_platform.infrastructure.session_context_store import SessionContextStore
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
            completion=CompletionMetadata(finish_reason="stop", usage={"output_tokens": 11}),
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
    assert (settings.sessions.directory / f"{first.session_id}.context.json").exists()

    updated = service.update(first.session_id, SessionUpdateRequest(web_tool_call_limit=9))

    assert updated.web_tool_call_limit == 9


def test_list_sessions_uses_summary_records(tmp_path: Path) -> None:
    async def scenario() -> list[dict[str, object]]:
        app = create_app()
        settings = AppSettings()
        settings.sessions = SessionSettings(
            directory=tmp_path / "sessions-list",
            db_directory=tmp_path / "dbs-list",
            shared_db_path=tmp_path / "dbs-list" / "shared.kuzu",
        )
        mission_service = FakeMissionService()
        store = SessionStore(settings.sessions)
        service = SessionService(settings, mission_service, store)
        session = service.open(SessionOpenRequest(name="List Session", web_tool_call_limit=2))
        app.state.session_service = service
        app.state.graph_snapshot_service = FakeGraphSnapshotService()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/sessions")

        assert response.status_code == 200
        return response.json()

    payload = asyncio.run(scenario())
    assert payload[0]["session_id"] != ""
    assert payload[0]["last_trace_id"] is None


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

    updated, trace_id, assistant_message, error, web_limit, completion, result_format = asyncio.run(
        service.run_chat(session.session_id, SessionChatRequest(message="Research NVIDIA", web_tool_call_limit=7))
    )

    assert trace_id == "trace-1"
    assert assistant_message == "assistant reply"
    assert error is None
    assert web_limit == 7
    assert completion is not None
    assert completion.finish_reason == "stop"
    assert result_format == ResultFormat.TEXT
    assert mission_service.requests[0].web_tool_call_limit == 7
    assert len(updated.turns) == 2
    assert updated.summary.last_trace_id == "trace-1"
    context_store = SessionContextStore(settings.sessions)
    context = context_store.load(session.session_id)
    assert context is not None
    assert len(context.active_turns) == 2
    assert context.current_mission_message_id == updated.turns[0].message_id


def test_session_prompt_prioritizes_current_mission(tmp_path: Path) -> None:
    settings = AppSettings()
    settings.sessions = SessionSettings(
        directory=tmp_path / "sessions-prompt",
        db_directory=tmp_path / "dbs-prompt",
        shared_db_path=tmp_path / "dbs-prompt" / "shared.kuzu",
    )
    mission_service = FakeMissionService()
    service = SessionService(settings, mission_service, SessionStore(settings.sessions))
    session = service.open(SessionOpenRequest(name="Prompt Test"))

    asyncio.run(service.run_chat(session.session_id, SessionChatRequest(message="First mission")))
    asyncio.run(service.run_chat(session.session_id, SessionChatRequest(message="Second mission is the main one")))

    prompt = mission_service.requests[-1].prompt

    assert "Current mission:\nSecond mission is the main one" in prompt
    assert "Recent active turns:" in prompt
    assert "User: First mission" in prompt
    assert prompt.index("Current mission:\nSecond mission is the main one") < prompt.index("Recent active turns:")


def test_session_context_compacts_without_dropping_visible_history(tmp_path: Path) -> None:
    settings = AppSettings()
    settings.sessions = SessionSettings(
        directory=tmp_path / "sessions-compact",
        db_directory=tmp_path / "dbs-compact",
        shared_db_path=tmp_path / "dbs-compact" / "shared.kuzu",
        active_context_turn_limit=2,
    )
    settings.compression.fallback_budget_chars = 100
    settings.models[0].context_window = 200
    mission_service = FakeMissionService()
    service = SessionService(settings, mission_service, SessionStore(settings.sessions))

    async def fake_compress(mission_request, context_state, compacted_turns, model):
        return CompressedMemory(
            notes=[f"compacted {len(compacted_turns)} turn(s)"],
            unresolved_goals=["carry forward prior context"],
            notice="session context compacted",
        )

    service._compress_context_state = fake_compress  # type: ignore[method-assign]
    session = service.open(SessionOpenRequest(name="Compact Test"))

    asyncio.run(service.run_chat(session.session_id, SessionChatRequest(message="A" * 220)))
    updated, *_ = asyncio.run(service.run_chat(session.session_id, SessionChatRequest(message="B" * 220)))

    context_store = SessionContextStore(settings.sessions)
    context = context_store.load(session.session_id)

    assert context is not None
    assert len(updated.turns) == 4
    assert len(context.active_turns) <= 3
    assert context.compressed_memory is not None
    assert context.compression_notice == "session context compacted"


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


def test_session_routes_page_turns(tmp_path: Path) -> None:
    async def scenario() -> dict[str, object]:
        app = create_app()
        settings = AppSettings()
        settings.sessions = SessionSettings(
            directory=tmp_path / "sessions-page",
            db_directory=tmp_path / "dbs-page",
            shared_db_path=tmp_path / "dbs-page" / "shared.kuzu",
        )
        mission_service = FakeMissionService()
        store = SessionStore(settings.sessions)
        app.state.session_service = SessionService(settings, mission_service, store)
        app.state.graph_snapshot_service = FakeGraphSnapshotService()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            opened = await client.post("/sessions/open", json={"name": "Paged Session"})
            session_id = opened.json()["session_id"]
            await client.post(f"/sessions/{session_id}/chat", json={"message": "First message"})
            await client.post(f"/sessions/{session_id}/chat", json={"message": "Second message"})
            detail = await client.get(f"/sessions/{session_id}?turn_limit=1")
            turns = await client.get(
                f"/sessions/{session_id}/turns?limit=1&before={detail.json()['oldest_turn_message_id']}"
            )

        return {"detail": detail.json(), "turns": turns.json()}

    payload = asyncio.run(scenario())
    assert payload["detail"]["turn_count"] == 4
    assert len(payload["detail"]["turns"]) == 1
    assert payload["detail"]["has_more_turns"] is True
    assert len(payload["turns"]["turns"]) == 1
    assert payload["turns"]["has_more_turns"] is True
