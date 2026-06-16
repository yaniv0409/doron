from __future__ import annotations

from agent_platform.application.runtime_builder import MissionRuntime
from agent_platform.domain.exceptions import ModelSwitchRequested


async def request_model_switch(runtime: MissionRuntime, target_model: str, reason: str) -> str:
    runtime.context.pending_model_switch = target_model
    runtime.context.reasoning_notes.append(f"switch requested to {target_model}: {reason}")
    raise ModelSwitchRequested(target_model)
