from __future__ import annotations

from agent_platform.application.runtime_builder import MissionRuntime
from agent_platform.domain.exceptions import BrowserError
from agent_platform.domain.models import ToolResult, WebArtifact
from agent_platform.tools.compression_tools import maybe_auto_compress
from agent_platform.tools.result_utils import build_tool_call, error_result, success_result


async def open_url(runtime: MissionRuntime, url: str) -> ToolResult:
    try:
        snapshot = await runtime.browser.navigate(url)
    except BrowserError as exc:
        result = error_result(
            "open_url",
            "browser_navigation_error",
            str(exc),
            "Try a different URL, simplify the task, or continue without web data if possible.",
        )
        runtime.context.tool_calls.append(
            build_tool_call(
                "open_url",
                {"url": url},
                result_summary=result.error_message or "browser navigation failed",
                ok=False,
                error_type=result.error_type,
                error_message=result.error_message,
            )
        )
        runtime.context.tool_summaries.append(f"open_url failed: {url}")
        return result
    runtime.context.browser_session_started = True
    runtime.context.web_findings.append(snapshot.text[:500])
    runtime.context.tool_calls.append(
        build_tool_call(
            "open_url",
            {"url": url},
            result_summary=f"opened {snapshot.url}",
        )
    )
    runtime.context.tool_summaries.append(f"open_url: {url}")
    artifact = WebArtifact(
        url=snapshot.url,
        title=snapshot.title,
        summary=snapshot.text[:500],
        load_state=snapshot.load_state,
        links_count=len(snapshot.links),
    )
    runtime.context.web_artifacts.append(artifact)
    runtime.context.web_findings.append(artifact.model_dump_json())
    await maybe_auto_compress(runtime, "web navigation expanded working memory")
    return success_result(
        "open_url",
        {
            "url": snapshot.url,
            "title": snapshot.title,
            "text": snapshot.text,
            "links": [item.model_dump() for item in snapshot.links],
            "load_state": snapshot.load_state,
        },
        f"opened {snapshot.url}",
    )


async def get_page_text(runtime: MissionRuntime) -> ToolResult:
    try:
        snapshot = await runtime.browser.extract_text()
    except BrowserError as exc:
        result = error_result(
            "get_page_text",
            "browser_extract_error",
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
        f"extracted {len(snapshot.text)} chars",
    )
