from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from agent_platform.application.live_events import emit_runtime_event
from agent_platform.application.runtime_builder import MissionRuntime
from agent_platform.domain.exceptions import BrowserError, ContextRefreshRequested, ModelSwitchRequested
from agent_platform.domain.models import ToolResult, WebArtifact, WebFetchBatchResult, WebFetchResult, WebSearchHit, WebSearchResponse
from agent_platform.infrastructure.browser import PlaywrightBrowserEngine
from agent_platform.tools.compression_tools import maybe_auto_compress
from agent_platform.tools.result_utils import build_tool_call, error_result, success_result

try:
    from ddgs import DDGS
except ImportError:  # pragma: no cover
    DDGS = None

_DEFAULT_WEB_SEARCH_RESULT_LIMIT = 5


async def open_url(runtime: MissionRuntime, urls: list[str], reason: str) -> ToolResult:
    reservation = _reserve_web_tool_call(runtime, "browser_open", reason, {"urls": urls})
    if reservation is not None:
        return reservation

    normalized_urls = _normalize_urls(urls)
    max_urls = runtime.services.settings.browser.max_urls_per_batch
    if not normalized_urls:
        result = error_result(
            "browser_open",
            "invalid_request",
            "browser_open requires at least one non-empty url",
            "Pass one or more URLs.",
        )
        runtime.context.tool_calls.append(
            build_tool_call(
                "browser_open",
                {"urls": urls, "reason": reason},
                result_summary=result.error_message or "no urls provided",
                reason=reason,
                ok=False,
                error_type=result.error_type,
                error_message=result.error_message,
            )
        )
        return result
    if len(normalized_urls) > max_urls:
        result = error_result(
            "browser_open",
            "batch_limit_exceeded",
            f"browser_open accepts at most {max_urls} urls per call",
            "Split the URLs into smaller batches and try again.",
        )
        runtime.context.tool_calls.append(
            build_tool_call(
                "browser_open",
                {"urls": normalized_urls, "reason": reason},
                result_summary=result.error_message or "batch limit exceeded",
                reason=reason,
                ok=False,
                error_type=result.error_type,
                error_message=result.error_message,
            )
        )
        return result

    emit_runtime_event(
        runtime.context,
        "browser_batch_started",
        "browser batch fetch started",
        {
            "requested_urls": normalized_urls,
            "url_count": len(normalized_urls),
            "max_workers": _worker_limit(runtime.services.settings.browser, len(normalized_urls)),
            "reason": reason,
            "web_tool_calls_used": runtime.context.web_tool_calls_used,
            "web_tool_calls_remaining": runtime.context.web_tool_calls_remaining(),
        },
    )

    worker_limit = _worker_limit(runtime.services.settings.browser, len(normalized_urls))
    browser_settings = _copy_browser_settings(runtime.services.settings.browser)
    try:
        results = _fetch_batch_sync(browser_settings, normalized_urls, worker_limit)
    except BrowserError as exc:
        result = error_result(
            "browser_open",
            "browser_runtime_error",
            str(exc),
            "Split the batch, try a different URL, or continue without web data if possible.",
        )
        _record_browser_failure(
            runtime,
            "browser_open",
            {"urls": normalized_urls, "reason": reason},
            reason,
            result,
        )
        return result
    except Exception as exc:  # pragma: no cover
        if _is_control_flow_exception(exc):
            raise
        result = error_result(
            "browser_open",
            "browser_runtime_error",
            str(exc),
            "Split the batch, try a different URL, or continue without web data if possible.",
        )
        _record_browser_failure(
            runtime,
            "browser_open",
            {"urls": normalized_urls, "reason": reason},
            reason,
            result,
        )
        return result

    successful_count = 0
    failed_count = 0
    for item in results:
        emit_runtime_event(
            runtime.context,
            "browser_fetch_completed" if item.ok else "browser_fetch_failed",
            "browser fetch completed" if item.ok else "browser fetch failed",
            {
                "requested_url": item.requested_url,
                "url": item.url,
                "ok": item.ok,
                "error_type": item.error_type,
                "error_message": item.error_message,
                "load_state": item.load_state,
                "browser_stage": item.browser_stage,
                "reason": reason,
            },
        )
        if item.ok:
            successful_count += 1
            runtime.context.browser_session_started = True
            runtime.context.web_findings.append(f"{item.url}: {item.text[:500] if item.text else ''}")
            artifact = WebArtifact(
                url=item.url or item.requested_url,
                title=item.title,
                summary=item.text[:500] if item.text else None,
                load_state=item.load_state,
                browser_stage=item.browser_stage,
                links_count=len(item.links),
            )
            runtime.context.web_artifacts.append(artifact)
            runtime.context.web_findings.append(artifact.model_dump_json())
        else:
            failed_count += 1

    batch_result = WebFetchBatchResult(
        reason=reason,
        requested_urls=normalized_urls,
        results=results,
        successful_count=successful_count,
        failed_count=failed_count,
        max_workers=worker_limit,
        web_tool_calls_used=runtime.context.web_tool_calls_used,
        web_tool_calls_remaining=runtime.context.web_tool_calls_remaining(),
    )
    emit_runtime_event(
        runtime.context,
        "browser_batch_completed",
        "browser batch fetch completed",
        {
            "requested_urls": normalized_urls,
            "successful_count": successful_count,
            "failed_count": failed_count,
            "max_workers": worker_limit,
            "reason": reason,
            "web_tool_calls_used": runtime.context.web_tool_calls_used,
            "web_tool_calls_remaining": runtime.context.web_tool_calls_remaining(),
        },
    )
    runtime.context.tool_calls.append(
        build_tool_call(
            "browser_open",
            {"urls": normalized_urls, "reason": reason},
            result_summary=f"fetched {successful_count}/{len(normalized_urls)} urls; web budget remaining {runtime.context.web_tool_calls_remaining()}",
            reason=reason,
        )
    )
    runtime.context.tool_summaries.append(
        f"browser_open batch: {successful_count}/{len(normalized_urls)} urls | reason: {reason}",
    )
    if successful_count == 0:
        return ToolResult(
            ok=False,
            tool="browser_open",
            error_type="all_urls_failed",
            error_message="all urls failed to fetch",
            retry_hint="Split the batch, try different URLs, or continue without web data if possible.",
            data=batch_result.model_dump(mode="json"),
        )
    await maybe_auto_compress(runtime, "web navigation expanded working memory")
    return success_result("browser_open", batch_result.model_dump(mode="json"))


