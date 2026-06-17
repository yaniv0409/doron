from argparse import Namespace

from agent_platform.cli.chat import build_defaults, load_prompt_file, parse_allowed_models, parse_args, read_prompt_block
from agent_platform.cli.formatters import format_final_stream_response, format_stream_event
from agent_platform.contracts.api import MissionStreamEvent
from agent_platform.contracts.api import MissionRunResponse
from agent_platform.domain.enums import MissionStatus, ResultFormat
from agent_platform.domain.models import utc_now


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


def test_parse_args_supports_prompt_file() -> None:
    args = parse_args(["--db-path", "/tmp/demo.kuzu", "--prompt-file", "/tmp/prompt.md"])

    assert args.prompt_file == "/tmp/prompt.md"


def test_load_prompt_file_reads_verbatim(tmp_path) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("First line\n\nSecond line\n", encoding="utf-8")

    assert load_prompt_file(str(prompt_file)) == "First line\n\nSecond line\n"


def test_format_stream_event_and_final_response() -> None:
    event = MissionStreamEvent(
        event="tool.completed",
        data={
            "name": "graph_schema",
            "ok": True,
            "result_summary": "schema returned",
            "created_at": "2026-06-17T12:34:56Z",
        },
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

    assert "[12:34:56] Tool ok: graph_schema" in format_stream_event(event)
    final_rendered = format_final_stream_response(response, ["graph_schema"])
    assert "Tools: graph_schema" in final_rendered


def test_read_prompt_block_collects_multiline_prompt() -> None:
    lines = iter(["First line", "Second line", ""])

    def fake_input(_: str) -> str:
        return next(lines)

    assert read_prompt_block(fake_input) == "First line\nSecond line"


def test_read_prompt_block_treats_commands_only_when_buffer_empty() -> None:
    command_lines = iter(["/help"])

    def command_input(_: str) -> str:
        return next(command_lines)

    assert read_prompt_block(command_input) == "/help"

    prompt_lines = iter(["First line", "/help", ""])

    def prompt_input(_: str) -> str:
        return next(prompt_lines)

    assert read_prompt_block(prompt_input) == "First line\n/help"


def test_read_prompt_block_eof_behaviour() -> None:
    def eof_input(_: str) -> str:
        raise EOFError

    assert read_prompt_block(eof_input) is None

    lines = iter(["First line"])

    def eof_after_buffer(_: str) -> str:
        try:
            return next(lines)
        except StopIteration as exc:
            raise EOFError from exc

    assert read_prompt_block(eof_after_buffer) == "First line"
