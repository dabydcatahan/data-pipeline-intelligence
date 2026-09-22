"""Structured logging setup.

Security note: uploaded JSON content and column *values* must never be logged.
Log only structural metadata (counts, names, artifact kinds, error types).
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from app.core.config import AppSettings, get_settings

_CONFIGURED = False


class _JsonFormatter(logging.Formatter):
    """Minimal JSON log formatter (no external dependency)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_type"] = getattr(record.exc_info[0], "__name__", str(record.exc_info[0]))
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(settings: AppSettings | None = None, *, force: bool = False) -> None:
    """Configure the root logger once, based on settings."""
    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    settings = settings or get_settings()
    handler = logging.StreamHandler(stream=sys.stderr)
    if settings.log_format == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s | %(message)s")
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a module logger, ensuring logging is configured."""
    configure_logging()
    return logging.getLogger(name)