async def get_page_text(runtime: MissionRuntime, reason: str) -> ToolResult:
    reservation = _reserve_web_tool_call(runtime, "browser_text", reason, {"reason": reason})
    if reservation is not None:
        return reservation
    try:
        snapshot = await runtime.browser.extract_text()
    except BrowserError as exc:
        error_type = "browser_timeout" if "browser_timeout:" in str(exc) else "browser_extract_error"
        result = error_result(
            "browser_text",
            error_type,
            str(exc),
            "Open a page first or continue without web text if the answer is still possible.",
        )
        _record_browser_text_failure(runtime, reason, result)
        return result
    except Exception as exc:  # pragma: no cover
        if _is_control_flow_exception(exc):
            raise
        result = error_result(
            "browser_text",
            "browser_runtime_error",
            str(exc),
            "Open a page first or continue without web text if the answer is still possible.",
        )
        _record_browser_text_failure(runtime, reason, result)
        return result
    runtime.context.tool_calls.append(
        build_tool_call(
            "get_page_text",
            {"reason": reason},
            result_summary=f"extracted {len(snapshot.text)} chars; web budget remaining {runtime.context.web_tool_calls_remaining()}",
            reason=reason,
        )
    )
    runtime.context.tool_summaries.append(f"get_page_text | reason: {reason}")
    runtime.context.web_findings.append(f"Extracted page text: {len(snapshot.text)} chars")
    await maybe_auto_compress(runtime, "web text extraction expanded working memory")
    return success_result(
        "get_page_text",
        {
            "reason": reason,
            "web_tool_calls_used": runtime.context.web_tool_calls_used,
            "web_tool_calls_remaining": runtime.context.web_tool_calls_remaining(),
            "url": snapshot.url,
            "title": snapshot.title,
            "text": snapshot.text,
            "links": [item.model_dump() for item in snapshot.links],
            "load_state": snapshot.load_state,
        },
    )


