from __future__ import annotations

import base64
import asyncio
import json
from contextlib import suppress
from typing import Any

from agent_platform.agent.factory import AgentFactory
from agent_platform.agent.prompts import build_handoff_prompt, build_output_repair_prompt
from agent_platform.application.result_validation import ResultValidator
from agent_platform.application.live_events import emit_runtime_event, emit_stream_event
from agent_platform.application.runtime_builder import MissionRuntime, RuntimeBuilder
from agent_platform.config.settings import AppSettings
from agent_platform.domain.enums import LogCategory, MissionStatus, ResultFormat
from agent_platform.domain.exceptions import AgentPlatformError, ContextRefreshRequested, ModelError, ModelSwitchRequested, OutputValidationError
from agent_platform.domain.models import CompletionMetadata, ExecutionTrace, MemoryRetrievalRecord, MissionError, MissionRequest, MissionResult, utc_now
from agent_platform.infrastructure.logging import get_logger

try:
    from pydantic_ai.messages import DocumentUrl, UserContent
except ImportError:  # pragma: no cover
    DocumentUrl = Any
    UserContent = Any


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
        self._maintenance_runner: Any | None = None

    @property
    def trace_store(self):
        return self._runtime_builder.services.trace_store

    def attach_maintenance_runner(self, runner: Any) -> None:
        self._maintenance_runner = runner

    async def run(self, request: MissionRequest, *, event_hook: Any | None = None) -> MissionResult:
        runtime = self._runtime_builder.build(request)
        self._prepare_runtime(runtime)
        runtime.context.progress_hook = lambda **kwargs: self._write_progress(runtime, **kwargs)
        runtime.context.event_hook = event_hook
        await self._load_durable_memory(runtime)
        request_payload = request.model_dump(mode="json")
        runtime.services.trace_store.write_request_snapshot(
            runtime.context.trace_id,
            request_payload,
        )
        self._write_trace_skeleton(runtime, request_payload)
        emit_stream_event(
            runtime.context,
            "mission.started",
            {
                "trace_id": runtime.context.trace_id,
                "request": request_payload,
                "model": runtime.context.current_model.name,
            },
        )
        self._append_runtime_event(runtime, "request_loaded", "mission request snapshot written")
        model_sequence = [runtime.context.current_model.name]
        prompt = request.prompt
        completion_retry_used = False
        try:
            while True:
                try:
                    raw_output, completion = self._normalize_run_output(await self._run_once(runtime, prompt))
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
                except ModelError as exc:
                    recovered = await self._maybe_recover_from_context_overflow(runtime, exc)
                    if recovered:
                        prompt = build_handoff_prompt(runtime.context)
                        continue
                    raise
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
                    raw_output, completion = self._normalize_run_output(await self._run_once(runtime, repair_prompt))
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
                if self._should_continue_after_completion(completion):
                    if completion_retry_used:
                        return await self._fail(
                            runtime,
                            model_sequence,
                            "completion_truncated",
                            "model response ended with finish_reason=length after continuation",
                        )
                    completion_retry_used = True
                    self._append_runtime_event(
                        runtime,
                        "completion_truncated",
                        "model response ended with finish_reason=length; continuing",
                        {
                            "finish_reason": completion.finish_reason,
                            "usage": completion.usage or {},
                        },
                    )
                    prompt = self._build_completion_continuation_prompt(
                        runtime,
                        previous_output=raw_output,
                        completion=completion,
                        result_format=result_format,
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
                    completion=completion,
                )
                trace = self._persist_trace(runtime, model_sequence, mission_result, None)
                self._maybe_schedule_skill_maintenance(trace)
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
        finally:
            with suppress(Exception):
                await runtime.browser.close()
            with suppress(Exception):
                runtime.memory_db.close()
            with suppress(Exception):
                runtime.research_meta_db.close()

    async def _run_once(self, runtime: MissionRuntime, prompt: str) -> tuple[Any, CompletionMetadata | None]:
        session = self._agent_factory.create(runtime)
        extra = {"trace_id": runtime.context.trace_id}
        agent_prompt = self._build_agent_prompt(runtime, prompt)
        prompt_size = self._prompt_size(agent_prompt)
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
                session.agent.run(agent_prompt, deps=runtime),
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
        return output, self._extract_completion_metadata(result)

    def _normalize_run_output(self, raw_run: Any) -> tuple[Any, CompletionMetadata | None]:
        if isinstance(raw_run, tuple) and len(raw_run) == 2:
            output, completion = raw_run
            return output, self._coerce_completion_metadata(completion)
        if hasattr(raw_run, "output") and hasattr(raw_run, "completion"):
            return getattr(raw_run, "output"), self._coerce_completion_metadata(getattr(raw_run, "completion"))
        return raw_run, None

    def _should_continue_after_completion(self, completion: CompletionMetadata | None) -> bool:
        return completion is not None and completion.finish_reason == "length"

    def _build_completion_continuation_prompt(
        self,
        runtime: MissionRuntime,
        *,
        previous_output: Any,
        completion: CompletionMetadata,
        result_format: ResultFormat,
    ) -> str:
        lines = [
            "The previous answer was cut off because the provider returned finish_reason=length.",
            "Continue from the cutoff and return only the final answer.",
            f"Mission prompt: {runtime.context.mission_request.prompt}",
            f"Previous finish reason: {completion.finish_reason}",
        ]
        if completion.usage:
            lines.append("Previous usage:")
            lines.append(json.dumps(completion.usage, ensure_ascii=False, sort_keys=True))
        if runtime.context.mission_request.output_schema is not None:
            lines.append("Output schema:")
            lines.append(json.dumps(runtime.context.mission_request.output_schema, ensure_ascii=False, indent=2, sort_keys=True))
            lines.append("Return a complete response that satisfies the schema.")
        elif result_format is ResultFormat.TEXT:
            lines.append("Continue the prose answer from the cutoff point without repeating the already-written part.")
            lines.append("Return the completed final answer in markdown.")
        lines.append("Previous partial answer:")
        lines.append(self._stringify_output(previous_output))
        return "\n".join(lines)

    def _extract_completion_metadata(self, result: Any) -> CompletionMetadata | None:
        finish_reason = self._find_field(result, {"finish_reason"})
        usage = self._find_field(result, {"usage"})
        if finish_reason is None and usage is None:
            return None
        normalized_usage = self._coerce_jsonable(usage) if usage is not None else None
        finish_reason_text = str(finish_reason) if finish_reason is not None else None
        return CompletionMetadata(finish_reason=finish_reason_text, usage=normalized_usage)

    def _coerce_completion_metadata(self, value: Any) -> CompletionMetadata | None:
        if value is None:
            return None
        if isinstance(value, CompletionMetadata):
            return value
        if isinstance(value, dict):
            return CompletionMetadata.model_validate(value)
        return self._extract_completion_metadata(value)

    def _find_field(self, value: Any, field_names: set[str], visited: set[int] | None = None) -> Any:
        if value is None:
            return None
        if visited is None:
            visited = set()
        marker = id(value)
        if marker in visited:
            return None
        visited.add(marker)
        if isinstance(value, dict):
            for name in field_names:
                if name in value and value[name] is not None:
                    return value[name]
            for nested in value.values():
                found = self._find_field(nested, field_names, visited)
                if found is not None:
                    return found
            return None
        if isinstance(value, (list, tuple, set)):
            for nested in value:
                found = self._find_field(nested, field_names, visited)
                if found is not None:
                    return found
            return None
        for name in field_names:
            if hasattr(value, name):
                attr = getattr(value, name)
                if callable(attr):
                    try:
                        attr = attr()
                    except Exception:
                        continue
                if attr is not None:
                    return attr
        if hasattr(value, "model_dump"):
            try:
                dumped = value.model_dump(mode="json")
            except Exception:
                dumped = None
            if dumped is not None:
                found = self._find_field(dumped, field_names, visited)
                if found is not None:
                    return found
        for name in ("response", "model_response", "result", "message", "data", "choice", "choices", "provider_response"):
            if hasattr(value, name):
                nested = getattr(value, name)
                if callable(nested):
                    try:
                        nested = nested()
                    except Exception:
                        continue
                found = self._find_field(nested, field_names, visited)
                if found is not None:
                    return found
        return None

    def _coerce_jsonable(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): self._coerce_jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._coerce_jsonable(item) for item in value]
        if hasattr(value, "model_dump"):
            try:
                return value.model_dump(mode="json")
            except Exception:
                pass
        if hasattr(value, "__dict__"):
            return {
                key: self._coerce_jsonable(item)
                for key, item in vars(value).items()
                if not key.startswith("_")
            }
        return str(value)

    def _stringify_output(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if hasattr(value, "model_dump_json"):
            try:
                return value.model_dump_json()
            except Exception:
                pass
        if isinstance(value, (dict, list, int, float, bool)) or value is None:
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    def _build_agent_prompt(self, runtime: MissionRuntime, prompt: str) -> str | list[UserContent]:
        if not self._is_skill_maintenance(runtime):
            return prompt
        return [
            prompt,
            self._build_trace_attachment(runtime),
        ]

    def _build_trace_attachment(self, runtime: MissionRuntime) -> DocumentUrl:
        parent_trace_id = self._parent_trace_id(runtime)
        trace_text = runtime.services.trace_store.read_raw_trace_text(parent_trace_id)
        data = base64.b64encode(trace_text.encode("utf-8")).decode("ascii")
        return DocumentUrl(
            url=f"data:text/plain;base64,{data}",
            media_type="text/plain",
            identifier=parent_trace_id,
        )

    def _parent_trace_id(self, runtime: MissionRuntime) -> str:
        metadata = runtime.context.mission_request.mission_metadata or {}
        parent_trace_id = metadata.get("parent_trace_id")
        if not isinstance(parent_trace_id, str) or not parent_trace_id:
            raise ModelError("skill maintenance mission is missing parent_trace_id metadata")
        return parent_trace_id

    def _is_skill_maintenance(self, runtime: MissionRuntime) -> bool:
        metadata = runtime.context.mission_request.mission_metadata or {}
        return metadata.get("mission_kind") == "skill_maintenance"

    def _prompt_size(self, prompt: str | list[UserContent]) -> int:
        if isinstance(prompt, str):
            return len(prompt)
        return len(json.dumps(prompt, default=str))

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
    ) -> ExecutionTrace:
        trace = ExecutionTrace(
            trace_id=runtime.context.trace_id,
            request=runtime.context.mission_request,
            model_sequence=model_sequence,
            tool_calls=getattr(runtime.context, "tool_calls", []),
            db_mutations=getattr(runtime.context, "db_mutations", []),
            docs_lookups=getattr(runtime.context, "docs_lookups", []),
            web_artifacts=getattr(runtime.context, "web_artifacts", []),
            memory_retrievals=getattr(runtime.context, "memory_retrievals", []),
            memory_mutations=getattr(runtime.context, "memory_mutations", []),
            compression_events=getattr(runtime.context, "compression_events", []),
            runtime_events=getattr(runtime.context, "runtime_events", []),
            result=result.result if result else None,
            completion=result.completion if result else None,
            error=error,
            started_at=runtime.context.started_at,
            completed_at=result.completed_at if result else utc_now(),
        )
        runtime.services.trace_store.write_trace(trace)
        return trace

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
        trace = self._persist_trace(runtime, model_sequence, result, error)
        self._append_runtime_event(runtime, "failed", message, {"code": code})
        self._maybe_schedule_skill_maintenance(trace)
        await runtime.browser.close()
        self._logger.error(message, extra={"trace_id": runtime.context.trace_id})
        return result

    async def _maybe_recover_from_context_overflow(self, runtime: MissionRuntime, exc: ModelError) -> bool:
        if not self._is_context_length_error(exc):
            return False
        if not runtime.services.settings.compression.enabled:
            return False
        if runtime.context.compression_in_progress:
            return False
        reason = str(exc)
        self._append_runtime_event(
            runtime,
            "context_refresh",
            "context refresh requested",
            {
                "reason": reason,
                "trigger": "context_overflow",
            },
        )
        runtime.context.compression_in_progress = True
        try:
            result = await runtime.services.context_compressor.compress(
                runtime,
                trigger="automatic",
                reason=reason,
            )
        finally:
            runtime.context.compression_in_progress = False
        if not result.ok:
            runtime.context.reasoning_notes.append(
                f"context overflow compression failed: {result.error_message}",
            )
            return False
        runtime.context.pending_context_refresh_reason = f"Automatic context compression: {reason}"
        runtime.context.reasoning_notes.append(
            f"Context was refreshed and compressed: {reason}",
        )
        return True

    def _is_context_length_error(self, exc: ModelError) -> bool:
        message = str(exc).lower()
        markers = (
            "maximum context length",
            "context length",
            "context_length",
            "requested about",
            "reduce the length of either one",
            "prompt too long",
        )
        return any(marker in message for marker in markers)

    def _prepare_runtime(self, runtime: MissionRuntime) -> None:
        metadata = runtime.context.mission_request.mission_metadata or {}
        if metadata.get("mission_kind") == "skill_maintenance":
            runtime.context.memory_tool_call_budget = runtime.services.settings.memory.maintenance_tool_budget
            runtime.services.memory_manager.ensure_schema(runtime.research_meta_db)

    def _write_trace_skeleton(self, runtime: MissionRuntime, request_payload: dict[str, Any]) -> None:
        metadata = runtime.context.mission_request.mission_metadata or {}
        if metadata.get("mission_kind") != "skill_maintenance":
            return
        parent_trace_id = metadata.get("parent_trace_id")
        parent_trace_path = None
        if isinstance(parent_trace_id, str) and parent_trace_id:
            parent_trace_path = str(runtime.services.trace_store.trace_path(parent_trace_id))
        runtime.services.trace_store.write_trace_skeleton(
            runtime.context.trace_id,
            {
                "trace_id": runtime.context.trace_id,
                "status": "started",
                "mission_kind": "skill_maintenance",
                "parent_trace_id": parent_trace_id,
                "parent_trace_path": parent_trace_path,
                "request": request_payload,
                "model_sequence": [runtime.context.current_model.name],
                "started_at": runtime.context.started_at.isoformat(),
                "runtime_events": [],
            },
        )

    async def _load_durable_memory(self, runtime: MissionRuntime) -> None:
        if not runtime.services.settings.memory.enabled:
            return
        metadata = runtime.context.mission_request.mission_metadata or {}
        if metadata.get("mission_kind") == "skill_maintenance":
            parent_trace_id = metadata.get("parent_trace_id")
            if isinstance(parent_trace_id, str) and parent_trace_id:
                trace_text = runtime.services.trace_store.read_raw_trace_text(parent_trace_id)
                results = await runtime.services.memory_manager.related_for_maintenance(
                    runtime.research_meta_db,
                    trace_text,
                )
                runtime.context.memory_retrievals.append(
                    MemoryRetrievalRecord(
                        query=parent_trace_id,
                        kinds=[],
                        results=results,
                        source="maintenance_preflight",
                    )
                )
                runtime.context.retrieved_skills = [item for item in results if item.kind == "skill"]
            return
        await runtime.services.memory_manager.preflight(runtime)

    def _maybe_schedule_skill_maintenance(self, trace: ExecutionTrace) -> None:
        if trace is None:
            return
        metadata = trace.request.mission_metadata or {}
        if metadata.get("mission_kind") == "skill_maintenance":
            return
        if not self._runtime_builder.services.settings.memory.maintenance_enabled:
            return
        if self._maintenance_runner is None:
            self._logger.info("maintenance runner unavailable; job not enqueued", extra={"trace_id": trace.trace_id})
            return
        self._maintenance_runner.enqueue(trace)

    def build_skill_maintenance_request(self, trace: ExecutionTrace) -> MissionRequest:
        strongest = self._runtime_builder.services.model_catalog.strongest_allowed(
            self._runtime_builder.services.model_catalog.resolve_allowed(trace.request)
        )
        return MissionRequest(
            prompt=self._build_skill_maintenance_prompt(trace),
            memory_db_path=trace.request.memory_db_path,
            research_meta_db_path=trace.request.research_meta_db_path,
            preferred_model=strongest.name,
            allowed_models=[item.name for item in self._runtime_builder.services.model_catalog.resolve_allowed(trace.request)],
            mission_metadata={
                "mission_kind": "skill_maintenance",
                "parent_trace_id": trace.trace_id,
            },
            web_enabled=False,
            db_mutation_enabled=True,
        )

    async def _run_skill_maintenance(self, trace: ExecutionTrace) -> None:
        request = self.build_skill_maintenance_request(trace)
        try:
            await self.run(request)
        except Exception as exc:  # pragma: no cover
            self._logger.error(
                "skill maintenance failed: %s",
                exc,
                extra={"trace_id": trace.trace_id},
            )

    def _build_skill_maintenance_prompt(self, trace: ExecutionTrace) -> str:
        return "\n".join(
            [
                "Your mission is to harden Doron's skills after a completed mission.",
                "Use skill tools to write, update, or deprecate skills.",
                "Use graph read tools to inspect existing graph state when that helps decide what skill changes are needed.",
                "Encourage good tool use and discourage wasteful repeated web source rediscovery.",
                "Only mutate skill records.",
                f"Parent trace ID: {trace.trace_id}",
                "A full trace.json attachment will be provided separately as the canonical mission record.",
                "Return a concise plain-text summary of what skill changes you made.",
            ]
        )

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
        runtime.services.trace_store.write_progress(
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
