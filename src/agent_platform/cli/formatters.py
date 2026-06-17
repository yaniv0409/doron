from __future__ import annotations

import json
from typing import Any

from agent_platform.contracts.api import MissionRunResponse
from agent_platform.domain.models import ExecutionTrace
from agent_platform.contracts.api import MissionStreamEvent


def format_response(response: MissionRunResponse, trace: ExecutionTrace) -> str:
    lines = [
        f"Status: {response.status.value}",
        "Result:",
        indent_block(_format_result(response.result)),
        f"Model: {response.final_model}",
        f"Trace: {response.trace_id}",
        f"Tools: {format_tool_summary(trace)}",
    ]
    if response.error:
        lines.extend(
            [
                "Error:",
                indent_block(f"{response.error.code}: {response.error.message}"),
            ]
        )
    return "\n".join(lines)


def format_stream_event(event: MissionStreamEvent) -> str:
    if event.event == "mission.started":
        trace_id = event.data.get("trace_id", "-")
        return f"Mission started: {trace_id}"
    if event.event == "mission.progress":
        phase = event.data.get("phase", "progress")
        message = event.data.get("message", "")
        if message:
            return f"Progress: {phase} - {message}"
        return f"Progress: {phase}"
    if event.event == "tool.started":
        name = event.data.get("name", "tool")
        return f"Tool started: {name}"
    if event.event == "tool.completed":
        name = event.data.get("name", "tool")
        ok = event.data.get("ok", True)
        summary = event.data.get("result_summary") or "completed"
        status = "ok" if ok else "failed"
        return f"Tool {status}: {name} - {summary}"
    if event.event in {"mission.completed", "mission.failed"}:
        return f"Mission {event.event.split('.')[-1]}"
    return f"{event.event}: {json.dumps(event.data, ensure_ascii=False)}"


def format_final_stream_response(response: MissionRunResponse, tool_names: list[str]) -> str:
    lines = [
        f"Status: {response.status.value}",
        "Result:",
        indent_block(_format_result(response.result)),
        f"Model: {response.final_model}",
        f"Trace: {response.trace_id}",
        f"Tools: {format_tool_names(tool_names)}",
    ]
    if response.error:
        lines.extend(
            [
                "Error:",
                indent_block(f"{response.error.code}: {response.error.message}"),
            ]
        )
    return "\n".join(lines)


def format_tool_summary(trace: ExecutionTrace) -> str:
    if not trace.tool_calls:
        return "none"
    return " -> ".join(call.name for call in trace.tool_calls)


def format_tool_names(tool_names: list[str]) -> str:
    if not tool_names:
        return "none"
    return " -> ".join(tool_names)


def format_config_summary(defaults: Any) -> str:
    allowed = ",".join(defaults.allowed_models) if defaults.allowed_models else "all configured"
    schema = "yes" if defaults.output_schema else "no"
    return "\n".join(
        [
            f"db_path={defaults.db_path}",
            f"preferred_model={defaults.preferred_model or 'default'}",
            f"allowed_models={allowed}",
            f"web_enabled={defaults.web_enabled}",
            f"db_mutation_enabled={defaults.db_mutation_enabled}",
            f"output_schema={schema}",
        ]
    )


def indent_block(text: str) -> str:
    return "\n".join(f"  {line}" for line in text.splitlines() or [""])


def _format_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, indent=2)
