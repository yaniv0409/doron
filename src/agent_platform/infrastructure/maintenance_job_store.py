from __future__ import annotations

import json
import uuid
from pathlib import Path

from pydantic import TypeAdapter

from agent_platform.config.settings import TraceSettings
from agent_platform.domain.enums import MaintenanceJobStatus
from agent_platform.domain.models import ExecutionTrace, MaintenanceJobRecord, MissionError, MissionRequest, utc_now
from agent_platform.infrastructure.trace_store import TraceStore


class MaintenanceJobStore:
    def __init__(self, settings: TraceSettings, trace_store: TraceStore) -> None:
        self._trace_store = trace_store
        self._jobs_directory = settings.directory / "maintenance-jobs"
        self._jobs_directory.mkdir(parents=True, exist_ok=True)
        self._adapter = TypeAdapter(MaintenanceJobRecord)

    def enqueue(self, parent_trace: ExecutionTrace, request: MissionRequest) -> MaintenanceJobRecord:
        record = MaintenanceJobRecord(
            trace_id=str(uuid.uuid4()),
            parent_trace_id=parent_trace.trace_id,
            request=request,
        )
        self.save(record)
        self._write_trace_artifacts(parent_trace, record)
        return record

    def list_jobs(self) -> list[MaintenanceJobRecord]:
        records: list[MaintenanceJobRecord] = []
        for path in sorted(self._jobs_directory.glob("*.json")):
            records.append(self._adapter.validate_python(json.loads(path.read_text(encoding="utf-8"))))
        return records

    def read(self, trace_id: str) -> MaintenanceJobRecord | None:
        path = self._path(trace_id)
        if not path.exists():
            return None
        return self._adapter.validate_python(json.loads(path.read_text(encoding="utf-8")))

    def save(self, record: MaintenanceJobRecord) -> MaintenanceJobRecord:
        self._path(record.trace_id).write_text(
            json.dumps(self._adapter.dump_python(record, mode="json"), indent=2),
            encoding="utf-8",
        )
        return record

    def mark_running(self, record: MaintenanceJobRecord) -> MaintenanceJobRecord:
        record.status = MaintenanceJobStatus.RUNNING
        record.attempt_count += 1
        record.started_at = utc_now()
        record.last_error = None
        return self.save(record)

    def mark_completed(self, record: MaintenanceJobRecord) -> MaintenanceJobRecord:
        record.status = MaintenanceJobStatus.COMPLETED
        record.completed_at = utc_now()
        return self.save(record)

    def mark_failed(self, record: MaintenanceJobRecord, error: MissionError) -> MaintenanceJobRecord:
        record.status = MaintenanceJobStatus.FAILED
        record.completed_at = utc_now()
        record.last_error = error
        return self.save(record)

    def mark_cancelled(self, record: MaintenanceJobRecord, error: MissionError) -> MaintenanceJobRecord:
        record.status = MaintenanceJobStatus.CANCELLED
        record.completed_at = utc_now()
        record.last_error = error
        self.save(record)
        self._write_cancelled_trace(record, error)
        return record

    def pending_jobs(self) -> list[MaintenanceJobRecord]:
        return [
            item
            for item in self.list_jobs()
            if item.status in {MaintenanceJobStatus.PENDING, MaintenanceJobStatus.RUNNING}
        ]

    def _write_trace_artifacts(self, parent_trace: ExecutionTrace, record: MaintenanceJobRecord) -> None:
        request_payload = record.request.model_dump(mode="json")
        self._trace_store.write_request_snapshot(record.trace_id, request_payload)
        self._trace_store.write_progress(
            record.trace_id,
            {
                "trace_id": record.trace_id,
                "parent_trace_id": record.parent_trace_id,
                "phase": "maintenance.queued",
                "message": "maintenance job queued",
                "model": record.request.preferred_model or "",
                "metadata": {},
                "runtime_events": [],
            },
        )
        self._trace_store.write_trace_skeleton(
            record.trace_id,
            {
                "trace_id": record.trace_id,
                "status": "queued",
                "mission_kind": "memory_maintenance",
                "parent_trace_id": record.parent_trace_id,
                "parent_trace_path": str(self._trace_store.trace_path(parent_trace.trace_id)),
                "request": request_payload,
                "model_sequence": [],
                "started_at": record.created_at.isoformat(),
                "runtime_events": [],
            },
        )

    def _write_cancelled_trace(self, record: MaintenanceJobRecord, error: MissionError) -> None:
        self._trace_store.write_trace_skeleton(
            record.trace_id,
            {
                "trace_id": record.trace_id,
                "status": "cancelled",
                "mission_kind": "memory_maintenance",
                "parent_trace_id": record.parent_trace_id,
                "request": record.request.model_dump(mode="json"),
                "model_sequence": [],
                "started_at": (record.started_at or record.created_at).isoformat(),
                "completed_at": record.completed_at.isoformat() if record.completed_at else utc_now().isoformat(),
                "error": error.model_dump(mode="json"),
                "runtime_events": [],
            },
        )

    def _path(self, trace_id: str) -> Path:
        return self._jobs_directory / f"{trace_id}.json"
