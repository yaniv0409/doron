from __future__ import annotations

from typing import Any

from agent_platform.application.runtime_builder import MissionRuntime
from agent_platform.domain.exceptions import DatabaseError
from agent_platform.domain.models import DbMutationRecord, ToolResult
from agent_platform.tools.compression_tools import maybe_auto_compress
from agent_platform.tools.result_utils import build_tool_call, error_result, success_result


async def read_graph(
    runtime: MissionRuntime,
    query: str,
    reason: str,
    parameters: dict[str, Any] | None = None,
) -> ToolResult:
    try:
        rows = runtime.db.execute(query, parameters)
    except DatabaseError as exc:
        result = error_result(
            "read_graph",
            classify_database_error(str(exc)),
            str(exc),
            retry_hint_for_database_error(str(exc)),
        )
        runtime.context.tool_calls.append(
            build_tool_call(
                "read_graph",
                {"query": query, "parameters": parameters or {}, "reason": reason},
                result_summary=result.error_message or "database read failed",
                reason=reason,
                ok=False,
                error_type=result.error_type,
                error_message=result.error_message,
            )
        )
        runtime.context.db_findings.append(f"Read query failed: {result.error_message}")
        runtime.context.tool_summaries.append(f"read_graph failed: {query[:160]} | reason: {reason}")
        return result
    runtime.context.tool_calls.append(
        build_tool_call(
            "read_graph",
            {"query": query, "parameters": parameters or {}, "reason": reason},
            result_summary=f"returned {len(rows)} row(s)",
            reason=reason,
        )
    )
    runtime.context.db_findings.append(f"Read query returned {len(rows)} row(s)")
    runtime.context.tool_summaries.append(f"read_graph: {query[:160]} | reason: {reason}")
    await maybe_auto_compress(runtime, "database read expanded working memory")
    return success_result("read_graph", rows)


async def write_graph(
    runtime: MissionRuntime,
    query: str,
    reason: str,
    parameters: dict[str, Any] | None = None,
) -> ToolResult:
    if runtime.context.db_checkpoint_path is None:
        checkpoint = runtime.services.trace_store.create_checkpoint(
            runtime.context.trace_id,
            runtime.context.mission_request.db_path,
        )
        runtime.context.db_checkpoint_path = str(checkpoint)
    try:
        rows = runtime.db.execute(query, parameters)
    except DatabaseError as exc:
        result = error_result(
            "write_graph",
            classify_database_error(str(exc)),
            str(exc),
            retry_hint_for_database_error(str(exc)),
        )
        runtime.context.tool_calls.append(
            build_tool_call(
                "write_graph",
                {"query": query, "parameters": parameters or {}, "reason": reason},
                result_summary=result.error_message or "database write failed",
                reason=reason,
                ok=False,
                error_type=result.error_type,
                error_message=result.error_message,
            )
        )
        runtime.context.tool_summaries.append(f"write_graph failed: {query[:160]} | reason: {reason}")
        return result
    summary = f"mutation executed, returned {len(rows)} row(s)"
    runtime.context.db_mutations.append(
        DbMutationRecord(
            query=query,
            parameters=parameters or {},
            summary=summary,
        )
    )
    runtime.context.tool_calls.append(
        build_tool_call(
            "write_graph",
            {"query": query, "parameters": parameters or {}, "reason": reason},
            result_summary=summary,
            reason=reason,
        )
    )
    runtime.context.tool_summaries.append(f"write_graph: {query[:160]} | reason: {reason}")
    await maybe_auto_compress(runtime, "database write expanded working memory")
    return success_result("write_graph", rows)


async def inspect_schema(runtime: MissionRuntime, reason: str) -> ToolResult:
    try:
        schema = runtime.db.get_schema()
    except DatabaseError as exc:
        result = error_result(
            "inspect_schema",
            classify_database_error(str(exc)),
            str(exc),
            "Consult Kuzu reference and retry schema inspection with a simpler query.",
        )
        runtime.context.tool_calls.append(
            build_tool_call(
                "inspect_schema",
                {"reason": reason},
                result_summary=result.error_message or "schema inspection failed",
                reason=reason,
                ok=False,
                error_type=result.error_type,
                error_message=result.error_message,
            )
        )
        runtime.context.db_findings.append(f"Schema inspection failed: {result.error_message}")
        runtime.context.tool_summaries.append(f"inspect_schema failed | reason: {reason}")
        return result
    runtime.context.tool_calls.append(
        build_tool_call(
            "inspect_schema",
            {"reason": reason},
            result_summary="schema returned",
            reason=reason,
        )
    )
    runtime.context.db_findings.append(schema[:500])
    runtime.context.tool_summaries.append(f"inspect_schema | reason: {reason}")
    await maybe_auto_compress(runtime, "schema inspection expanded working memory")
    return success_result("inspect_schema", schema)


def classify_database_error(message: str) -> str:
    lowered = message.lower()
    missing_patterns = [
        "table",
        "does not exist",
        "not found",
        "binder exception",
        "no such",
    ]
    if "binder" in lowered or all(token in lowered for token in ["table", "does not exist"]):
        return "database_missing_object"
    if any(token in lowered for token in missing_patterns):
        return "database_missing_object"
    return "database_query_error"


def retry_hint_for_database_error(message: str) -> str:
    error_type = classify_database_error(message)
    if error_type == "database_missing_object":
        return "Inspect the schema first, then retry with existing table or relationship names."
    return "Review the query, inspect the schema, and consult Kuzu reference before retrying."
