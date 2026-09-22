"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.core.config import AppSettings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def read_fixture_bytes():
    def _read(name: str) -> bytes:
        return (FIXTURES / name).read_bytes()

    return _read


@pytest.fixture
def settings() -> AppSettings:
    # Explicit construction so tests never depend on a developer's local .env.
    return AppSettings(
        max_upload_bytes=1_000_000,
        allowed_extensions=(".json",),
        log_level="WARNING",
    )
