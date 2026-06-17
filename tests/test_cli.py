from argparse import Namespace

from agent_platform.cli.chat import build_defaults, parse_allowed_models
from agent_platform.cli.formatters import format_final_stream_response, format_response, format_stream_event, format_tool_summary
from agent_platform.contracts.api import MissionStreamEvent
from agent_platform.contracts.api import MissionRunResponse
from agent_platform.domain.enums import MissionStatus, ResultFormat
from agent_platform.domain.models import ExecutionTrace, MissionRequest, ToolCallRecord, utc_now


def test_parse_allowed_models() -> None:
    assert parse_allowed_models("a,b , c") == ["a", "b", "c"]
    assert parse_allowed_models(None) is None


def test_build_defaults_uses_namespace_values() -> None:
    args = Namespace(
        db_path="/tmp/demo.kuzu",
        api_url="http://127.0.0.1:8000",
        start_server=True,
        server_ready_timeout_seconds=30,
        preferred_model="openai/gpt-5.2",
        allowed_models="openai/gpt-4.1-mini,openai/gpt-5.2",
        output_schema=None,
        web_enabled=False,
        db_mutation_enabled=True,
    )
    defaults = build_defaults(args)
    assert defaults.db_path == "/tmp/demo.kuzu"
    assert defaults.api_url == "http://127.0.0.1:8000"
    assert defaults.start_server is True
    assert defaults.server_ready_timeout_seconds == 30
    assert defaults.preferred_model == "openai/gpt-5.2"
    assert defaults.allowed_models == ["openai/gpt-4.1-mini", "openai/gpt-5.2"]
    assert defaults.web_enabled is False


def test_format_tool_summary_none() -> None:
    trace = ExecutionTrace(
        trace_id="trace-1",
        request=MissionRequest(prompt="hello", db_path="/tmp/demo.kuzu"),
        model_sequence=["openai/gpt-4.1-mini"],
        tool_calls=[],
        db_mutations=[],
        docs_lookups=[],
        web_artifacts=[],
        compression_events=[],
        runtime_events=[],
        result="ok",
        started_at=utc_now(),
        completed_at=utc_now(),
    )
    assert format_tool_summary(trace) == "none"


def test_format_response_includes_ordered_tools() -> None:
    response = MissionRunResponse(
        status=MissionStatus.COMPLETED,
        result="done",
        result_format=ResultFormat.TEXT,
        final_model="openai/gpt-5.2",
        trace_id="trace-1",
        started_at=utc_now().isoformat(),
        completed_at=utc_now().isoformat(),
    )
    trace = ExecutionTrace(
        trace_id="trace-1",
        request=MissionRequest(prompt="hello", db_path="/tmp/demo.kuzu"),
        model_sequence=["openai/gpt-4.1-mini", "openai/gpt-5.2"],
        tool_calls=[
            ToolCallRecord(name="graph_schema", arguments={}, result_summary="schema returned"),
            ToolCallRecord(name="lookup_kuzu_docs", arguments={"query": "schema"}, result_summary="matched docs"),
        ],
        db_mutations=[],
        docs_lookups=[],
        web_artifacts=[],
        compression_events=[],
        runtime_events=[],
        result="done",
        started_at=utc_now(),
        completed_at=utc_now(),
    )

    rendered = format_response(response, trace)

    assert "Trace: trace-1" in rendered
    assert "Tools: graph_schema -> lookup_kuzu_docs" in rendered


def test_format_stream_event_and_final_response() -> None:
    event = MissionStreamEvent(
        event="tool.completed",
        data={"name": "graph_schema", "ok": True, "result_summary": "schema returned"},
    )
    response = MissionRunResponse(
        status=MissionStatus.COMPLETED,
        result={"answer": "ok"},
        result_format=ResultFormat.JSON_SCHEMA,
        final_model="openai/gpt-5.2",
        trace_id="trace-1",
        started_at=utc_now().isoformat(),
        completed_at=utc_now().isoformat(),
    )

    assert "Tool ok: graph_schema" in format_stream_event(event)
    final_rendered = format_final_stream_response(response, ["graph_schema"])
    assert "Tools: graph_schema" in final_rendered
