from __future__ import annotations

import logging
from pathlib import Path

from agent_platform.infrastructure.logging import configure_logging, get_logger
from agent_platform.config.settings import LoggingSettings


def test_logging_defaults_trace_id_when_missing(tmp_path: Path) -> None:
    settings = LoggingSettings(directory=tmp_path / "logs", max_bytes=10_000, backup_count=1)
    configure_logging(settings)

    logger = get_logger("mission")
    logger.info("maintenance runner started")

    log_path = settings.directory / "mission.log"
    assert log_path.exists()
    assert "trace_id=- maintenance runner started" in log_path.read_text(encoding="utf-8")
