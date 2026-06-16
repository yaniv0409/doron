from __future__ import annotations

from agent_platform.application.runtime_builder import MissionRuntime
from agent_platform.domain.models import ToolCallRecord, WebArtifact


async def open_url(runtime: MissionRuntime, url: str) -> str:
    snapshot = await runtime.browser.navigate(url)
    runtime.context.browser_session_started = True
    runtime.context.web_findings.append(snapshot.text[:500])
    runtime.context.tool_calls.append(
        ToolCallRecord(
            name="open_url",
            arguments={"url": url},
            result_summary=f"opened {snapshot.url}",
        )
    )
    runtime.context.tool_summaries.append(f"open_url: {url}")
    artifact = WebArtifact(url=snapshot.url, title=snapshot.title, summary=snapshot.text[:500])
    runtime.context.web_artifacts.append(artifact)
    runtime.context.web_findings.append(artifact.model_dump_json())
    return snapshot.text


async def get_page_text(runtime: MissionRuntime) -> str:
    text = await runtime.browser.extract_text()
    runtime.context.tool_calls.append(
        ToolCallRecord(
            name="get_page_text",
            arguments={},
            result_summary=f"extracted {len(text)} chars",
        )
    )
    runtime.context.tool_summaries.append("get_page_text")
    runtime.context.web_findings.append(f"Extracted page text: {len(text)} chars")
    return text[:10_000]
