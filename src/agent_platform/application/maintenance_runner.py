from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from agent_platform.config.settings import AppSettings
from agent_platform.domain.enums import LogCategory, MissionStatus
from agent_platform.domain.models import ExecutionTrace, MaintenanceJobRecord, MissionError
from agent_platform.infrastructure.maintenance_job_store import MaintenanceJobStore
from agent_platform.infrastructure.logging import get_logger


class MaintenanceRunner:
    def __init__(self, settings: AppSettings, mission_service: Any, job_store: MaintenanceJobStore) -> None:
        self._settings = settings
        self._mission_service = mission_service
        self._job_store = job_store
        self._logger = get_logger(LogCategory.MISSION.value)
        self._started = False
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def start(self) -> None:
        self._started = True
        self._logger.info("maintenance runner started")
        await self.resume_pending()

    async def stop(self) -> None:
        self._started = False
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._logger.info("maintenance runner stopped")

    async def resume_pending(self) -> None:
        if not self._settings.memory.maintenance_enabled:
            return
        for record in self._job_store.pending_jobs():
            self._schedule(record.trace_id)

    def enqueue(self, parent_trace: ExecutionTrace) -> MaintenanceJobRecord | None:
        if not self._settings.memory.maintenance_enabled:
            self._logger.info("maintenance job skipped; disabled", extra={"trace_id": parent_trace.trace_id})
            return None
        request = self._mission_service.build_skill_maintenance_request(parent_trace)
        record = self._job_store.enqueue(parent_trace, request)
        self._logger.info(
            "maintenance job queued",
            extra={"trace_id": parent_trace.trace_id, "maintenance_trace_id": record.trace_id},
        )
        if self._started:
            self._schedule(record.trace_id)
        return record

    def _schedule(self, trace_id: str) -> None:
        task = self._tasks.get(trace_id)
        if task is not None and not task.done():
            return
        self._tasks[trace_id] = asyncio.create_task(self._run_job(trace_id))
        self._tasks[trace_id].add_done_callback(lambda _task: self._tasks.pop(trace_id, None))

    async def _run_job(self, trace_id: str) -> None:
        record = self._job_store.read(trace_id)
        if record is None:
            self._logger.warning("maintenance job missing", extra={"maintenance_trace_id": trace_id})
            return
        record = self._job_store.mark_running(record)
        self._logger.info(
            "maintenance job started",
            extra={"trace_id": record.parent_trace_id, "maintenance_trace_id": record.trace_id},
        )
        try:
            result = await self._mission_service.run(record.request)
        except asyncio.CancelledError:
            error = MissionError(code="cancelled", message="maintenance task cancelled")
            self._job_store.mark_cancelled(record, error)
            self._logger.warning(
                "maintenance job cancelled",
                extra={"trace_id": record.parent_trace_id, "maintenance_trace_id": record.trace_id},
            )
            raise
        except Exception as exc:  # pragma: no cover
            error = MissionError(code=type(exc).__name__, message=str(exc))
            self._job_store.mark_failed(record, error)
            self._logger.error(
                "maintenance job failed",
                extra={"trace_id": record.parent_trace_id, "maintenance_trace_id": record.trace_id},
            )
            return
        if result.status is MissionStatus.COMPLETED:
            self._job_store.mark_completed(record)
            self._logger.info(
                "maintenance job completed",
                extra={"trace_id": record.parent_trace_id, "maintenance_trace_id": record.trace_id},
            )
            return
        error = result.error or MissionError(code="maintenance_failed", message="maintenance mission failed")
        self._job_store.mark_failed(record, error)
        self._logger.warning(
            "maintenance job returned failure",
            extra={"trace_id": record.parent_trace_id, "maintenance_trace_id": record.trace_id},
        )
