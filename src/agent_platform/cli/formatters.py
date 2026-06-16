from __future__ import annotations

import json
from typing import Any

from agent_platform.contracts.api import MissionRunResponse
from agent_platform.domain.models import ExecutionTrace


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


def format_tool_summary(trace: ExecutionTrace) -> str:
    if not trace.tool_calls:
        return "none"
    return " -> ".join(call.name for call in trace.tool_calls)


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
