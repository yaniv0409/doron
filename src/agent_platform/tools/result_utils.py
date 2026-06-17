from __future__ import annotations

from typing import Any

from agent_platform.domain.models import ToolCallRecord, ToolResult


def success_result(tool: str, data: Any) -> ToolResult:
    return ToolResult(
        ok=True,
        tool=tool,
        data=data,
        retry_hint=None,
        error_type=None,
        error_message=None,
    )


def error_result(
    tool: str,
    error_type: str,
    error_message: str,
    retry_hint: str,
) -> ToolResult:
    return ToolResult(
        ok=False,
        tool=tool,
        error_type=error_type,
        error_message=error_message,
        retry_hint=retry_hint,
        data=None,
    )


def build_tool_call(
    tool: str,
    arguments: dict[str, Any],
    result_summary: str,
    *,
    reason: str | None = None,
    ok: bool = True,
    error_type: str | None = None,
    error_message: str | None = None,
) -> ToolCallRecord:
    return ToolCallRecord(
        name=tool,
        arguments=arguments,
        result_summary=result_summary,
        reason=reason,
        ok=ok,
        error_type=error_type,
        error_message=error_message,
    )
