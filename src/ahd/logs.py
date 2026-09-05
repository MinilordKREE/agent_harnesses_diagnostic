"""Structured logging: stdlib ``logging`` with a JSON-lines formatter.

No reference source: written fresh for ahd (see docs/reuse/M0.md). None of the surveyed
repos has a JSON log formatter; their structured record is the trace file, not the logger.

Library code obtains loggers with ``logging.getLogger(__name__)`` and never prints.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

PACKAGE_LOGGER = "ahd"

_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
    | {"message", "asctime", "taskName"}
)


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, msg, extra fields, and exc if present."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class _AhdHandlerMarker:
    """Mixin used to recognise handlers installed by :func:`configure_logging`."""


class _ConsoleHandler(logging.StreamHandler[TextIO], _AhdHandlerMarker):
    pass


class _JsonFileHandler(logging.FileHandler, _AhdHandlerMarker):
    pass


def configure_logging(
    *,
    level: int = logging.INFO,
    json_path: Path | None = None,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Install a human console handler and, if ``json_path`` is given, a JSON-lines file handler.

    Idempotent: previously installed ahd handlers are removed first. Only the ``ahd`` logger
    tree is configured; the root logger is left alone.
    """
    logger = logging.getLogger(PACKAGE_LOGGER)
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        if isinstance(handler, _AhdHandlerMarker):
            logger.removeHandler(handler)
            handler.close()

    console = _ConsoleHandler(stream or sys.stderr)
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(console)

    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = _JsonFileHandler(json_path, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        logger.addHandler(file_handler)
    return logger
