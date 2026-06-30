from __future__ import annotations

from typing import Any

from agent_platform.application.runtime_builder import MissionRuntime
from agent_platform.domain.models import MemoryRetrievalRecord, ToolResult
from agent_platform.tools.result_utils import build_tool_call, error_result, success_result


async def skill_search(
    runtime: MissionRuntime,
    query: str,
    reason: str,
    *,
    tool_name: str = "skill_search",
) -> ToolResult:
    blocked = _reserve_skill_tool_call(runtime, tool_name, reason, {"query": query})
    if blocked is not None:
        return blocked
    results = await runtime.services.memory_manager.search(
        runtime.memory_db,
        query,
        kinds=["skill"],
        limit=runtime.services.settings.memory.preflight_limit,
        source=tool_name,
    )
    runtime.context.memory_retrievals.append(
        MemoryRetrievalRecord(query=query, kinds=["skill"], results=results, source=tool_name)
    )
    runtime.context.tool_calls.append(
        build_tool_call(
            tool_name,
            {"query": query, "reason": reason},
            result_summary=f"returned {len(results)} skill result(s)",
            reason=reason,
        )
    )
    runtime.context.tool_summaries.append(f"{tool_name}: {query[:120]} | reason: {reason}")
    return success_result(tool_name, [item.model_dump(mode="json") for item in results])


async def skill_read(runtime: MissionRuntime, ids: list[str], reason: str) -> ToolResult:
    blocked = _reserve_skill_tool_call(runtime, "skill_read", reason, {"ids": ids})
    if blocked is not None:
        return blocked
    results = runtime.services.memory_manager.read(runtime.memory_db, ids)
    runtime.context.tool_calls.append(
        build_tool_call(
            "skill_read",
            {"ids": ids, "reason": reason},
            result_summary=f"returned {len(results)} skill record(s)",
            reason=reason,
        )
    )
    runtime.context.tool_summaries.append(f"skill_read: {len(ids)} ids | reason: {reason}")
    return success_result("skill_read", [item.model_dump(mode="json") for item in results])


async def skill_write(runtime: MissionRuntime, entries: list[dict[str, Any]], reason: str) -> ToolResult:
    blocked = _reserve_skill_tool_call(runtime, "skill_write", reason, {"entry_count": len(entries)})
    if blocked is not None:
        return blocked
    skill_entries = [_force_skill_entry(entry) for entry in entries]
    mutations = await runtime.services.memory_manager.write_entries(runtime.memory_db, skill_entries, reason=reason)
    runtime.context.memory_mutations.extend(mutations)
    runtime.context.tool_calls.append(
        build_tool_call(
            "skill_write",
            {"entry_count": len(entries), "reason": reason},
            result_summary=f"wrote {len(mutations)} skill record(s)",
            reason=reason,
        )
    )
    runtime.context.tool_summaries.append(f"skill_write: {len(mutations)} records | reason: {reason}")
    return success_result("skill_write", [item.model_dump(mode="json") for item in mutations])


async def skill_update(runtime: MissionRuntime, entries: list[dict[str, Any]], reason: str) -> ToolResult:
    blocked = _reserve_skill_tool_call(runtime, "skill_update", reason, {"entry_count": len(entries)})
    if blocked is not None:
        return blocked
    skill_entries = [_force_skill_entry(entry) for entry in entries]
    mutations = await runtime.services.memory_manager.update_entries(runtime.memory_db, skill_entries, reason=reason)
    runtime.context.memory_mutations.extend(mutations)
    runtime.context.tool_calls.append(
        build_tool_call(
            "skill_update",
            {"entry_count": len(entries), "reason": reason},
            result_summary=f"updated {len(mutations)} skill record(s)",
            reason=reason,
        )
    )
    runtime.context.tool_summaries.append(f"skill_update: {len(mutations)} records | reason: {reason}")
    return success_result("skill_update", [item.model_dump(mode="json") for item in mutations])


async def skill_deprecate(
    runtime: MissionRuntime,
    ids: list[str],
    reason: str,
    replacement_id: str | None = None,
) -> ToolResult:
    blocked = _reserve_skill_tool_call(runtime, "skill_deprecate", reason, {"ids": ids})
    if blocked is not None:
        return blocked
    mutations = runtime.services.memory_manager.deprecate_entries(
        runtime.memory_db,
        ids,
        reason=reason,
        replacement_id=replacement_id,
    )
    runtime.context.memory_mutations.extend(mutations)
    runtime.context.tool_calls.append(
        build_tool_call(
            "skill_deprecate",
            {"ids": ids, "replacement_id": replacement_id, "reason": reason},
            result_summary=f"deprecated {len(mutations)} skill record(s)",
            reason=reason,
        )
    )
    runtime.context.tool_summaries.append(f"skill_deprecate: {len(mutations)} records | reason: {reason}")
    return success_result("skill_deprecate", [item.model_dump(mode="json") for item in mutations])


def _force_skill_entry(entry: dict[str, Any]) -> dict[str, Any]:
    forced = dict(entry)
    forced["kind"] = "skill"
    return forced


def _reserve_skill_tool_call(
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
                result_summary="skill tool budget exhausted",
                reason=reason,
                ok=False,
                error_type="skill_rate_limit_exceeded",
                error_message=f"skill tool budget exhausted at {budget} calls",
            )
        )
        runtime.context.tool_summaries.append(f"{tool_name} blocked: skill budget exhausted | reason: {reason}")
        return error_result(
            tool_name,
            "skill_rate_limit_exceeded",
            f"skill tool budget exhausted at {budget} calls",
            "Use current skill findings and web research to finish the task.",
        )
    runtime.context.memory_tool_calls_used += 1
    return None
