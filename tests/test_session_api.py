from __future__ import annotations

import asyncio
import json
from pathlib import Path

from httpx import ASGITransport, AsyncClient
import pytest

from agent_platform.api.app import create_app
from agent_platform.application.session_service import SessionService
from agent_platform.config.settings import AppSettings, SessionSettings
from agent_platform.contracts.session import (
    SessionChatRequest,
    SessionForkRequest,
    SessionGraphResponse,
    SessionOpenRequest,
    SessionSteerRequest,
    SessionStopRequest,
    SessionUpdateRequest,
)
from agent_platform.domain.enums import MissionStatus, ResultFormat, SessionStopMode
from agent_platform.domain.models import (
    CompressionEvent,
    CompressedMemory,
    CompletionMetadata,
    ExecutionTrace,
    MissionRequest,
    MissionResult,
    utc_now,
)
from agent_platform.domain.exceptions import RequestValidationError
from agent_platform.infrastructure.session_context_store import SessionContextStore
from agent_platform.infrastructure.session_store import SessionStore


class FakeTraceStore:
    def read_trace(self, trace_id: str) -> ExecutionTrace:
        return ExecutionTrace(
            trace_id=trace_id,
            request=MissionRequest(prompt="p", memory_db_path="/tmp/memory.kuzu", research_meta_db_path="/tmp/research_meta.kuzu"),
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


class SteerableFakeMissionService(FakeMissionService):
    def __init__(self) -> None:
        super().__init__()
        self.first_run_started = asyncio.Event()
        self.allow_completion = asyncio.Event()

    async def run(self, request: MissionRequest, *, event_hook=None) -> MissionResult:
        self.requests.append(request)
        trace_id = f"trace-{len(self.requests)}"
        if event_hook is not None:
            event_hook({"event": "mission.started", "data": {"trace_id": trace_id, "model": "openai/gpt-4.1-mini"}})
        if len(self.requests) == 1:
            self.first_run_started.set()
            await self.allow_completion.wait()
        return MissionResult(
            status=MissionStatus.COMPLETED,
            result=f"assistant reply {len(self.requests)}",
            result_format=ResultFormat.TEXT,
            final_model="openai/gpt-4.1-mini",
            trace_id=trace_id,
            started_at=utc_now(),
            completed_at=utc_now(),
            completion=CompletionMetadata(finish_reason="stop", usage={"output_tokens": 11}),
        )


class FakeGraphSnapshotService:
    def build_snapshot(self, session_id: str, target: str, db_path: str) -> SessionGraphResponse:
        return SessionGraphResponse(
            session_id=session_id,
            target=target,
            graph_db_path=db_path,
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
        shared_db_dir=tmp_path / "dbs-open" / "shared",
    )
    mission_service = FakeMissionService()
    service = SessionService(settings, mission_service, SessionStore(settings.sessions))

    first = service.open(SessionOpenRequest(name="Market Research", use_dedicated_db=True, web_tool_call_limit=4))
    second = service.open(SessionOpenRequest(name=" market   research ", use_dedicated_db=False))

    assert first.session_id == second.session_id
    assert first.web_tool_call_limit == 4
    assert first.db_dir.endswith("shared") is False
    assert first.memory_db_path.endswith("memory.kuzu")
    assert first.research_meta_db_path.endswith("research_meta.kuzu")
    assert (settings.sessions.directory / f"{first.session_id}.context.json").exists()

    updated = service.update(first.session_id, SessionUpdateRequest(web_tool_call_limit=9))

    assert updated.web_tool_call_limit == 9


def test_grouped_open_and_fork_inheritance(tmp_path: Path) -> None:
    settings = AppSettings()
    settings.sessions = SessionSettings(
        directory=tmp_path / "sessions-groups",
        db_directory=tmp_path / "dbs-groups",
        shared_db_path=tmp_path / "dbs-groups" / "shared.kuzu",
        shared_db_dir=tmp_path / "dbs-groups" / "shared",
    )
    mission_service = FakeMissionService()
    service = SessionService(settings, mission_service, SessionStore(settings.sessions))
    preferred_model = settings.models[0].name
    source = service.open(
        SessionOpenRequest(
            name="Alpha",
            use_dedicated_db=True,
            preferred_model=preferred_model,
            allowed_models=[preferred_model],
            output_schema={"type": "object"},
            web_enabled=False,
            db_mutation_enabled=False,
            web_tool_call_limit=4,
        )
    )
    asyncio.run(service.run_chat(source.session_id, SessionChatRequest(message="Seed context")))

    forked = service.fork(
        source.session_id,
        SessionForkRequest(
            name="Alpha Fork",
            group_name="Alpha Group",
            inherit_model_settings=True,
            inherit_output_schema=True,
            inherit_runtime_settings=True,
            inherit_context=False,
        ),
    )

    assert forked.session_group_id is not None
    assert forked.session_group_name == "Alpha Group"
    assert forked.db_dir == source.db_dir
    assert forked.turns == []
    forked_context = SessionContextStore(settings.sessions).load(forked.session_id)
    assert forked_context is not None
    assert forked_context.active_turns == []
    source_after = service.get(source.session_id)
    assert source_after is not None
    assert source_after.session_group_id == forked.session_group_id
    assert source_after.session_group_name == "Alpha Group"

    resumed = service.open(SessionOpenRequest(name="alpha fork", session_group_id=forked.session_group_id))
    grouped_new = service.open(SessionOpenRequest(name="Alpha Branch", session_group_id=forked.session_group_id))

    assert resumed.session_id == forked.session_id
    assert grouped_new.session_id not in {source.session_id, forked.session_id}
    assert grouped_new.session_group_id == forked.session_group_id
    assert grouped_new.db_dir == source.db_dir


def test_fork_can_clone_context(tmp_path: Path) -> None:
    settings = AppSettings()
    settings.sessions = SessionSettings(
        directory=tmp_path / "sessions-fork-context",
        db_directory=tmp_path / "dbs-fork-context",
        shared_db_path=tmp_path / "dbs-fork-context" / "shared.kuzu",
        shared_db_dir=tmp_path / "dbs-fork-context" / "shared",
    )
    mission_service = FakeMissionService()
    service = SessionService(settings, mission_service, SessionStore(settings.sessions))
    source = service.open(SessionOpenRequest(name="Clone Source"))
    updated, *_ = asyncio.run(service.run_chat(source.session_id, SessionChatRequest(message="Preserve this context")))

    cloned = service.fork(
        source.session_id,
        SessionForkRequest(
            name="Clone Fork",
            group_name="Clone Group",
            inherit_model_settings=False,
            inherit_output_schema=False,
            inherit_runtime_settings=False,
            inherit_context=True,
        ),
    )

    cloned_context = SessionContextStore(settings.sessions).load(cloned.session_id)
    assert cloned.session_group_name == "Clone Group"
    assert len(cloned.turns) == len(updated.turns)
    assert cloned.turns[0].content == updated.turns[0].content
    assert cloned_context is not None
    assert len(cloned_context.active_turns) == len(updated.turns)
    assert cloned_context.current_mission_message_id == updated.turns[0].message_id
    assert cloned.preferred_model is None
    assert cloned.output_schema is None
    assert cloned.web_tool_call_limit is None


def test_list_sessions_uses_summary_records(tmp_path: Path) -> None:
    async def scenario() -> list[dict[str, object]]:
        app = create_app()
        settings = AppSettings()
        settings.sessions = SessionSettings(
            directory=tmp_path / "sessions-list",
            db_directory=tmp_path / "dbs-list",
        shared_db_path=tmp_path / "dbs-list" / "shared.kuzu",
        shared_db_dir=tmp_path / "dbs-list" / "shared",
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
        shared_db_dir=tmp_path / "dbs-chat" / "shared",
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
        shared_db_dir=tmp_path / "dbs-prompt" / "shared",
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


def test_soft_stop_injects_wrapup_prompt_and_resumes(tmp_path: Path) -> None:
    settings = AppSettings()
    settings.sessions = SessionSettings(
        directory=tmp_path / "sessions-soft-stop",
        db_directory=tmp_path / "dbs-soft-stop",
        shared_db_path=tmp_path / "dbs-soft-stop" / "shared.kuzu",
        shared_db_dir=tmp_path / "dbs-soft-stop" / "shared",
    )
    mission_service = FakeMissionService()
    service = SessionService(settings, mission_service, SessionStore(settings.sessions))
    session = service.open(SessionOpenRequest(name="Soft Stop Session"))

    stopped = service.stop(
        session.session_id,
        SessionStopRequest(mode=SessionStopMode.SOFT, reason="wrap up the research"),
    )
    asyncio.run(service.run_chat(session.session_id, SessionChatRequest(message="Continue research")))

    prompt = mission_service.requests[-1].prompt

    assert stopped.stop_mode == SessionStopMode.SOFT
    assert stopped.is_closed is False
    assert "Session stop request:" in prompt
    assert "Wrap up the mission" in prompt
    assert "Stop note: wrap up the research" in prompt

    resumed = service.resume(session.session_id)

    assert resumed.stop_mode is None
    assert resumed.stop_reason is None
    assert resumed.is_closed is False


def test_hard_stop_cancels_active_run_and_blocks_future_messages(tmp_path: Path) -> None:
    settings = AppSettings()
    settings.sessions = SessionSettings(
        directory=tmp_path / "sessions-hard-stop",
        db_directory=tmp_path / "dbs-hard-stop",
        shared_db_path=tmp_path / "dbs-hard-stop" / "shared.kuzu",
        shared_db_dir=tmp_path / "dbs-hard-stop" / "shared",
    )
    mission_service = FakeMissionService()
    service = SessionService(settings, mission_service, SessionStore(settings.sessions))
    session = service.open(SessionOpenRequest(name="Hard Stop Session"))

    stopped = service.stop(
        session.session_id,
        SessionStopRequest(mode=SessionStopMode.HARD, reason="hard stop now"),
    )

    assert stopped.is_closed is True
    assert stopped.stop_mode == SessionStopMode.HARD
    assert stopped.stop_reason == "hard stop now"
    assert stopped.last_error is not None
    assert stopped.last_error.code == "cancelled"

    with pytest.raises(RequestValidationError):
        asyncio.run(service.run_chat(session.session_id, SessionChatRequest(message="Try again")))


def test_session_context_compacts_without_dropping_visible_history(tmp_path: Path) -> None:
    settings = AppSettings()
    settings.sessions = SessionSettings(
        directory=tmp_path / "sessions-compact",
        db_directory=tmp_path / "dbs-compact",
        shared_db_path=tmp_path / "dbs-compact" / "shared.kuzu",
        shared_db_dir=tmp_path / "dbs-compact" / "shared",
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


def test_session_steer_restarts_active_run_with_preserved_context(tmp_path: Path) -> None:
    async def scenario():
        settings = AppSettings()
        settings.sessions = SessionSettings(
            directory=tmp_path / "sessions-steer",
            db_directory=tmp_path / "dbs-steer",
            shared_db_path=tmp_path / "dbs-steer" / "shared.kuzu",
            shared_db_dir=tmp_path / "dbs-steer" / "shared",
        )
        mission_service = SteerableFakeMissionService()
        service = SessionService(settings, mission_service, SessionStore(settings.sessions))
        session = service.open(SessionOpenRequest(name="Steer Session"))

        run_task = asyncio.create_task(service.run_chat(session.session_id, SessionChatRequest(message="Research Nvidia")))
        await mission_service.first_run_started.wait()
        steered = service.steer(session.session_id, SessionSteerRequest(message="Focus on pricing and guidance"))
        mission_service.allow_completion.set()
        updated, *_ = await run_task
        context = SessionContextStore(settings.sessions).load(session.session_id)
        return steered, updated, mission_service.requests, context

    steered, updated, requests, context = asyncio.run(scenario())

    assert steered.turns[-1].role == "steer"
    assert len(requests) == 2
    assert "Current mission:\nResearch Nvidia" in requests[0].prompt
    assert "Live steering updates:" not in requests[0].prompt
    assert "Current mission:\nResearch Nvidia" in requests[1].prompt
    assert "Live steering updates:\n- Focus on pricing and guidance" in requests[1].prompt
    assert [turn.role for turn in updated.turns] == ["user", "steer", "assistant"]
    assert updated.turns[-1].content == "assistant reply 2"
    assert context is not None
    assert context.current_mission_message_id == updated.turns[0].message_id


def test_session_routes_stream_and_graph(tmp_path: Path) -> None:
    async def scenario() -> tuple[list[tuple[str, dict[str, object]]], dict[str, object], dict[str, object]]:
        app = create_app()
        settings = AppSettings()
        settings.sessions = SessionSettings(
            directory=tmp_path / "sessions-routes",
            db_directory=tmp_path / "dbs-routes",
            shared_db_path=tmp_path / "dbs-routes" / "shared.kuzu",
            shared_db_dir=tmp_path / "dbs-routes" / "shared",
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
    assert detail_payload["memory_db_path"].endswith("memory.kuzu")
    assert detail_payload["turns"][-1]["web_tool_call_limit_used"] == 6


def test_session_stop_routes_update_status_and_resume(tmp_path: Path) -> None:
    async def scenario() -> dict[str, object]:
        app = create_app()
        settings = AppSettings()
        settings.sessions = SessionSettings(
            directory=tmp_path / "sessions-stop-routes",
            db_directory=tmp_path / "dbs-stop-routes",
            shared_db_path=tmp_path / "dbs-stop-routes" / "shared.kuzu",
            shared_db_dir=tmp_path / "dbs-stop-routes" / "shared",
        )
        mission_service = FakeMissionService()
        store = SessionStore(settings.sessions)
        app.state.session_service = SessionService(settings, mission_service, store)
        app.state.graph_snapshot_service = FakeGraphSnapshotService()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            opened = await client.post("/sessions/open", json={"name": "Stop Routes"})
            session_id = opened.json()["session_id"]
            stopped = await client.post(
                f"/sessions/{session_id}/stop",
                json={"mode": "soft", "reason": "wrap up"},
            )
            resumed = await client.post(f"/sessions/{session_id}/resume")
            detail = await client.get(f"/sessions/{session_id}")

        return {
            "stopped": stopped.json(),
            "resumed": resumed.json(),
            "detail": detail.json(),
        }

    payload = asyncio.run(scenario())
    assert payload["stopped"]["stop_mode"] == "soft"
    assert payload["stopped"]["stop_reason"] == "wrap up"
    assert payload["stopped"]["is_closed"] is False
    assert payload["resumed"]["stop_mode"] is None
    assert payload["resumed"]["stop_reason"] is None
    assert payload["resumed"]["is_closed"] is False
    assert payload["detail"]["stop_mode"] is None


def test_session_steer_route_requires_active_run(tmp_path: Path) -> None:
    async def scenario() -> tuple[int, dict[str, object]]:
        app = create_app()
        settings = AppSettings()
        settings.sessions = SessionSettings(
            directory=tmp_path / "sessions-steer-route",
            db_directory=tmp_path / "dbs-steer-route",
            shared_db_path=tmp_path / "dbs-steer-route" / "shared.kuzu",
            shared_db_dir=tmp_path / "dbs-steer-route" / "shared",
        )
        mission_service = FakeMissionService()
        store = SessionStore(settings.sessions)
        app.state.session_service = SessionService(settings, mission_service, store)
        app.state.graph_snapshot_service = FakeGraphSnapshotService()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            opened = await client.post("/sessions/open", json={"name": "Steer Route"})
            session_id = opened.json()["session_id"]
            response = await client.post(f"/sessions/{session_id}/steer", json={"message": "Focus the mission"})
        return response.status_code, response.json()

    status_code, payload = asyncio.run(scenario())
    assert status_code == 400
    assert payload["error"]["message"] == "session is not actively running"


def test_session_routes_page_turns(tmp_path: Path) -> None:
    async def scenario() -> dict[str, object]:
        app = create_app()
        settings = AppSettings()
        settings.sessions = SessionSettings(
            directory=tmp_path / "sessions-page",
            db_directory=tmp_path / "dbs-page",
            shared_db_path=tmp_path / "dbs-page" / "shared.kuzu",
            shared_db_dir=tmp_path / "dbs-page" / "shared",
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
