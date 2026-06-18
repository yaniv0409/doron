from __future__ import annotations

from typing import Any

from agent_platform.application.runtime_builder import MissionRuntime
from agent_platform.domain.models import MemoryRetrievalRecord, ToolResult
from agent_platform.tools.result_utils import build_tool_call, error_result, success_result


async def search_memory(
    runtime: MissionRuntime,
    query: str,
    reason: str,
    *,
    kinds: list[str] | None = None,
    tool_name: str = "memory_search",
) -> ToolResult:
    blocked = _reserve_memory_tool_call(runtime, tool_name, reason, {"query": query, "kinds": kinds or []})
    if blocked is not None:
        return blocked
    results = await runtime.services.memory_manager.search(
        runtime.db,
        query,
        kinds=kinds,
        limit=runtime.services.settings.memory.preflight_limit,
        source=tool_name,
    )
    runtime.context.memory_retrievals.append(
        MemoryRetrievalRecord(query=query, kinds=kinds or [], results=results, source=tool_name)
    )
    runtime.context.tool_calls.append(
        build_tool_call(
            tool_name,
            {"query": query, "kinds": kinds or [], "reason": reason},
            result_summary=f"returned {len(results)} memory result(s)",
            reason=reason,
        )
    )
    runtime.context.tool_summaries.append(f"{tool_name}: {query[:120]} | reason: {reason}")
    return success_result(tool_name, [item.model_dump(mode="json") for item in results])


async def read_memory(runtime: MissionRuntime, ids: list[str], reason: str) -> ToolResult:
    blocked = _reserve_memory_tool_call(runtime, "memory_read", reason, {"ids": ids})
    if blocked is not None:
        return blocked
    results = runtime.services.memory_manager.read(runtime.db, ids)
    runtime.context.tool_calls.append(
        build_tool_call(
            "memory_read",
            {"ids": ids, "reason": reason},
            result_summary=f"returned {len(results)} memory record(s)",
            reason=reason,
        )
    )
    runtime.context.tool_summaries.append(f"memory_read: {len(ids)} ids | reason: {reason}")
    return success_result("memory_read", [item.model_dump(mode="json") for item in results])


async def write_memory(runtime: MissionRuntime, entries: list[dict[str, Any]], reason: str) -> ToolResult:
    blocked = _reserve_memory_tool_call(runtime, "memory_write", reason, {"entry_count": len(entries)})
    if blocked is not None:
        return blocked
    mutations = await runtime.services.memory_manager.write_entries(runtime.db, entries, reason=reason)
    runtime.context.memory_mutations.extend(mutations)
    runtime.context.tool_calls.append(
        build_tool_call(
            "memory_write",
            {"entry_count": len(entries), "reason": reason},
            result_summary=f"wrote {len(mutations)} memory record(s)",
            reason=reason,
        )
    )
    runtime.context.tool_summaries.append(f"memory_write: {len(mutations)} records | reason: {reason}")
    return success_result("memory_write", [item.model_dump(mode="json") for item in mutations])


async def update_memory(runtime: MissionRuntime, entries: list[dict[str, Any]], reason: str) -> ToolResult:
    blocked = _reserve_memory_tool_call(runtime, "memory_update", reason, {"entry_count": len(entries)})
    if blocked is not None:
        return blocked
    mutations = await runtime.services.memory_manager.update_entries(runtime.db, entries, reason=reason)
    runtime.context.memory_mutations.extend(mutations)
    runtime.context.tool_calls.append(
        build_tool_call(
            "memory_update",
            {"entry_count": len(entries), "reason": reason},
            result_summary=f"updated {len(mutations)} memory record(s)",
            reason=reason,
        )
    )
    runtime.context.tool_summaries.append(f"memory_update: {len(mutations)} records | reason: {reason}")
    return success_result("memory_update", [item.model_dump(mode="json") for item in mutations])


async def deprecate_memory(
    runtime: MissionRuntime,
    ids: list[str],
    reason: str,
    replacement_id: str | None = None,
) -> ToolResult:
    blocked = _reserve_memory_tool_call(runtime, "memory_deprecate", reason, {"ids": ids})
    if blocked is not None:
        return blocked
    mutations = runtime.services.memory_manager.deprecate_entries(
        runtime.db,
        ids,
        reason=reason,
        replacement_id=replacement_id,
    )
    runtime.context.memory_mutations.extend(mutations)
    runtime.context.tool_calls.append(
        build_tool_call(
            "memory_deprecate",
            {"ids": ids, "replacement_id": replacement_id, "reason": reason},
            result_summary=f"deprecated {len(mutations)} memory record(s)",
            reason=reason,
        )
    )
    runtime.context.tool_summaries.append(f"memory_deprecate: {len(mutations)} records | reason: {reason}")
    return success_result("memory_deprecate", [item.model_dump(mode="json") for item in mutations])


def _reserve_memory_tool_call(
    runtime: MissionRuntime,
    tool_name: str,
    reason: str,
    arguments: dict[str, Any],
) -> ToolResult | None:
    budget = runtime.context.memory_tool_call_budget
    if budget is None:
        runtime.context.memory_tool_calls_used += 1
        return None
    if runtime.context.memory_tool_calls_used >= budget:
        runtime.context.tool_calls.append(
            build_tool_call(
                tool_name,
                {**arguments, "reason": reason},
                result_summary="memory tool budget exhausted",
                reason=reason,
                ok=False,
                error_type="memory_rate_limit_exceeded",
                error_message=f"memory tool budget exhausted at {budget} calls",
            )
        )
        runtime.context.tool_summaries.append(f"{tool_name} blocked: memory budget exhausted | reason: {reason}")
        return error_result(
            tool_name,
            "memory_rate_limit_exceeded",
            f"memory tool budget exhausted at {budget} calls",
            "Use current memory findings and graph inspection to finish the task.",
        )
    runtime.context.memory_tool_calls_used += 1
    return None
