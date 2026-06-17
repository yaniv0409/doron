from __future__ import annotations

import json
import subprocess
import sys
import time
from typing import Iterator
from urllib.parse import urlparse

import httpx

from agent_platform.contracts.api import MissionRunRequest, MissionRunResponse, MissionStreamEvent


class MissionApiClient:
    def __init__(
        self,
        api_url: str,
        *,
        start_server: bool = False,
        server_ready_timeout_seconds: int = 20,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._start_server = start_server
        self._server_ready_timeout_seconds = server_ready_timeout_seconds
        self._server_process: subprocess.Popen[str] | None = None

    def close(self) -> None:
        if self._server_process is not None and self._server_process.poll() is None:
            self._server_process.terminate()
            try:
                self._server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._server_process.kill()
        self._server_process = None

    def ensure_running(self) -> None:
        if self._health_check():
            return
        if not self._start_server:
            raise RuntimeError(f"API server is not reachable at {self._api_url}")
        self._start_local_server()
        self._wait_for_health()

    def stream_mission(self, request: MissionRunRequest) -> Iterator[MissionStreamEvent]:
        self.ensure_running()
        request = request.model_copy(update={"stream": True})
        with httpx.Client(timeout=None) as client:
            with client.stream("POST", f"{self._api_url}/missions/run", json=request.model_dump(mode="json")) as response:
                response.raise_for_status()
                yield from self._iter_sse_events(response.iter_lines())

    def run_mission(self, request: MissionRunRequest) -> MissionRunResponse:
        self.ensure_running()
        request = request.model_copy(update={"stream": False})
        with httpx.Client(timeout=None) as client:
            response = client.post(
                f"{self._api_url}/missions/run",
                json=request.model_dump(mode="json"),
            )
            response.raise_for_status()
            return MissionRunResponse.model_validate(response.json())

    def _iter_sse_events(self, lines: Iterator[str]) -> Iterator[MissionStreamEvent]:
        event_name: str | None = None
        data_lines: list[str] = []
        for line in lines:
            if not line:
                if event_name is not None and data_lines:
                    data = json.loads("\n".join(data_lines))
                    yield MissionStreamEvent(event=event_name, data=data)
                event_name = None
                data_lines = []
                continue
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
                continue
            if line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())
        if event_name is not None and data_lines:
            data = json.loads("\n".join(data_lines))
            yield MissionStreamEvent(event=event_name, data=data)

    def _health_check(self) -> bool:
        try:
            with httpx.Client(timeout=2) as client:
                response = client.get(f"{self._api_url}/health")
                return response.status_code == 200
        except Exception:
            return False

    def _start_local_server(self) -> None:
        parsed = urlparse(self._api_url)
        if parsed.scheme not in {"http", "https"}:
            raise RuntimeError(f"unsupported API URL: {self._api_url}")
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("auto-start is only supported for local API URLs")
        if parsed.port is None:
            raise RuntimeError("auto-start requires an explicit port in api_url")
        if self._server_process is not None and self._server_process.poll() is None:
            return
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "agent_platform.api.app:create_app",
            "--factory",
            "--host",
            parsed.hostname,
            "--port",
            str(parsed.port),
        ]
        self._server_process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

    def _wait_for_health(self) -> None:
        deadline = time.time() + self._server_ready_timeout_seconds
        while time.time() < deadline:
            if self._health_check():
                return
            time.sleep(0.5)
        raise RuntimeError(f"API server did not become ready at {self._api_url}")
