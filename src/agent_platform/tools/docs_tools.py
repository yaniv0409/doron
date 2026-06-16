from __future__ import annotations

from agent_platform.application.runtime_builder import MissionRuntime
from agent_platform.domain.exceptions import DocumentationError
from agent_platform.domain.models import DocumentationLookupRecord, ToolResult
from agent_platform.tools.result_utils import build_tool_call, error_result, success_result


async def lookup_kuzu_docs(runtime: MissionRuntime, query: str) -> ToolResult:
    try:
        section = runtime.services.docs_repository.lookup(query)
    except DocumentationError as exc:
        result = error_result(
            "lookup_kuzu_docs",
            "docs_lookup_error",
            str(exc),
            "Try a broader Kuzu keyword or continue with schema inspection and query reformulation.",
        )
        runtime.context.tool_calls.append(
            build_tool_call(
                "lookup_kuzu_docs",
                {"query": query},
                result_summary=result.error_message or "docs lookup failed",
                ok=False,
                error_type=result.error_type,
                error_message=result.error_message,
            )
        )
        runtime.context.tool_summaries.append(f"lookup_kuzu_docs failed: {query}")
        return result
    excerpt = section.body[:2_000]
    runtime.context.docs_lookups.append(
        DocumentationLookupRecord(
            query=query,
            source_id=section.source_id,
            excerpt=excerpt,
        )
    )
    runtime.context.tool_calls.append(
        build_tool_call(
            "lookup_kuzu_docs",
            {"query": query},
            result_summary=f"matched {section.source_id}",
        )
    )
    runtime.context.tool_summaries.append(f"lookup_kuzu_docs: {query}")
    return success_result(
        "lookup_kuzu_docs",
        f"[{section.title}]\n{excerpt}",
        f"matched {section.source_id}",
    )
