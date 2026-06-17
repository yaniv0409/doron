from __future__ import annotations

from typing import Any

from agent_platform.domain.models import RuntimeEvent, utc_now


def emit_runtime_event(
    context: Any,
    phase: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    *,
    stream_event: str = "mission.progress",
) -> RuntimeEvent:
    event = RuntimeEvent(
        phase=phase,
        message=message,
        metadata=metadata or {},
    )
    if hasattr(context, "runtime_events"):
        context.runtime_events.append(event)
    progress_hook = getattr(context, "progress_hook", None)
    if progress_hook is not None:
        progress_hook(
            phase=phase,
            message=message,
            metadata=metadata or {},
        )
    event_hook = getattr(context, "event_hook", None)
    if event_hook is not None:
        trace_id = getattr(context, "trace_id", None)
        payload = {
            "phase": phase,
            "message": message,
            "metadata": metadata or {},
            "created_at": utc_now().isoformat(),
        }
        if trace_id is not None:
            payload["trace_id"] = trace_id
        event_hook(
            {
                "event": stream_event,
                "data": payload,
            }
        )
    return event


def emit_stream_event(context: Any, event: str, data: dict[str, Any]) -> None:
    event_hook = getattr(context, "event_hook", None)
    if event_hook is not None:
        payload = dict(data)
        payload.setdefault("created_at", utc_now().isoformat())
        event_hook({"event": event, "data": payload})
