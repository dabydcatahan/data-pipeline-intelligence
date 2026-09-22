"""Opt-in local capture of error text the analyzer didn't recognize (F025).

This is the one deliberate exception to "nothing is ever persisted" (DEC-005): a
user can explicitly click "Flag this error" in the UI when the analyzer returns
``unknown_error``, so the pattern can be reviewed and added to
``error_patterns.json`` later. It never runs automatically - see
:attr:`app.core.config.AppSettings.error_misses_path`, which is ``None`` (disabled)
unless a deployment opts in.

Unlike uploaded ADF JSON (which is a known, typed schema that ``scrub_secrets``
strips secret-bearing *keys* from), pasted error text is unstructured prose - there
are no keys to check, only substrings that *look* secret-shaped. :func:`redact_text`
is a best-effort regex redactor over that raw text before it's ever written to disk.
It is not a guarantee: a secret in an unusual shape can still slip through, which is
why this stays opt-in and local rather than automatic.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from app.core.logging import get_logger
from app.parsers.base import SECRET_KEY_HINTS

logger = get_logger(__name__)

_REDACTED = "***redacted***"

#: ``key=value`` / ``key: value`` pairs where the key looks secret-bearing (reuses
#: the same keyword list ``scrub_secrets`` uses for JSON, so "what counts as a
#: secret" has one source of truth across both the JSON and free-text paths).
_KEY_VALUE_RE = re.compile(
    r"(?i)\b(" + "|".join(re.escape(h) for h in SECRET_KEY_HINTS) + r")\b\s*[:=]\s*"
    r"(['\"]?)([^'\";,\s]+)\2"
)
#: Azure Storage / SQL connection-string style ``AccountKey=...;`` fragments.
_ACCOUNT_KEY_RE = re.compile(
    r"(?i)\b(AccountKey|SharedAccessSignature|SharedAccessKey)\s*=\s*[^;'\"\s]+"
)
#: ``Authorization: Bearer <token>`` headers echoed into an error message.
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-_.]+")
#: A SAS token's ``sig=`` query parameter.
_SAS_SIG_RE = re.compile(r"(?i)([?&]sig=)[^&\s'\"]+")


def redact_text(text: str) -> str:
    """Best-effort redaction of secret-shaped substrings in free text.

    Not a guarantee - see module docstring. Applied before any error text this
    module writes to disk.
    """
    text = _ACCOUNT_KEY_RE.sub(lambda m: f"{m.group(1)}={_REDACTED}", text)
    text = _BEARER_RE.sub(f"Bearer {_REDACTED}", text)
    text = _SAS_SIG_RE.sub(lambda m: f"{m.group(1)}{_REDACTED}", text)
    text = _KEY_VALUE_RE.sub(lambda m: f"{m.group(1)}={_REDACTED}", text)
    return text


def record_unrecognized_error(error_text: str, path: Path) -> None:
    """Append one redacted, timestamped error to ``path`` as a JSON line.

    Creates parent directories if needed. Raises on I/O failure - the caller (the
    UI) is responsible for catching and showing a friendly message, since a failed
    write here should never crash the analysis the user already has on screen.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "flagged_at": datetime.now(UTC).isoformat(),
        "error_text": redact_text(error_text),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    logger.info("Recorded an unrecognized error to %s for later pattern review.", path)
