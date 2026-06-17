from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from agent_platform.application.live_events import emit_runtime_event
from agent_platform.application.runtime_builder import MissionRuntime
from agent_platform.config.settings import BrowserSettings
from agent_platform.domain.exceptions import BrowserError
from agent_platform.domain.models import ToolResult, WebArtifact, WebFetchBatchResult, WebFetchResult
from agent_platform.tools.compression_tools import maybe_auto_compress
from agent_platform.tools.result_utils import build_tool_call, error_result, success_result
from agent_platform.infrastructure.browser import PlaywrightBrowserEngine


async def open_url(runtime: MissionRuntime, urls: list[str]) -> ToolResult:
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
                {"urls": urls},
                result_summary=result.error_message or "no urls provided",
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
                {"urls": normalized_urls},
                result_summary=result.error_message or "batch limit exceeded",
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
        },
    )

    successful_count = 0
    failed_count = 0
    worker_limit = _worker_limit(runtime.services.settings.browser, len(normalized_urls))
    browser_settings = _copy_browser_settings(runtime.services.settings.browser)
    for index, url in enumerate(normalized_urls):
        emit_runtime_event(
            runtime.context,
            "browser_fetch_started",
            "browser fetch started",
            {"requested_url": url, "index": index},
        )
    results = _fetch_batch_sync(browser_settings, normalized_urls, worker_limit)
    items = list(results)
    for item in items:
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
        requested_urls=normalized_urls,
        results=[item for item in items if item is not None],
        successful_count=successful_count,
        failed_count=failed_count,
        max_workers=worker_limit,
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
        },
    )
    runtime.context.tool_calls.append(
        build_tool_call(
            "browser_open",
            {"urls": normalized_urls},
            result_summary=f"fetched {successful_count}/{len(normalized_urls)} urls",
        )
    )
    runtime.context.tool_summaries.append(
        f"browser_open batch: {successful_count}/{len(normalized_urls)} urls",
    )
    if successful_count > 0:
        await maybe_auto_compress(runtime, "web navigation expanded working memory")
    if successful_count == 0:
        return ToolResult(
            ok=False,
            tool="browser_open",
            error_type="all_urls_failed",
            error_message="all urls failed to fetch",
            retry_hint="Split the batch, try different URLs, or continue without web data if possible.",
            data=batch_result.model_dump(mode="json"),
        )
    return success_result("browser_open", batch_result.model_dump(mode="json"))


async def get_page_text(runtime: MissionRuntime) -> ToolResult:
    try:
        snapshot = await runtime.browser.extract_text()
    except BrowserError as exc:
        error_type = "browser_timeout" if "browser_timeout:" in str(exc) else "browser_extract_error"
        result = error_result(
            "get_page_text",
            error_type,
            str(exc),
            "Open a page first or continue without web text if the answer is still possible.",
        )
        runtime.context.tool_calls.append(
            build_tool_call(
                "get_page_text",
                {},
                result_summary=result.error_message or "browser text extraction failed",
                ok=False,
                error_type=result.error_type,
                error_message=result.error_message,
            )
        )
        runtime.context.tool_summaries.append("get_page_text failed")
        return result
    runtime.context.tool_calls.append(
        build_tool_call(
            "get_page_text",
            {},
            result_summary=f"extracted {len(snapshot.text)} chars",
        )
    )
    runtime.context.tool_summaries.append("get_page_text")
    runtime.context.web_findings.append(f"Extracted page text: {len(snapshot.text)} chars")
    await maybe_auto_compress(runtime, "web text extraction expanded working memory")
    return success_result(
        "get_page_text",
        {
            "url": snapshot.url,
            "title": snapshot.title,
            "text": snapshot.text,
            "links": [item.model_dump() for item in snapshot.links],
            "load_state": snapshot.load_state,
        },
    )


def _fetch_url_worker(settings: BrowserSettings, url: str) -> WebFetchResult:
    return asyncio.run(_fetch_url_worker_async(settings, url))


async def _fetch_url_worker_async(settings: BrowserSettings, url: str) -> WebFetchResult:
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


def _fetch_batch_sync(
    settings: BrowserSettings,
    urls: list[str],
    worker_limit: int,
) -> list[WebFetchResult]:
    with ThreadPoolExecutor(max_workers=worker_limit, thread_name_prefix="web-fetch") as executor:
        futures = [executor.submit(_fetch_url_worker, settings, url) for url in urls]
        return [future.result() for future in futures]


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


def _worker_limit(settings: BrowserSettings, url_count: int) -> int:
    return max(1, min(settings.web_fetch_workers, url_count))


def _copy_browser_settings(settings: BrowserSettings) -> BrowserSettings:
    if hasattr(settings, "model_copy"):
        return settings.model_copy(deep=True)
    return settings
