import asyncio
from types import SimpleNamespace

from agent_platform.application.runtime_builder import _record_browser_event
from agent_platform.infrastructure.browser import BrowserTelemetry
from agent_platform.tools.web_tools import open_url


def test_record_browser_event_updates_runtime_events_and_progress_hook() -> None:
    progress_calls: list[dict[str, object]] = []
    context = SimpleNamespace(
        runtime_events=[],
        progress_hook=lambda **kwargs: progress_calls.append(kwargs),
    )

    _record_browser_event(
        context,
        BrowserTelemetry(
            stage="browser_navigation",
            message="network idle timeout",
            metadata={"url": "https://example.com", "timeout_ms": 15000},
        ),
    )

    assert len(context.runtime_events) == 1
    assert context.runtime_events[0].phase == "browser_navigation"
    assert context.runtime_events[0].message == "network idle timeout"
    assert progress_calls[0]["phase"] == "browser_navigation"
    assert progress_calls[0]["metadata"]["timeout_ms"] == 15000


class RecoverableTimeoutBrowser:
    async def navigate(self, url: str):
        from agent_platform.domain.exceptions import BrowserError

        raise BrowserError("browser_timeout: navigation exceeded 15000ms during page load or network idle")


def test_open_url_returns_recoverable_browser_timeout() -> None:
    runtime = SimpleNamespace(
        browser=RecoverableTimeoutBrowser(),
        context=SimpleNamespace(
            tool_calls=[],
            tool_summaries=[],
            browser_session_started=False,
            web_findings=[],
            web_artifacts=[],
        ),
    )

    result = asyncio.run(open_url(runtime, "https://example.com"))

    assert result.ok is False
    assert result.error_type == "browser_timeout"
    assert runtime.context.tool_calls[0].name == "open_url"
    assert runtime.context.tool_calls[0].error_type == "browser_timeout"
