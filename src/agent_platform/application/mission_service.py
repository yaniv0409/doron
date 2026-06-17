from __future__ import annotations

import asyncio
from typing import Any

from agent_platform.agent.factory import AgentFactory
from agent_platform.agent.prompts import build_handoff_prompt, build_output_repair_prompt
from agent_platform.application.result_validation import ResultValidator
from agent_platform.application.live_events import emit_runtime_event, emit_stream_event
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

    async def run(self, request: MissionRequest, *, event_hook: Any | None = None) -> MissionResult:
        runtime = self._runtime_builder.build(request)
        runtime.context.progress_hook = lambda **kwargs: self._write_progress(runtime, **kwargs)
        runtime.context.event_hook = event_hook
        self._runtime_builder.services.trace_store.write_request_snapshot(
            runtime.context.trace_id,
            request.model_dump(mode="json"),
        )
        emit_stream_event(
            runtime.context,
            "mission.started",
            {
                "trace_id": runtime.context.trace_id,
                "request": request.model_dump(mode="json"),
                "model": runtime.context.current_model.name,
            },
        )
        self._append_runtime_event(runtime, "request_loaded", "mission request snapshot written")
        model_sequence = [runtime.context.current_model.name]
        prompt = request.prompt
        try:
            while True:
                try:
                    raw_output = await self._run_once(runtime, prompt)
                except ContextRefreshRequested as exc:
                    runtime.context.pending_context_refresh_reason = None
                    self._append_runtime_event(
                        runtime,
                        "context_refresh",
                        "context refresh requested",
                        {"reason": exc.reason},
                    )
                    prompt = build_handoff_prompt(runtime.context)
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
                    self._append_runtime_event(
                        runtime,
                        "model_switch",
                        "model switched",
                        {"model": next_model.name},
                    )
                    prompt = build_handoff_prompt(runtime.context)
                    continue
                try:
                    result, result_format = self._validator.validate(
                        raw_output,
                        request.output_schema,
                    )
                except OutputValidationError as exc:
                    repair_prompt = build_output_repair_prompt(runtime.context, raw_output, str(exc))
                    raw_output = await self._run_once(runtime, repair_prompt)
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
                        self._append_runtime_event(
                            runtime,
                            "model_switch",
                            "model switched",
                            {"model": next_model.name},
                        )
                        prompt = build_handoff_prompt(runtime.context)
                        continue
                    try:
                        result, result_format = self._validator.validate(
                            raw_output,
                            request.output_schema,
                        )
                    except OutputValidationError as repair_exc:
                        return await self._fail(
                            runtime,
                            model_sequence,
                            "output_validation_error",
                            str(repair_exc),
                        )
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
        except asyncio.TimeoutError:
            result = await self._fail(
                runtime,
                model_sequence,
                "agent_run_timeout",
                f"agent run exceeded {self._runtime_builder.services.settings.runtime.agent_run_timeout_seconds}s timeout",
            )
            return result
        except AgentPlatformError as exc:
            result = await self._fail(runtime, model_sequence, type(exc).__name__, str(exc))
            return result
        except Exception as exc:  # pragma: no cover
            result = await self._fail(runtime, model_sequence, "unexpected_error", str(exc))
            return result

    async def _run_once(self, runtime: MissionRuntime, prompt: str) -> Any:
        session = self._agent_factory.create(runtime)
        extra = {"trace_id": runtime.context.trace_id}
        prompt_size = len(prompt)
        working_memory_size = runtime.context.estimate_working_memory_size()
        self._append_runtime_event(
            runtime,
            "agent_setup",
            "agent session created",
            {
                "model": runtime.context.current_model.name,
                "tool_count": len(session.tool_names),
                "tool_names": session.tool_names,
                "prompt_size": prompt_size,
                "working_memory_size": working_memory_size,
                "compressed_memory_active": runtime.context.compressed_memory is not None,
            },
        )
        self._logger.info(
            "starting mission iteration with model=%s",
            runtime.context.current_model.name,
            extra=extra,
        )
        self._append_runtime_event(runtime, "agent_run_started", "agent.run started")
        try:
            result = await asyncio.wait_for(
                session.agent.run(prompt, deps=runtime),
                timeout=runtime.services.settings.runtime.agent_run_timeout_seconds,
            )
        except ModelSwitchRequested:
            raise
        finally:
            self._append_runtime_event(runtime, "agent_run_wait_complete", "agent.run returned or raised")
        self._append_runtime_event(
            runtime,
            "agent_run_complete",
            "agent run completed",
            {"model": runtime.context.current_model.name},
        )
        self._log_runtime_state(runtime)
        output = getattr(result, "output", result)
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
            runtime_events=runtime.context.runtime_events,
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
        self._append_runtime_event(runtime, "failed", message, {"code": code})
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

    def _append_runtime_event(
        self,
        runtime: MissionRuntime,
        phase: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        emit_runtime_event(runtime.context, phase, message, metadata or {})

    def _write_progress(
        self,
        runtime: MissionRuntime,
        *,
        phase: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._runtime_builder.services.trace_store.write_progress(
            runtime.context.trace_id,
            {
                "trace_id": runtime.context.trace_id,
                "phase": phase,
                "message": message,
                "model": runtime.context.current_model.name,
                "metadata": metadata or {},
                "runtime_events": [item.model_dump(mode="json") for item in runtime.context.runtime_events[-10:]],
            },
        )
