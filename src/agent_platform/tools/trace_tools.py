from __future__ import annotations

from typing import Any

from agent_platform.application.runtime_builder import MissionRuntime
from agent_platform.domain.exceptions import ConfigurationError
from agent_platform.domain.models import ToolResult
from agent_platform.tools.result_utils import build_tool_call, error_result, success_result


async def read_trace_head(runtime: MissionRuntime, reason: str, chars: int | None = None) -> ToolResult:
    parent_trace_id = _parent_trace_id(runtime)
    limit = chars or runtime.services.settings.memory.maintenance_trace_head_chars
    text = runtime.services.trace_store.read_trace_head(parent_trace_id, limit)
    runtime.context.tool_calls.append(
        build_tool_call(
            "trace_head",
            {"reason": reason, "chars": limit},
            result_summary=f"returned {len(text)} trace chars",
            reason=reason,
        )
    )
    runtime.context.tool_summaries.append(f"trace_head: {len(text)} chars | reason: {reason}")
    return success_result(
        "trace_head",
        {
            "parent_trace_id": parent_trace_id,
            "chars": limit,
            "text": text,
        },
    )


async def grep_trace(
    runtime: MissionRuntime,
    pattern: str,
    reason: str,
    radius_lines: int | None = None,
) -> ToolResult:
    parent_trace_id = _parent_trace_id(runtime)
    settings = runtime.services.settings.memory
    radius = settings.maintenance_trace_grep_radius_lines if radius_lines is None else max(0, radius_lines)
    try:
        snippets = runtime.services.trace_store.grep_trace_text(
            parent_trace_id,
            pattern,
            radius_lines=radius,
            max_matches=settings.maintenance_trace_grep_max_matches,
            max_lines=settings.maintenance_trace_grep_max_lines,
        )
    except Exception as exc:
        return error_result(
            "trace_grep",
            "trace_grep_error",
            str(exc),
            "Use a simpler grep pattern or inspect the trace head first.",
        )
    runtime.context.tool_calls.append(
        build_tool_call(
            "trace_grep",
            {"pattern": pattern, "reason": reason, "radius_lines": radius},
            result_summary=f"returned {len(snippets)} trace snippet(s)",
            reason=reason,
        )
    )
    runtime.context.tool_summaries.append(f"trace_grep: {pattern[:80]} | reason: {reason}")
    return success_result(
        "trace_grep",
        {
            "parent_trace_id": parent_trace_id,
            "pattern": pattern,
            "radius_lines": radius,
            "snippets": snippets,
        },
    )


def _parent_trace_id(runtime: MissionRuntime) -> str:
    metadata = runtime.context.mission_request.mission_metadata or {}
    parent_trace_id = metadata.get("parent_trace_id")
    if not isinstance(parent_trace_id, str) or not parent_trace_id:
        raise ConfigurationError("trace tools require a parent_trace_id in mission metadata")
    return parent_trace_id
