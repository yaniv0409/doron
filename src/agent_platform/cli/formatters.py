from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from agent_platform.contracts.api import MissionStreamEvent
from agent_platform.contracts.api import MissionRunResponse


def format_stream_event(event: MissionStreamEvent) -> str:
    prefix = _format_event_prefix(event.data.get("created_at"))
    if event.event == "mission.started":
        trace_id = event.data.get("trace_id", "-")
        return f"{prefix} Mission started: {trace_id}"
    if event.event == "mission.progress":
        phase = event.data.get("phase", "progress")
        message = event.data.get("message", "")
        if message:
            return f"{prefix} Progress: {phase} - {message}"
        return f"{prefix} Progress: {phase}"
    if event.event == "tool.started":
        name = event.data.get("name", "tool")
        return f"{prefix} Tool started: {name}"
    if event.event == "tool.completed":
        name = event.data.get("name", "tool")
        ok = event.data.get("ok", True)
        summary = event.data.get("result_summary") or "completed"
        status = "ok" if ok else "failed"
        return f"{prefix} Tool {status}: {name} - {summary}"
    if event.event in {"mission.completed", "mission.failed"}:
        return f"{prefix} Mission {event.event.split('.')[-1]}"
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


def _format_event_prefix(created_at: Any) -> str:
    if not isinstance(created_at, str) or not created_at:
        return "[--:--:--]"
    try:
        stamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return "[--:--:--]"
    return f"[{stamp.strftime('%H:%M:%S')}]"


def _format_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, indent=2)
