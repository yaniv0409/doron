from __future__ import annotations

import asyncio
from typing import Any

from agent_platform.agent.factory import AgentFactory
from agent_platform.agent.prompts import build_handoff_prompt
from agent_platform.application.result_validation import ResultValidator
from agent_platform.application.runtime_builder import MissionRuntime, RuntimeBuilder
from agent_platform.config.settings import AppSettings
from agent_platform.domain.enums import LogCategory, MissionStatus, ResultFormat
from agent_platform.domain.exceptions import AgentPlatformError, ContextRefreshRequested, ModelSwitchRequested, OutputValidationError
from agent_platform.domain.models import ExecutionTrace, MissionError, MissionRequest, MissionResult, utc_now
from agent_platform.infrastructure.logging import get_logger


class MissionService:
    def __init__(self, settings: AppSettings) -> None:
        self._runtime_builder = RuntimeBuilder(settings)
        self._agent_factory = AgentFactory()
        self._validator = ResultValidator()
        self._logger = get_logger(LogCategory.MISSION.value)
        self._reasoning_logger = get_logger(LogCategory.REASONING.value)
        self._db_logger = get_logger(LogCategory.DB_AUDIT.value)
        self._docs_logger = get_logger(LogCategory.DOCS_AUDIT.value)
        self._web_logger = get_logger(LogCategory.WEB_AUDIT.value)

    def run_sync(self, request: MissionRequest) -> MissionResult:
        return asyncio.run(self.run(request))

    async def run(self, request: MissionRequest) -> MissionResult:
        runtime = self._runtime_builder.build(request)
        self._runtime_builder.services.trace_store.write_request_snapshot(
            runtime.context.trace_id,
            request.model_dump(mode="json"),
        )
        model_sequence = [runtime.context.current_model.name]
        prompt = request.prompt
        try:
            while True:
                try:
                    raw_output = await self._run_once(runtime, prompt)
                except ContextRefreshRequested as exc:
                    runtime.context.pending_context_refresh_reason = None
                    prompt = build_handoff_prompt(
                        runtime.context.build_transfer_packet(),
                        request,
                    )
                    runtime.context.reasoning_notes.append(
                        f"Context was refreshed and compressed: {exc.reason}",
                    )
                    continue
                if runtime.context.pending_model_switch:
                    next_model = self._promote_model(runtime, runtime.context.pending_model_switch)
                    if next_model is None:
                        return await self._fail(
                            runtime,
                            model_sequence,
                            "invalid_model_switch",
                            "requested model switch is not allowed",
                        )
                    runtime.context.current_model = next_model
                    runtime.context.pending_model_switch = None
                    model_sequence.append(next_model.name)
                    prompt = build_handoff_prompt(
                        runtime.context.build_transfer_packet(),
                        request,
                    )
                    continue
                try:
                    result, result_format = self._validator.validate(
                        raw_output,
                        request.output_schema,
                    )
                except OutputValidationError as exc:
                    stronger = self._runtime_builder.services.model_catalog.next_stronger(
                        runtime.context.current_model.name,
                        runtime.context.allowed_models,
                    )
                    if stronger is None:
                        return await self._fail(
                            runtime,
                            model_sequence,
                            "output_validation_error",
                            str(exc),
                        )
                    runtime.context.current_model = stronger
                    runtime.context.reasoning_notes.append(
                        "Escalated model after output validation failure",
                    )
                    model_sequence.append(stronger.name)
                    prompt = build_handoff_prompt(
                        runtime.context.build_transfer_packet(),
                        request,
                    )
                    continue
                completed_at = utc_now()
                mission_result = MissionResult(
                    status=MissionStatus.COMPLETED,
                    result=result,
                    result_format=result_format,
                    final_model=runtime.context.current_model.name,
                    trace_id=runtime.context.trace_id,
                    started_at=runtime.context.started_at,
                    completed_at=completed_at,
                )
                self._persist_trace(runtime, model_sequence, mission_result, None)
                await runtime.browser.close()
                return mission_result
        except AgentPlatformError as exc:
            return await self._fail(runtime, model_sequence, type(exc).__name__, str(exc))
        except Exception as exc:  # pragma: no cover
            return await self._fail(runtime, model_sequence, "unexpected_error", str(exc))

    async def _run_once(self, runtime: MissionRuntime, prompt: str) -> str:
        session = self._agent_factory.create(runtime)
        extra = {"trace_id": runtime.context.trace_id}
        self._logger.info(
            "starting mission iteration with model=%s",
            runtime.context.current_model.name,
            extra=extra,
        )
        try:
            result = await session.agent.run(prompt, deps=runtime)
        except ModelSwitchRequested:
            raise
        self._log_runtime_state(runtime)
        output = getattr(result, "output", result)
        if not isinstance(output, str):
            return str(output)
        return output

    def _promote_model(self, runtime: MissionRuntime, requested_model: str) -> Any | None:
        for model in runtime.context.allowed_models:
            if model.name == requested_model:
                return model
        return self._runtime_builder.services.model_catalog.next_stronger(
            runtime.context.current_model.name,
            runtime.context.allowed_models,
        )

    def _persist_trace(
        self,
        runtime: MissionRuntime,
        model_sequence: list[str],
        result: MissionResult | None,
        error: MissionError | None,
    ) -> None:
        trace = ExecutionTrace(
            trace_id=runtime.context.trace_id,
            request=runtime.context.mission_request,
            model_sequence=model_sequence,
            tool_calls=runtime.context.tool_calls,
            db_mutations=runtime.context.db_mutations,
            docs_lookups=runtime.context.docs_lookups,
            web_artifacts=runtime.context.web_artifacts,
            compression_events=runtime.context.compression_events,
            result=result.result if result else None,
            error=error,
            started_at=runtime.context.started_at,
            completed_at=result.completed_at if result else utc_now(),
        )
        self._runtime_builder.services.trace_store.write_trace(trace)

    async def _fail(
        self,
        runtime: MissionRuntime,
        model_sequence: list[str],
        code: str,
        message: str,
    ) -> MissionResult:
        completed_at = utc_now()
        error = MissionError(code=code, message=message)
        result = MissionResult(
            status=MissionStatus.FAILED,
            result=None,
            result_format=(
                ResultFormat.JSON_SCHEMA
                if runtime.context.mission_request.output_schema
                else ResultFormat.TEXT
            ),
            final_model=runtime.context.current_model.name,
            trace_id=runtime.context.trace_id,
            started_at=runtime.context.started_at,
            completed_at=completed_at,
            error=error,
        )
        self._persist_trace(runtime, model_sequence, result, error)
        await runtime.browser.close()
        self._logger.error(message, extra={"trace_id": runtime.context.trace_id})
        return result

    def _log_runtime_state(self, runtime: MissionRuntime) -> None:
        extra = {"trace_id": runtime.context.trace_id}
        for item in runtime.context.db_mutations:
            self._db_logger.info(item.model_dump_json(), extra=extra)
        for item in runtime.context.docs_lookups:
            self._docs_logger.info(item.model_dump_json(), extra=extra)
        for item in runtime.context.compression_events:
            self._reasoning_logger.info(item.model_dump_json(), extra=extra)
        for item in runtime.context.web_findings[-5:]:
            self._web_logger.info(item, extra=extra)
        for item in runtime.context.reasoning_notes[-10:]:
            self._reasoning_logger.info(item, extra=extra)
