from __future__ import annotations

from agent_platform.cli.api_client import MissionApiClient


class _FakeProcess:
    def __init__(self) -> None:
        self.terminated = False
        self.killed = False
        self._alive = True

    def poll(self) -> int | None:
        return None if self._alive else 0

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        self._alive = False
        return 0

    def kill(self) -> None:
        self.killed = True
        self._alive = False


def test_close_keeps_autostarted_server_alive() -> None:
    client = MissionApiClient("http://127.0.0.1:8000", start_server=True)
    process = _FakeProcess()
    client._server_process = process  # type: ignore[attr-defined]

    client.close()

    assert client._server_process is process  # type: ignore[attr-defined]
    assert not process.terminated
    assert not process.killed


def test_shutdown_server_terminates_autostarted_server() -> None:
    client = MissionApiClient("http://127.0.0.1:8000", start_server=True)
    process = _FakeProcess()
    client._server_process = process  # type: ignore[attr-defined]

    client.shutdown_server()

    assert client._server_process is None  # type: ignore[attr-defined]
    assert process.terminated
    assert not process.killed
