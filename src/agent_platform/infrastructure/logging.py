from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from agent_platform.config.settings import LoggingSettings
from agent_platform.domain.enums import LogCategory


def configure_logging(settings: LoggingSettings) -> None:
    settings.directory.mkdir(parents=True, exist_ok=True)
    for category in LogCategory:
        logger = logging.getLogger(category.value)
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        logger.propagate = False
        logger.addHandler(_build_handler(settings, category.value))


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def _build_handler(settings: LoggingSettings, name: str) -> RotatingFileHandler:
    path = Path(settings.directory) / f"{name}.log"
    handler = RotatingFileHandler(
        path,
        maxBytes=settings.max_bytes,
        backupCount=settings.backup_count,
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s trace_id=%(trace_id)s %(message)s",
        )
    )
    return handler
