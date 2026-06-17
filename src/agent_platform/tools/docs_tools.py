from __future__ import annotations

import asyncio

from agent_platform.application.runtime_builder import MissionRuntime
from agent_platform.domain.exceptions import ContextRefreshRequested, DocumentationError, ModelSwitchRequested
from agent_platform.domain.models import DocumentationLookupRecord, ToolResult
from agent_platform.tools.compression_tools import maybe_auto_compress
from agent_platform.tools.result_utils import build_tool_call, error_result, success_result


async def lookup_kuzu_docs(runtime: MissionRuntime, query: str, reason: str) -> ToolResult:
    try:
        section = runtime.services.docs_repository.lookup(query)
    except DocumentationError as exc:
        result = error_result(
            "lookup_kuzu_docs",
            "docs_lookup_error",
            str(exc),
            "Try a broader Kuzu keyword or continue with schema inspection and query reformulation.",
        )
        _record_docs_failure(
            runtime,
            query,
            reason,
            result,
            f"lookup_kuzu_docs failed: {query} | reason: {reason}",
        )
        return result
    except Exception as exc:  # pragma: no cover
        if _is_control_flow_exception(exc):
            raise
        result = error_result(
            "lookup_kuzu_docs",
            "docs_runtime_error",
            str(exc),
            "Try a broader Kuzu keyword or continue with schema inspection and query reformulation.",
        )
        _record_docs_failure(
            runtime,
            query,
            reason,
            result,
            f"lookup_kuzu_docs failed: {query} | reason: {reason}",
        )
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
            {"query": query, "reason": reason},
            result_summary=f"matched {section.source_id}",
            reason=reason,
        )
    )
    runtime.context.tool_summaries.append(f"lookup_kuzu_docs: {query} | reason: {reason}")
    await maybe_auto_compress(runtime, "documentation lookup expanded working memory")
    return success_result(
        "lookup_kuzu_docs",
        f"[{section.title}]\n{excerpt}",
    )


def _record_docs_failure(
    runtime: MissionRuntime,
    query: str,
    reason: str,
    result: ToolResult,
    summary: str,
) -> None:
    runtime.context.tool_calls.append(
        build_tool_call(
            "lookup_kuzu_docs",
            {"query": query, "reason": reason},
            result_summary=result.error_message or "docs lookup failed",
            reason=reason,
            ok=False,
            error_type=result.error_type,
            error_message=result.error_message,
        )
    )
    runtime.context.tool_summaries.append(summary)


def _is_control_flow_exception(exc: Exception) -> bool:
    return isinstance(exc, (ContextRefreshRequested, ModelSwitchRequested, asyncio.CancelledError))
