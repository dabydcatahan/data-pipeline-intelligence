"""Application configuration.

All configuration is environment-driven (prefix ``ADFPI_``) with safe defaults so
the application runs with zero setup. No secrets are required.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogFormat = Literal["text", "json"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

_DEFAULT_MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MiB


class AppSettings(BaseSettings):
    """Runtime settings, populated from environment variables / ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="ADFPI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    app_title: str = "PipeSight"

    # Uploads / limits
    max_upload_bytes: int = Field(default=_DEFAULT_MAX_UPLOAD_BYTES, ge=1_024, le=100 * 1024 * 1024)
    allowed_extensions: tuple[str, ...] = (".json",)

    # Logging
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "text"

    # Error analyzer: opt-in local capture of unrecognized errors (F025).
    # None (default) means the feature is off - nothing is ever written to disk.
    # Set to a file path to let a user explicitly flag an unrecognized error from
    # the UI; the text is redacted (app.insights.error_feedback) before being
    # appended. This is the ONLY place in the app that persists anything, and only
    # on explicit user action - never automatic, never for uploaded ADF JSON.
    error_misses_path: str | None = None

    @field_validator("allowed_extensions", mode="before")
    @classmethod
    def _split_extensions(cls, value: object) -> object:
        """Accept a comma-separated string from the environment."""
        if isinstance(value, str):
            return tuple(
                ext if ext.startswith(".") else f".{ext}"
                for ext in (part.strip().lower() for part in value.split(","))
                if ext
            )
        return value

    @property
    def max_upload_mib(self) -> float:
        return self.max_upload_bytes / (1024 * 1024)


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return the process-wide settings singleton."""
    return AppSettings()
