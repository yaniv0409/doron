from __future__ import annotations

from types import SimpleNamespace

from agent_platform.application.live_events import emit_runtime_event


def test_emit_runtime_event_includes_extra_payload_fields() -> None:
    captured: list[dict[str, object]] = []
    context = SimpleNamespace(
        trace_id="trace-1",
        runtime_events=[],
        event_hook=captured.append,
        progress_hook=None,
    )

    emit_runtime_event(
        context,
        "tool.started",
        "browser_open started",
        {"name": "browser_open"},
        stream_event="tool.started",
        payload={"name": "browser_open", "parameters": {"urls": ["https://example.com"], "reason": "research"}},
    )

    assert captured[0]["event"] == "tool.started"
    assert captured[0]["data"]["name"] == "browser_open"
    assert captured[0]["data"]["parameters"] == {
        "urls": ["https://example.com"],
        "reason": "research",
    }
