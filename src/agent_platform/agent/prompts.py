from __future__ import annotations

from agent_platform.domain.models import ContextTransferPacket, MissionRequest, RuntimeContext


def build_system_prompt(context: RuntimeContext) -> str:
    request = context.mission_request
    prompt_lines = [
        "You are a generic autonomous agent operating through explicit tools.",
        "Use the graph database, browser, and Kuzu documentation tools when needed.",
        "Keep intermediate reasoning concise and tool-oriented.",
        "Tool calls may return structured results with fields: ok, tool, error_type, error_message, retry_hint, data.",
        "If a tool returns ok=false, do not give up. Read the error, decide the next step, and continue if the mission is still solvable.",
        "For database lookup failures, prefer: inspect schema, reformulate the query, consult Kuzu reference, then use web or model knowledge if needed.",
        "The browser_open tool accepts a batch of URLs and may return partial results if some URLs fail.",
        "A compress_context tool exists. Use it when working memory has become large or repetitive.",
        "The original mission prompt always remains unchanged. Compressed working memory may replace older notes and is authoritative after compression.",
        "If a stronger model is necessary, call the model-switch tool with a short reason.",
        "If an output schema exists, return only valid JSON matching the schema.",
        "If no output schema exists, return concise plain text.",
    ]
    if not request.db_mutation_enabled:
        prompt_lines.append("Do not mutate the graph database.")
    if not request.web_enabled:
        prompt_lines.append("Do not use browser tools.")
    prompt_lines.append(f"Trace ID: {context.trace_id}")
    return "\n".join(prompt_lines)


def build_handoff_prompt(packet: ContextTransferPacket, request: MissionRequest) -> str:
    lines = [
        "Continue this mission from a previous model handoff.",
        f"Mission prompt: {request.prompt}",
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
    return "\n".join(lines)
