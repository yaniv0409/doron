from pathlib import Path

from agent_platform.config.settings import TraceSettings
from agent_platform.domain.models import CompressionEvent, ExecutionTrace, MissionRequest, RuntimeEvent, ToolCallRecord, WebArtifact, utc_now
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
            WebArtifact(
                url="https://example.com",
                title="Example",
                summary="demo",
                load_state="networkidle",
                browser_stage="extract_complete",
                links_count=2,
            ),
        ],
        compression_events=[
            CompressionEvent(
                trigger="manual",
                summarizer_model="openai/gpt-5.2",
                reason="reduce context",
                size_before=20000,
                size_after=4000,
                preview="distilled note",
            )
        ],
        runtime_events=[
            RuntimeEvent(
                phase="agent_setup",
                message="agent session created",
                metadata={"tool_count": 2},
            )
        ],
        result={"ok": True},
        started_at=utc_now(),
        completed_at=utc_now(),
    )

    store.write_trace(trace)
    loaded = store.read_trace("trace-123")

    assert [item.name for item in loaded.tool_calls] == ["open_url", "switch_model"]
    assert loaded.web_artifacts[0].url == "https://example.com"
    assert loaded.web_artifacts[0].load_state == "networkidle"
    assert loaded.web_artifacts[0].browser_stage == "extract_complete"
    assert loaded.web_artifacts[0].links_count == 2
    assert loaded.compression_events[0].summarizer_model == "openai/gpt-5.2"
    assert loaded.runtime_events[0].phase == "agent_setup"


def test_checkpoint_copies_file_database_path(tmp_path: Path) -> None:
    source = tmp_path / "demo.kuzu"
    source.write_text("db", encoding="utf-8")
    store = TraceStore(
        TraceSettings(
            directory=tmp_path / "traces",
            checkpoint_directory=tmp_path / "checkpoints",
        )
    )

    checkpoint = store.create_checkpoint("trace-file", str(source))

    assert checkpoint.exists()
    assert checkpoint.read_text(encoding="utf-8") == "db"


def test_checkpoint_copies_directory_database_path(tmp_path: Path) -> None:
    source = tmp_path / "demo-db"
    source.mkdir()
    (source / "data.bin").write_text("db", encoding="utf-8")
    store = TraceStore(
        TraceSettings(
            directory=tmp_path / "traces",
            checkpoint_directory=tmp_path / "checkpoints",
        )
    )

    checkpoint = store.create_checkpoint("trace-dir", str(source))

    assert checkpoint.is_dir()
    assert (checkpoint / "data.bin").read_text(encoding="utf-8") == "db"
