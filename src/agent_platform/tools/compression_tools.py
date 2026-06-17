from __future__ import annotations

from agent_platform.application.runtime_builder import MissionRuntime
from agent_platform.domain.exceptions import ContextRefreshRequested
from agent_platform.domain.models import ToolResult
from agent_platform.tools.result_utils import build_tool_call


async def compress_context(runtime: MissionRuntime, reason: str) -> ToolResult:
    if runtime.context.compression_in_progress:
        return ToolResult(
            ok=False,
            tool="compress_context",
            error_type="compression_in_progress",
            error_message="context compression is already in progress",
            retry_hint="Continue the mission and retry compression later if needed.",
            data=None,
        )
    runtime.context.compression_in_progress = True
    try:
        result = await runtime.services.context_compressor.compress(
            runtime,
            trigger="manual",
            reason=reason,
        )
    finally:
        runtime.context.compression_in_progress = False
    runtime.context.tool_calls.append(
        build_tool_call(
            "compress_context",
            {"reason": reason},
            result_summary=result.data.get("preview", "context compressed") if result.ok and isinstance(result.data, dict) else (result.error_message or "compression failed"),
            reason=reason,
            ok=result.ok,
            error_type=result.error_type,
            error_message=result.error_message,
        )
    )
    runtime.context.tool_summaries.append(f"compress_context: {reason}")
    if result.ok:
        runtime.context.pending_context_refresh_reason = f"Manual context compression: {reason}"
        raise ContextRefreshRequested(reason)
    return result


async def maybe_auto_compress(runtime: MissionRuntime, reason: str) -> None:
    if not runtime.services.context_compressor.should_auto_compress(runtime):
        return
    if runtime.context.compression_in_progress:
        return
    runtime.context.compression_in_progress = True
    try:
        result = await runtime.services.context_compressor.compress(
            runtime,
            trigger="automatic",
            reason=reason,
        )
    finally:
        runtime.context.compression_in_progress = False
    if not result.ok:
        runtime.context.reasoning_notes.append(
            f"automatic context compression failed: {result.error_message}",
        )
        return
    runtime.context.tool_calls.append(
        build_tool_call(
            "auto_compress_context",
            {"reason": reason},
            result_summary=result.data.get("preview", "automatic context compression") if isinstance(result.data, dict) else "automatic context compression",
            reason=reason,
        )
    )
    runtime.context.tool_summaries.append("automatic context compression")
    runtime.context.pending_context_refresh_reason = f"Automatic context compression: {reason}"
    raise ContextRefreshRequested(reason)
