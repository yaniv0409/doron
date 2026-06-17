import asyncio
import threading
from types import SimpleNamespace

from agent_platform.application.runtime_builder import _record_browser_event
from agent_platform.infrastructure.browser import BrowserTelemetry
from agent_platform.domain.models import WebFetchResult
from agent_platform.tools import web_tools


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


def _build_runtime() -> SimpleNamespace:
    settings = SimpleNamespace(
        browser=SimpleNamespace(max_urls_per_batch=5, web_fetch_workers=2),
        compression=SimpleNamespace(enabled=False, tool_enabled=False),
    )
    runtime = SimpleNamespace(
        browser=SimpleNamespace(),
        services=SimpleNamespace(settings=settings),
        context=SimpleNamespace(
            tool_calls=[],
            tool_summaries=[],
            browser_session_started=False,
            web_findings=[],
            web_artifacts=[],
            reasoning_notes=[],
        ),
    )
    return runtime


def test_open_url_fetches_urls_in_parallel_and_preserves_order(monkeypatch) -> None:
    runtime = _build_runtime()
    started_threads: set[int] = set()
    barrier = threading.Barrier(2)

    async def noop(*_args, **_kwargs):
        return None

    def fake_worker(settings, url):
        started_threads.add(threading.get_ident())
        barrier.wait(timeout=5)
        if url.endswith("a"):
            return WebFetchResult(
                requested_url=url,
                url=url,
                ok=True,
                title="A",
                text="alpha",
                links=[],
                load_state="networkidle",
                browser_stage="extract_complete",
            )
        return WebFetchResult(
            requested_url=url,
            ok=False,
            error_type="browser_timeout",
            error_message="boom",
            retry_hint="try again",
        )

    monkeypatch.setattr(web_tools, "_fetch_url_worker", fake_worker)
    monkeypatch.setattr(web_tools, "maybe_auto_compress", noop)

    result = asyncio.run(web_tools.open_url(runtime, [" https://a ", "https://a", "https://b"]))

    assert result.ok is True
    assert result.tool == "browser_open"
    assert result.data["requested_urls"] == ["https://a", "https://b"]
    assert [item["requested_url"] for item in result.data["results"]] == ["https://a", "https://b"]
    assert result.data["successful_count"] == 1
    assert result.data["failed_count"] == 1
    assert len(started_threads) == 2
    assert runtime.context.tool_calls[0].name == "browser_open"
    assert runtime.context.tool_calls[0].result_summary == "fetched 1/2 urls"


def test_open_url_enforces_batch_limit(monkeypatch) -> None:
    runtime = _build_runtime()
    runtime.services.settings.browser.max_urls_per_batch = 1

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(web_tools, "maybe_auto_compress", noop)

    result = asyncio.run(web_tools.open_url(runtime, ["https://a", "https://b"]))

    assert result.ok is False
    assert result.error_type == "batch_limit_exceeded"
    assert runtime.context.tool_calls[0].error_type == "batch_limit_exceeded"
