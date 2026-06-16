from __future__ import annotations

from agent_platform.application.runtime_builder import MissionRuntime
from agent_platform.domain.exceptions import ModelSwitchRequested
from agent_platform.domain.models import ToolCallRecord


async def request_model_switch(runtime: MissionRuntime, target_model: str, reason: str) -> str:
    runtime.context.pending_model_switch = target_model
    runtime.context.reasoning_notes.append(f"switch requested to {target_model}: {reason}")
    runtime.context.tool_calls.append(
        ToolCallRecord(
            name="switch_model",
            arguments={"target_model": target_model, "reason": reason},
            result_summary=f"requested switch to {target_model}",
        )
    )
    runtime.context.tool_summaries.append(f"switch_model: {target_model}")
    raise ModelSwitchRequested(target_model)
