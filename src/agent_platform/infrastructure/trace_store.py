from __future__ import annotations

import json
import re
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
        path = self.trace_path(trace.trace_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._adapter.dump_python(trace, mode="json")
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def read_trace(self, trace_id: str) -> ExecutionTrace:
        path = self.trace_path(trace_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return self._adapter.validate_python(payload)

    def read_raw_trace_text(self, trace_id: str) -> str:
        return self.trace_path(trace_id).read_text(encoding="utf-8")

    def read_trace_head(self, trace_id: str, chars: int) -> str:
        return self.read_raw_trace_text(trace_id)[: max(0, chars)]

    def grep_trace_text(
        self,
        trace_id: str,
        pattern: str,
        *,
        radius_lines: int,
        max_matches: int,
        max_lines: int,
    ) -> list[dict[str, Any]]:
        text = self.read_raw_trace_text(trace_id)
        lines = text.splitlines()
        regex = re.compile(pattern, re.IGNORECASE)
        snippets: list[dict[str, Any]] = []
        total_lines = 0
        for index, line in enumerate(lines):
            if not regex.search(line):
                continue
            start = max(0, index - radius_lines)
            end = min(len(lines), index + radius_lines + 1)
            snippet_lines = lines[start:end]
            next_total = total_lines + len(snippet_lines)
            if next_total > max_lines and snippets:
                break
            snippets.append(
                {
                    "match_line": index + 1,
                    "start_line": start + 1,
                    "end_line": end,
                    "lines": [
                        {"line_number": start + offset + 1, "text": item}
                        for offset, item in enumerate(snippet_lines)
                    ],
                }
            )
            total_lines = next_total
            if len(snippets) >= max_matches or total_lines >= max_lines:
                break
        return snippets

    def write_request_snapshot(self, trace_id: str, payload: dict[str, Any]) -> Path:
        trace_dir = self._settings.directory / trace_id
        trace_dir.mkdir(parents=True, exist_ok=True)
        path = trace_dir / "request.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def write_trace_skeleton(self, trace_id: str, payload: dict[str, Any]) -> Path:
        path = self.trace_path(trace_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def trace_path(self, trace_id: str) -> Path:
        return self._settings.directory / trace_id / "trace.json"

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
