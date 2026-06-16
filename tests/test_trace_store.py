from pathlib import Path

from agent_platform.config.settings import TraceSettings
from agent_platform.domain.models import ExecutionTrace, MissionRequest, ToolCallRecord, WebArtifact, utc_now
from agent_platform.infrastructure.trace_store import TraceStore


def test_trace_store_round_trip_preserves_tool_calls_and_web_artifacts(tmp_path: Path) -> None:
    store = TraceStore(
        TraceSettings(
            directory=tmp_path / "traces",
            checkpoint_directory=tmp_path / "checkpoints",
        )
    )
    trace = ExecutionTrace(
        trace_id="trace-123",
        request=MissionRequest(prompt="hello", db_path="/tmp/db.kuzu"),
        model_sequence=["openai/gpt-4.1-mini"],
        tool_calls=[
            ToolCallRecord(
                name="open_url",
                arguments={"url": "https://example.com"},
                result_summary="opened page",
            ),
            ToolCallRecord(
                name="switch_model",
                arguments={"target_model": "openai/gpt-5.2", "reason": "need stronger model"},
                result_summary="requested switch",
            ),
        ],
        db_mutations=[],
        docs_lookups=[],
        web_artifacts=[
            WebArtifact(url="https://example.com", title="Example", summary="demo"),
        ],
        result={"ok": True},
        started_at=utc_now(),
        completed_at=utc_now(),
    )

    store.write_trace(trace)
    loaded = store.read_trace("trace-123")

    assert [item.name for item in loaded.tool_calls] == ["open_url", "switch_model"]
    assert loaded.web_artifacts[0].url == "https://example.com"
