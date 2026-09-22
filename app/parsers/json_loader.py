"""Safe loading and classification of uploaded ADF JSON (F002).

Responsibilities:
* enforce the configured size limit on raw bytes *before* any parsing;
* validate extension;
* decode UTF-8 and ``json.loads`` with explicit, user-safe error wrapping;
* reject anything that is not a JSON object;
* classify the object as a Pipeline, Mapping Data Flow, or Unknown artifact.

No file is ever written to disk. ``json.loads`` is the only deserializer used.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any

from app.core.config import AppSettings, get_settings
from app.core.exceptions import (
    FileTooLargeError,
    InvalidJsonError,
    UnsupportedFileTypeError,
)
from app.models.artifact import ArtifactKind

_PIPELINE_RESOURCE_TYPE = "microsoft.datafactory/factories/pipelines"
_DATAFLOW_RESOURCE_TYPE = "microsoft.datafactory/factories/dataflows"


def _check_extension(filename: str, settings: AppSettings) -> None:
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix not in settings.allowed_extensions:
        allowed = ", ".join(settings.allowed_extensions)
        raise UnsupportedFileTypeError(
            f"File '{filename}' has extension '{suffix or '(none)'}'. Allowed: {allowed}."
        )


def load_adf_json(
    data: bytes,
    *,
    filename: str = "upload.json",
    settings: AppSettings | None = None,
) -> dict[str, Any]:
    """Validate size/type and return the parsed JSON object.

    Raises :class:`FileTooLargeError`, :class:`UnsupportedFileTypeError` or
    :class:`InvalidJsonError` - all subclasses of
    :class:`app.core.exceptions.UploadError`.
    """
    settings = settings or get_settings()
    _check_extension(filename, settings)

    if len(data) > settings.max_upload_bytes:
        raise FileTooLargeError(size=len(data), limit=settings.max_upload_bytes)
    if not data.strip():
        raise InvalidJsonError(f"File '{filename}' is empty.")

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidJsonError(f"File '{filename}' is not valid UTF-8 text.") from exc

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidJsonError(
            f"File '{filename}' is not valid JSON "
            f"(line {exc.lineno}, column {exc.colno}): {exc.msg}."
        ) from exc

    if not isinstance(parsed, dict):
        raise InvalidJsonError(
            f"File '{filename}' must contain a JSON object at the top level, "
            f"got {type(parsed).__name__}."
        )
    return parsed


def _resource_type(obj: dict[str, Any]) -> str:
    value = obj.get("type")
    return value.lower() if isinstance(value, str) else ""


def detect_artifact_kind(obj: dict[str, Any]) -> ArtifactKind:
    """Best-effort classification of a parsed ADF JSON object.

    Handles both the plain export form (``{"name": ..., "properties": {...}}``)
    and the ARM-template resource form (``type`` = ``Microsoft.DataFactory/...``).
    """
    resource_type = _resource_type(obj)
    if resource_type == _PIPELINE_RESOURCE_TYPE:
        return ArtifactKind.PIPELINE
    if resource_type == _DATAFLOW_RESOURCE_TYPE:
        return ArtifactKind.MAPPING_DATA_FLOW

    properties = obj.get("properties")
    props = properties if isinstance(properties, dict) else {}

    prop_type = props.get("type")
    if isinstance(prop_type, str):
        lowered = prop_type.lower()
        if lowered == "mappingdataflow":
            return ArtifactKind.MAPPING_DATA_FLOW
        if lowered == "flowlet":
            return ArtifactKind.MAPPING_DATA_FLOW

    # Structural fallbacks.
    if isinstance(props.get("activities"), list) or isinstance(obj.get("activities"), list):
        return ArtifactKind.PIPELINE

    type_properties = props.get("typeProperties")
    if isinstance(type_properties, dict) and (
        "sources" in type_properties
        or "sinks" in type_properties
        or "transformations" in type_properties
    ):
        return ArtifactKind.MAPPING_DATA_FLOW

    return ArtifactKind.UNKNOWN