async def web_search(runtime: MissionRuntime, query: str, reason: str) -> ToolResult:
    if DDGS is None:
        result = error_result(
            "web_search",
            "duckduckgo_search_unavailable",
            "DuckDuckGo search support is not installed",
            'Install the `ddgs` package or the `duckduckgo` optional group.',
        )
        _record_web_search_failure(runtime, query, reason, result)
        return result

    arguments = {"query": query, "reason": reason}
    runtime.context.tool_summaries.append(f"web_search | reason: {reason}")
    emit_runtime_event(
        runtime.context,
        "web_search_started",
        "web search started",
        {
            "query": query,
            "reason": reason,
        },
    )
    try:
        raw_results = await asyncio.to_thread(_duckduckgo_search, query, _DEFAULT_WEB_SEARCH_RESULT_LIMIT)
    except Exception as exc:  # pragma: no cover
        if _is_control_flow_exception(exc):
            raise
        result = error_result(
            "web_search",
            "web_search_error",
            str(exc),
            "Try a narrower query or continue with browser tools if a specific page is already known.",
        )
        _record_web_search_failure(runtime, query, reason, result)
        return result

    hits = [
        WebSearchHit(
            title=item["title"],
            url=item["href"],
            snippet=_normalize_search_snippet(item["body"]),
        )
        for item in raw_results
    ]
    response = WebSearchResponse(query=query, reason=reason, hits=hits)
    runtime.context.tool_calls.append(
        build_tool_call(
            "web_search",
            arguments,
            result_summary=f"returned {len(hits)} web result(s)",
            reason=reason,
        )
    )
    emit_runtime_event(
        runtime.context,
        "web_search_completed",
        "web search completed",
        {
            "query": query,
            "reason": reason,
            "result_count": len(hits),
            "top_result": hits[0].model_dump(mode="json") if hits else None,
        },
    )
    return success_result("web_search", response.model_dump(mode="json"))


def _reserve_web_tool_call(
    runtime: MissionRuntime,
    tool_name: str,
    reason: str,
    arguments: dict[str, Any],
) -> ToolResult | None:
    budget = runtime.context.web_tool_budget()
    used = runtime.context.web_tool_calls_used
    if used >= budget:
        runtime.context.tool_calls.append(
            build_tool_call(
                tool_name,
                {**arguments, "reason": reason},
                result_summary="web tool budget exhausted",
                reason=reason,
                ok=False,
                error_type="web_rate_limit_exceeded",
                error_message=f"web tool budget exhausted at {budget} calls per mission",
            )
        )
        runtime.context.tool_summaries.append(f"{tool_name} blocked: web budget exhausted | reason: {reason}")
        runtime.context.reasoning_notes.append(
            "web budget exhausted; stop browsing and use memory or the database",
        )
        emit_runtime_event(
            runtime.context,
            "web_rate_limit_exceeded",
            "web tool budget exhausted",
            {
                "tool": tool_name,
                "reason": reason,
                "web_tool_calls_used": used,
                "web_tool_calls_remaining": 0,
                "web_tool_budget": budget,
            },
        )
        return ToolResult(
            ok=False,
            tool=tool_name,
            error_type="web_rate_limit_exceeded",
            error_message=f"web tool budget exhausted at {budget} calls per mission",
            retry_hint="Continue using memory and the database. Stop browsing unless it is essential.",
            data={
                "reason": reason,
                "web_tool_calls_used": used,
                "web_tool_calls_remaining": 0,
                "web_tool_budget": budget,
            },
        )
    runtime.context.web_tool_calls_used += 1
    emit_runtime_event(
        runtime.context,
        "web_budget_reserved",
        "web tool call reserved",
        {
            "tool": tool_name,
            "reason": reason,
            "web_tool_calls_used": runtime.context.web_tool_calls_used,
            "web_tool_calls_remaining": runtime.context.web_tool_calls_remaining(),
            "web_tool_budget": budget,
        },
    )
    return None


