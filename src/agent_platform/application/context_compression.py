from __future__ import annotations

import json
import asyncio
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from agent_platform.config.settings import CompressionSettings
from agent_platform.domain.exceptions import ModelError
from agent_platform.domain.models import CompressedMemory, CompressionEvent, RuntimeEvent, ToolResult
from agent_platform.tools.result_utils import error_result, success_result

if TYPE_CHECKING:
    from agent_platform.application.runtime_builder import MissionRuntime


class ContextCompressor:
    def __init__(self, settings: CompressionSettings) -> None:
        self._settings = settings

    async def compress(
        self,
        runtime: MissionRuntime,
        *,
        trigger: str,
        reason: str,
    ) -> ToolResult:
        model = runtime.services.model_catalog.strongest_allowed(runtime.context.allowed_models)
        size_before = runtime.context.estimate_working_memory_size()
        try:
            payload = await asyncio.wait_for(
                runtime.services.chat_client.complete_json(
                    model=model.name,
                    system_prompt=self._build_system_prompt(),
                    user_prompt=self._build_user_prompt(runtime, reason),
                ),
                timeout=runtime.services.settings.compression.timeout_seconds,
            )
            compressed = CompressedMemory.model_validate(payload)
        except asyncio.TimeoutError as exc:
            return error_result(
                "compress_context",
                "compression_timeout",
                "context compression timed out",
                "Continue with the current context or retry compression later.",
            )
        except (ModelError, ValidationError, json.JSONDecodeError) as exc:
            return error_result(
                "compress_context",
                "compression_error",
                str(exc),
                "Continue with the current context or retry compression later.",
            )

        self._apply_compressed_memory(runtime, compressed, trigger, reason, model.name, size_before)
        preview = self._preview(compressed)
        return success_result(
            "compress_context",
            {
                "trigger": trigger,
                "summarizer_model": model.name,
                "size_before": size_before,
                "size_after": runtime.context.estimate_working_memory_size(),
                "preview": preview,
            },
            "context compressed",
        )

    def should_auto_compress(self, runtime: MissionRuntime) -> bool:
        if not self._settings.enabled or runtime.context.compression_in_progress:
            return False
        budget = self._budget(runtime)
        current = runtime.context.estimate_working_memory_size()
        if current <= budget:
            return False
        return current - runtime.context.last_compression_size >= self._settings.min_growth_chars

    def _apply_compressed_memory(
        self,
        runtime: MissionRuntime,
        compressed: CompressedMemory,
        trigger: str,
        reason: str,
        model_name: str,
        size_before: int,
    ) -> None:
        runtime.context.compressed_memory = compressed
        runtime.context.reasoning_notes = compressed.notes[: self._settings.max_notes]
        runtime.context.db_findings = compressed.db_findings[: self._settings.max_findings]
        runtime.context.web_findings = compressed.web_findings[: self._settings.max_findings]
        runtime.context.tool_summaries = compressed.tool_summaries[: self._settings.max_tool_summaries]
        notice = compressed.notice or f"Working memory was compressed ({trigger}). Use the distilled memory as authoritative."
        runtime.context.compression_notice = notice
        size_after = runtime.context.estimate_working_memory_size()
        runtime.context.last_compression_size = size_after
        runtime.context.compression_events.append(
            CompressionEvent(
                trigger=trigger,
                summarizer_model=model_name,
                reason=reason,
                size_before=size_before,
                size_after=size_after,
                preview=self._preview(compressed),
            )
        )
        runtime.context.runtime_events.append(
            RuntimeEvent(
                phase="compression",
                message=f"context compression completed ({trigger})",
                metadata={
                    "reason": reason,
                    "model": model_name,
                    "size_before": size_before,
                    "size_after": size_after,
                },
            )
        )

    def _budget(self, runtime: MissionRuntime) -> int:
        window = runtime.context.current_model.context_window
        if window is None:
            return self._settings.fallback_budget_chars
        return max(int(window * self._settings.threshold_ratio), self._settings.fallback_budget_chars)

    def _build_system_prompt(self) -> str:
        return "\n".join(
            [
                "Summarize and clean the agent's working memory.",
                "The original mission prompt is canonical and must not be rewritten or replaced.",
                "Return valid JSON matching this shape:",
                json.dumps(
                    {
                        "notes": ["short note"],
                        "db_findings": ["important database fact"],
                        "web_findings": ["important web fact"],
                        "tool_summaries": ["tool outcome summary"],
                        "unresolved_goals": ["open problem or next action"],
                        "notice": "short notice for the next agent turn",
                    }
                ),
                "Keep only high-signal facts, failures, unresolved goals, and next useful actions.",
                "Remove repetition and low-value scratch reasoning.",
            ]
        )

    def _build_user_prompt(self, runtime: MissionRuntime, reason: str) -> str:
        payload: dict[str, Any] = {
            "mission_prompt": runtime.context.mission_request.prompt,
            "mission_constraints": {
                "output_schema_present": runtime.context.mission_request.output_schema is not None,
                "web_enabled": runtime.context.mission_request.web_enabled,
                "db_mutation_enabled": runtime.context.mission_request.db_mutation_enabled,
                "allowed_models": [item.name for item in runtime.context.allowed_models],
            },
            "compression_reason": reason,
            "working_memory": {
                "reasoning_notes": runtime.context.reasoning_notes,
                "db_findings": runtime.context.db_findings,
                "web_findings": runtime.context.web_findings,
                "tool_summaries": runtime.context.tool_summaries,
            },
            "recent_tool_calls": [
                {
                    "name": item.name,
                    "ok": item.ok,
                    "result_summary": item.result_summary,
                    "error_type": item.error_type,
                    "error_message": item.error_message,
                }
                for item in runtime.context.tool_calls[-20:]
            ],
        }
        return json.dumps(payload)

    def _preview(self, compressed: CompressedMemory) -> str:
        for group in (
            compressed.notes,
            compressed.db_findings,
            compressed.web_findings,
            compressed.tool_summaries,
            compressed.unresolved_goals,
        ):
            if group:
                return group[0][:200]
        return "compressed memory updated"
