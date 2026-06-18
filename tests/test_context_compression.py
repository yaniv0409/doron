import asyncio
from types import SimpleNamespace

from agent_platform.application.context_compression import ContextCompressor
from agent_platform.config.settings import AppSettings, CompressionSettings
from agent_platform.domain.models import MissionRequest, ModelDescriptor, RuntimeContext, ToolCallRecord, utc_now


class FakeChatClient:
    async def complete_json(self, *, model: str, system_prompt: str, user_prompt: str):
        return {
            "notes": ["distilled note"],
            "db_findings": ["important db fact"],
            "web_findings": ["important web fact"],
            "tool_summaries": ["important tool outcome"],
            "tool_outcomes": [
                {
                    "tool": "graph_read",
                    "status": "worked",
                    "reason": "reuse company basics query",
                    "arguments_summary": "query=MATCH (c:Company) RETURN c.ticker, c.name",
                    "result_summary": "returned 23 row(s)",
                    "error_type": None,
                    "error_message": None,
                    "retry_guidance": "repeat this query when company basics are needed",
                    "repeat": True,
                },
                {
                    "tool": "graph_read",
                    "status": "failed",
                    "reason": "avoid repeating bad Kuzu shape",
                    "arguments_summary": "query=MATCH (c:Company) RETURN properties(c)",
                    "result_summary": "Kuzu rejected PROPERTIES on NODE",
                    "error_type": "database_missing_object",
                    "error_message": "Function PROPERTIES did not receive correct arguments",
                    "retry_guidance": "do not retry unchanged; use explicit properties instead",
                    "repeat": False,
                },
            ],
            "unresolved_goals": ["continue investigating"],
            "notice": "Memory was compressed automatically. Use the distilled memory.",
        }


def build_runtime() -> SimpleNamespace:
    request = MissionRequest(
        prompt="Find all companies related to Elon and summarize them.",
        db_path="/tmp/demo.kuzu",
        allowed_models=["openai/gpt-4.1-mini", "openai/gpt-5.2"],
    )
    allowed_models = [
        ModelDescriptor(name="openai/gpt-4.1-mini", rank=10, context_window=4000),
        ModelDescriptor(name="openai/gpt-5.2", rank=100, context_window=32000),
    ]
    context = RuntimeContext(
        trace_id="trace-1",
        mission_request=request,
        started_at=utc_now(),
        current_model=allowed_models[0],
        allowed_models=allowed_models,
    )
    context.reasoning_notes = ["a" * 4000, "b" * 4000]
    context.db_findings = ["db finding"]
    context.web_findings = ["web finding"]
    context.tool_summaries = ["tool summary"]
    context.tool_calls = [
        ToolCallRecord(
            name="graph_read",
            arguments={"query": "MATCH (c:Company) RETURN c.ticker, c.name"},
            result_summary="returned 23 row(s)",
            reason="read company basics",
            ok=True,
        ),
        ToolCallRecord(
            name="graph_read",
            arguments={"query": "MATCH (c:Company) RETURN properties(c)"},
            result_summary="Function PROPERTIES did not receive correct arguments",
            reason="inspect all properties",
            ok=False,
            error_type="database_missing_object",
            error_message="Function PROPERTIES did not receive correct arguments",
        ),
    ]
    compression_settings = CompressionSettings(
        enabled=True,
        threshold_ratio=0.1,
        fallback_budget_chars=1000,
        min_growth_chars=100,
    )
    services = SimpleNamespace(
        settings=AppSettings(compression=compression_settings),
        chat_client=FakeChatClient(),
        model_catalog=SimpleNamespace(strongest_allowed=lambda allowed: allowed[-1]),
        context_compressor=ContextCompressor(compression_settings),
    )
    return SimpleNamespace(context=context, services=services)


def test_compression_preserves_original_mission_prompt() -> None:
    runtime = build_runtime()

    result = asyncio.run(
        runtime.services.context_compressor.compress(
            runtime,
            trigger="manual",
            reason="working memory is too large",
        )
    )

    assert result.ok is True
    assert runtime.context.mission_request.prompt == "Find all companies related to Elon and summarize them."
    assert runtime.context.compressed_memory is not None
    assert runtime.context.compressed_memory.notes == ["distilled note"]
    assert runtime.context.compression_events[0].original_prompt_preserved is True


def test_build_transfer_packet_keeps_original_prompt_and_notice() -> None:
    runtime = build_runtime()
    asyncio.run(
        runtime.services.context_compressor.compress(
            runtime,
            trigger="automatic",
            reason="context budget exceeded",
        )
    )

    packet = runtime.context.build_transfer_packet()

    assert packet.mission_prompt == runtime.context.mission_request.prompt
    assert packet.notice == "Memory was compressed automatically. Use the distilled memory."
    assert packet.notes == ["distilled note"]
    assert packet.worked_tool_patterns == [
        "graph_read | query=MATCH (c:Company) RETURN c.ticker, c.name | returned 23 row(s) | repeat this query when company basics are needed"
    ]
    assert packet.failed_tool_patterns == [
        "graph_read | query=MATCH (c:Company) RETURN properties(c) | Kuzu rejected PROPERTIES on NODE | do not retry unchanged; use explicit properties instead"
    ]


def test_handoff_prompt_includes_worked_and_failed_tool_patterns() -> None:
    from agent_platform.agent.prompts import build_handoff_prompt

    runtime = build_runtime()
    asyncio.run(
        runtime.services.context_compressor.compress(
            runtime,
            trigger="automatic",
            reason="context budget exceeded",
        )
    )

    prompt = build_handoff_prompt(runtime.context)

    assert "Worked tool patterns:" in prompt
    assert "Failed tool patterns to avoid repeating:" in prompt
    assert "RETURN c.ticker, c.name" in prompt
    assert "RETURN properties(c)" in prompt


def test_auto_compression_threshold_uses_working_memory_size() -> None:
    runtime = build_runtime()

    should_compress = runtime.services.context_compressor.should_auto_compress(runtime)

    assert should_compress is True
