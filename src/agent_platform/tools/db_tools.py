from __future__ import annotations

from typing import Any

from agent_platform.application.runtime_builder import MissionRuntime
from agent_platform.domain.models import DbMutationRecord, ToolCallRecord


async def read_graph(
    runtime: MissionRuntime,
    query: str,
    parameters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows = runtime.db.execute(query, parameters)
    runtime.context.tool_calls.append(
        ToolCallRecord(
            name="read_graph",
            arguments={"query": query, "parameters": parameters or {}},
            result_summary=f"returned {len(rows)} row(s)",
        )
    )
    runtime.context.db_findings.append(f"Read query returned {len(rows)} row(s)")
    runtime.context.tool_summaries.append(f"read_graph: {query[:160]}")
    return rows


async def write_graph(
    runtime: MissionRuntime,
    query: str,
    parameters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if runtime.context.db_checkpoint_path is None:
        checkpoint = runtime.services.trace_store.create_checkpoint(
            runtime.context.trace_id,
            runtime.context.mission_request.db_path,
        )
        runtime.context.db_checkpoint_path = str(checkpoint)
    rows = runtime.db.execute(query, parameters)
    summary = f"mutation executed, returned {len(rows)} row(s)"
    runtime.context.db_mutations.append(
        DbMutationRecord(
            query=query,
            parameters=parameters or {},
            summary=summary,
        )
    )
    runtime.context.tool_calls.append(
        ToolCallRecord(
            name="write_graph",
            arguments={"query": query, "parameters": parameters or {}},
            result_summary=summary,
        )
    )
    runtime.context.tool_summaries.append(f"write_graph: {query[:160]}")
    return rows


async def inspect_schema(runtime: MissionRuntime) -> str:
    schema = runtime.db.get_schema()
    runtime.context.tool_calls.append(
        ToolCallRecord(
            name="inspect_schema",
            arguments={},
            result_summary="schema returned",
        )
    )
    runtime.context.db_findings.append(schema[:500])
    runtime.context.tool_summaries.append("inspect_schema")
    return schema
