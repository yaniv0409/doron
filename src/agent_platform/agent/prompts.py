from __future__ import annotations

import json
from typing import Any

from agent_platform.domain.models import MissionRequest, RuntimeContext


def build_system_prompt(context: RuntimeContext) -> str:
    request = context.mission_request
    prompt_lines = [
        "You are a generic autonomous agent operating through explicit tools.",
        "Use the graph database, browser, and Kuzu documentation tools when needed.",
        "Keep intermediate reasoning concise and tool-oriented.",
        f"Every tool call must include a short reason argument.",
        "Tool calls may return structured results with fields: ok, tool, error_type, error_message, retry_hint, data.",
        "If a tool returns ok=false, do not give up. Read the error, decide the next step, and continue if the mission is still solvable.",
        "For database lookup failures, prefer: inspect schema, reformulate the query, consult Kuzu reference, then use web or model knowledge if needed.",
        f"The browser_open tool accepts a batch of URLs and may return partial results if some URLs fail.",
        f"Web tool budget: {context.web_tool_budget()} browser calls per mission; used so far: {context.web_tool_calls_used}; remaining: {context.web_tool_calls_remaining()}.",
        "A compress_context tool exists. Use it when working memory has become large or repetitive.",
        "The original mission prompt always remains unchanged. Compressed working memory may replace older notes and is authoritative after compression.",
        "If a stronger model is necessary, call the model-switch tool with a short reason.",
    ]
    prompt_lines.extend(_build_output_format_lines(request.output_schema))
    if not request.db_mutation_enabled:
        prompt_lines.append("Do not mutate the graph database.")
    if not request.web_enabled:
        prompt_lines.append("Do not use browser tools.")
    prompt_lines.append(f"Trace ID: {context.trace_id}")
    return "\n".join(prompt_lines)


def build_handoff_prompt(context: RuntimeContext) -> str:
    packet = context.build_transfer_packet()
    request = context.mission_request
    lines = [
        "Continue this mission from a previous model handoff.",
        f"Mission prompt: {request.prompt}",
        f"Web tool budget remaining: {context.web_tool_calls_remaining()} of {context.web_tool_budget()} calls.",
        "Carry forward prior findings and finish the task.",
    ]
    if packet.notice:
        lines.append(f"Context notice: {packet.notice}")
    if packet.notes:
        lines.append("Notes:")
        lines.extend(f"- {item}" for item in packet.notes)
    if packet.db_findings:
        lines.append("Database findings:")
        lines.extend(f"- {item}" for item in packet.db_findings)
    if packet.web_findings:
        lines.append("Web findings:")
        lines.extend(f"- {item}" for item in packet.web_findings)
    if packet.tool_summaries:
        lines.append("Tool summary:")
        lines.extend(f"- {item}" for item in packet.tool_summaries)
    recent_calls = context.tool_calls[-10:]
    if recent_calls:
        lines.append("Recent tool reasons:")
        lines.extend(
            f"- {item.name}: {item.reason or item.arguments.get('reason', 'unspecified')}"
            for item in recent_calls
        )
    lines.extend(_build_output_format_lines(request.output_schema))
    return "\n".join(lines)


def build_output_repair_prompt(context: RuntimeContext, raw_output: str, validation_error: str) -> str:
    request = context.mission_request
    lines = [
        "The previous answer was invalid.",
        "Return only valid JSON that matches the output schema below.",
        "Do not include markdown, code fences, or explanations.",
        f"Validation error: {validation_error}",
        f"Mission prompt: {request.prompt}",
    ]
    lines.extend(_build_output_format_lines(request.output_schema))
    lines.append("Previous invalid output:")
    lines.append(_truncate(raw_output, 2000))
    return "\n".join(lines)


def _build_output_format_lines(schema: dict[str, Any] | None) -> list[str]:
    if schema is None:
        return ["If no output schema exists, return concise plain text."]
    return [
        "If an output schema exists, return only valid JSON matching the schema.",
        "Output schema:",
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True),
    ]


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated]"
