"""Shared parsing utilities."""

from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)

#: ADF ``typeProperties`` / dataset keys that may carry secrets. The parser drops
#: these entirely so they can never reach the model, UI, export or AI layers.
SECRET_KEY_HINTS = frozenset(
    {
        "connectionstring",
        "connectionString",
        "accountkey",
        "accesskey",
        "password",
        "secret",
        "sasuri",
        "sastoken",
        "token",
        "apikey",
        "credential",
        "serviceprincipalkey",
        "clientsecret",
        "encryptedcredential",
    }
)


class ParseContext:
    """Collects non-fatal warnings during a parse so processing can continue.

    Warnings are plain strings safe to show the user. Never put uploaded JSON
    content or column values in a warning.
    """

    def __init__(self) -> None:
        self._warnings: list[str] = []

    def warn(self, message: str) -> None:
        self._warnings.append(message)
        logger.warning("parse warning: %s", message)

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(self._warnings)

    def extend(self, messages: tuple[str, ...]) -> None:
        for message in messages:
            self.warn(message)


def scrub_secrets(value: object) -> object:
    """Recursively drop secret-bearing keys from a parsed JSON structure."""
    if isinstance(value, dict):
        cleaned: dict[str, object] = {}
        for key, item in value.items():
            if key.lower() in {hint.lower() for hint in SECRET_KEY_HINTS}:
                cleaned[key] = "***redacted***"
                continue
            cleaned[key] = scrub_secrets(item)
        return cleaned
    if isinstance(value, list):
        return [scrub_secrets(item) for item in value]
    return value