def _fetch_batch_sync(
    settings: Any,
    urls: list[str],
    worker_limit: int,
) -> list[WebFetchResult]:
    with ThreadPoolExecutor(max_workers=worker_limit, thread_name_prefix="web-fetch") as executor:
        futures = [executor.submit(_fetch_url_worker, settings, url) for url in urls]
        return [future.result() for future in futures]


def _fetch_url_worker(settings: Any, url: str) -> WebFetchResult:
    return asyncio.run(_fetch_url_worker_async(settings, url))


async def _fetch_url_worker_async(settings: Any, url: str) -> WebFetchResult:
    browser = PlaywrightBrowserEngine(settings)
    try:
        snapshot = await browser.navigate(url)
        return WebFetchResult(
            requested_url=url,
            url=snapshot.url,
            ok=True,
            title=snapshot.title,
            text=snapshot.text,
            links=snapshot.links,
            load_state=snapshot.load_state,
            browser_stage=snapshot.browser_stage,
        )
    except BrowserError as exc:
        error_type = "browser_timeout" if "browser_timeout:" in str(exc) else "browser_navigation_error"
        return WebFetchResult(
            requested_url=url,
            ok=False,
            error_type=error_type,
            error_message=str(exc),
            retry_hint="Split the batch, try a different URL, or continue without web data if possible.",
        )
    except Exception as exc:  # pragma: no cover
        return WebFetchResult(
            requested_url=url,
            ok=False,
            error_type="browser_runtime_error",
            error_message=str(exc),
            retry_hint="Split the batch, try a different URL, or continue without web data if possible.",
        )
    finally:
        await browser.close()


def _normalize_urls(urls: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for url in urls:
        cleaned = url.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def _worker_limit(settings: Any, url_count: int) -> int:
    return max(1, min(settings.web_fetch_workers, url_count))


def _copy_browser_settings(settings: Any) -> Any:
    if hasattr(settings, "model_copy"):
        return settings.model_copy(deep=True)
    return settings


def _record_browser_failure(
    runtime: MissionRuntime,
    tool_name: str,
    arguments: dict[str, Any],
    reason: str,
    result: ToolResult,
) -> None:
    runtime.context.tool_calls.append(
        build_tool_call(
            tool_name,
            arguments,
            result_summary=result.error_message or "browser request failed",
            reason=reason,
            ok=False,
            error_type=result.error_type,
            error_message=result.error_message,
        )
    )
    runtime.context.tool_summaries.append(f"{tool_name} failed | reason: {reason}")


def _record_browser_text_failure(runtime: MissionRuntime, reason: str, result: ToolResult) -> None:
    runtime.context.tool_calls.append(
        build_tool_call(
            "get_page_text",
            {"reason": reason},
            result_summary=result.error_message or "browser text extraction failed",
            reason=reason,
            ok=False,
            error_type=result.error_type,
            error_message=result.error_message,
        )
    )
    runtime.context.tool_summaries.append(f"get_page_text failed | reason: {reason}")


def _record_web_search_failure(runtime: MissionRuntime, query: str, reason: str, result: ToolResult) -> None:
    runtime.context.tool_calls.append(
        build_tool_call(
            "web_search",
            {"query": query, "reason": reason},
            result_summary=result.error_message or "web search failed",
            reason=reason,
            ok=False,
            error_type=result.error_type,
            error_message=result.error_message,
        )
    )
    runtime.context.tool_summaries.append(f"web_search failed | reason: {reason}")


def _normalize_search_snippet(text: str) -> str:
    cleaned = " ".join(text.split())
    return cleaned[:240]


def _duckduckgo_search(query: str, max_results: int | None) -> list[dict[str, Any]]:
    client = DDGS()
    return client.text(query, max_results=max_results)


def _is_control_flow_exception(exc: Exception) -> bool:
    return isinstance(exc, (ContextRefreshRequested, ModelSwitchRequested, asyncio.CancelledError))
