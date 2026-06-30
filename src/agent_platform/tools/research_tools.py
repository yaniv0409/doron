from __future__ import annotations

from typing import Any

from agent_platform.application.runtime_builder import MissionRuntime
from agent_platform.domain.models import ToolResult
from agent_platform.tools.result_utils import build_tool_call, error_result, success_result


async def create_root_work_item(runtime: MissionRuntime, prompt: str, reason: str) -> ToolResult:
    return _run_tool(
        runtime,
        "create_root_work_item",
        {"prompt": prompt, "reason": reason},
        lambda: runtime.services.research_graph_manager.create_root_work_item(
            runtime.research_meta_db,
            prompt,
            source_trace_id=runtime.context.trace_id,
        ),
        "created or returned root work item",
    )


async def advance_new(
    runtime: MissionRuntime,
    from_node_id: str,
    edge: dict[str, Any],
    new_node: dict[str, Any],
    reason: str,
) -> ToolResult:
    return _run_tool(
        runtime,
        "advance_new",
        {"from_node_id": from_node_id, "edge": edge, "new_node": new_node, "reason": reason},
        lambda: runtime.services.research_graph_manager.advance_new(
            runtime.research_meta_db,
            from_node_id,
            edge,
            new_node,
            prompt=runtime.context.mission_request.prompt,
            source_trace_id=runtime.context.trace_id,
        ),
        "advanced to new node",
    )


async def advance_existing(
    runtime: MissionRuntime,
    from_node_id: str,
    to_node_id: str,
    edge: dict[str, Any],
    reason: str,
) -> ToolResult:
    return _run_tool(
        runtime,
        "advance_existing",
        {"from_node_id": from_node_id, "to_node_id": to_node_id, "edge": edge, "reason": reason},
        lambda: runtime.services.research_graph_manager.advance_existing(
            runtime.research_meta_db,
            from_node_id,
            to_node_id,
            edge,
            prompt=runtime.context.mission_request.prompt,
            source_trace_id=runtime.context.trace_id,
        ),
        "advanced to existing node",
    )


async def get_frontier(runtime: MissionRuntime, reason: str) -> ToolResult:
    return _run_tool(
        runtime,
        "get_frontier",
        {"reason": reason},
        lambda: [
            item.model_dump(mode="json")
            for item in runtime.services.research_graph_manager.get_frontier(
                runtime.research_meta_db,
                prompt=runtime.context.mission_request.prompt,
                source_trace_id=runtime.context.trace_id,
            )
        ],
        "returned frontier nodes",
    )


async def get_ancestry(runtime: MissionRuntime, node_id: str, depth: int, reason: str) -> ToolResult:
    return _run_tool(
        runtime,
        "get_ancestry",
        {"node_id": node_id, "depth": depth, "reason": reason},
        lambda: runtime.services.research_graph_manager.get_ancestry(
            runtime.research_meta_db,
            node_id,
            depth,
            prompt=runtime.context.mission_request.prompt,
            source_trace_id=runtime.context.trace_id,
        ),
        "returned ancestry",
    )


async def get_descendants(runtime: MissionRuntime, node_id: str, depth: int, mode: str, reason: str) -> ToolResult:
    return _run_tool(
        runtime,
        "get_descendants",
        {"node_id": node_id, "depth": depth, "mode": mode, "reason": reason},
        lambda: runtime.services.research_graph_manager.get_descendants(
            runtime.research_meta_db,
            node_id,
            depth,
            mode,
            prompt=runtime.context.mission_request.prompt,
            source_trace_id=runtime.context.trace_id,
        ),
        "returned descendants",
    )


async def search_research_nodes(
    runtime: MissionRuntime,
    query: str,
    limit: int,
    include_failures: bool,
    reason: str,
) -> ToolResult:
    return _run_tool(
        runtime,
        "search_research_nodes",
        {"query": query, "limit": limit, "include_failures": include_failures, "reason": reason},
        lambda: [
            item.model_dump(mode="json")
            for item in runtime.services.research_graph_manager.search_nodes(
                runtime.research_meta_db,
                query,
                limit=limit,
                include_failures=include_failures,
                prompt=runtime.context.mission_request.prompt,
                source_trace_id=runtime.context.trace_id,
            )
        ],
        "returned research node candidates",
    )


def _run_tool(
    runtime: MissionRuntime,
    tool_name: str,
    arguments: dict[str, Any],
    operation,
    success_summary: str,
) -> ToolResult:
    try:
        data = operation()
    except Exception as exc:
        runtime.context.tool_calls.append(
            build_tool_call(
                tool_name,
                arguments,
                result_summary=str(exc),
                reason=arguments.get("reason"),
                ok=False,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        )
        runtime.context.tool_summaries.append(f"{tool_name} failed: {exc}")
        return error_result(
            tool_name,
            type(exc).__name__,
            str(exc),
            "Inspect the research graph context, then retry with valid node ids and payloads.",
        )
    runtime.context.tool_calls.append(
        build_tool_call(
            tool_name,
            arguments,
            result_summary=success_summary,
            reason=arguments.get("reason"),
        )
    )
    runtime.context.tool_summaries.append(f"{tool_name}: {success_summary}")
    return success_result(tool_name, data)
