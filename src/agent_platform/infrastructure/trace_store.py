from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from agent_platform.config.settings import TraceSettings
from agent_platform.domain.models import ExecutionTrace


class TraceStore:
    def __init__(self, settings: TraceSettings) -> None:
        self._settings = settings
        self._settings.directory.mkdir(parents=True, exist_ok=True)
        self._settings.checkpoint_directory.mkdir(parents=True, exist_ok=True)
        self._adapter = TypeAdapter(ExecutionTrace)

    def write_trace(self, trace: ExecutionTrace) -> Path:
        trace_dir = self._settings.directory / trace.trace_id
        trace_dir.mkdir(parents=True, exist_ok=True)
        path = trace_dir / "trace.json"
        payload = self._adapter.dump_python(trace, mode="json")
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def read_trace(self, trace_id: str) -> ExecutionTrace:
        path = self._settings.directory / trace_id / "trace.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        return self._adapter.validate_python(payload)

    def write_request_snapshot(self, trace_id: str, payload: dict[str, Any]) -> Path:
        trace_dir = self._settings.directory / trace_id
        trace_dir.mkdir(parents=True, exist_ok=True)
        path = trace_dir / "request.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def write_progress(self, trace_id: str, payload: dict[str, Any]) -> Path:
        trace_dir = self._settings.directory / trace_id
        trace_dir.mkdir(parents=True, exist_ok=True)
        path = trace_dir / "progress.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def create_checkpoint(self, trace_id: str, db_path: str) -> Path:
        source_path = Path(db_path)
        if not source_path.exists():
            raise FileNotFoundError(f"database path does not exist for checkpoint: {db_path}")
        checkpoint_path = self._settings.checkpoint_directory / trace_id
        if source_path.is_dir():
            if checkpoint_path.exists():
                shutil.rmtree(checkpoint_path)
            shutil.copytree(source_path, checkpoint_path)
            return checkpoint_path
        checkpoint_path = checkpoint_path.with_suffix(source_path.suffix or ".kuzu")
        shutil.copy2(source_path, checkpoint_path)
        return checkpoint_path
