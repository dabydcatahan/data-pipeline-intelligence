"""Exception hierarchy for the application.

All errors that are meaningful to the user derive from :class:`ADFIntelligenceError`
so the UI layer can catch a single base type and render a friendly message.
"""

from __future__ import annotations


class ADFIntelligenceError(Exception):
    """Base class for all application errors."""

    #: Short, user-facing category shown in the UI.
    category: str = "Error"


class ConfigurationError(ADFIntelligenceError):
    """Invalid or missing configuration."""

    category = "Configuration error"


class UploadError(ADFIntelligenceError):
    """Base class for problems with an uploaded file."""

    category = "Upload error"


class FileTooLargeError(UploadError):
    """Uploaded file exceeds the configured size limit."""

    category = "File too large"

    def __init__(self, *, size: int, limit: int) -> None:
        self.size = size
        self.limit = limit
        super().__init__(
            f"Uploaded file is {size} bytes, which exceeds the limit of {limit} bytes."
        )


class UnsupportedFileTypeError(UploadError):
    """Uploaded file has a disallowed extension."""

    category = "Unsupported file type"


class InvalidJsonError(UploadError):
    """Uploaded content is not valid JSON, or not a JSON object."""

    category = "Invalid JSON"


class UnsupportedArtifactError(ADFIntelligenceError):
    """The JSON is valid but is not a recognised ADF artifact we can process."""

    category = "Unsupported artifact"


class ParseError(ADFIntelligenceError):
    """A recognised artifact could not be parsed into the internal model.

    Non-fatal issues are collected as warnings instead (see
    :class:`app.parsers.base.ParseContext`); this is raised only when parsing
    cannot produce any usable model at all.
    """

    category = "Parse error"


class LineageError(ADFIntelligenceError):
    """Raised by the lineage engine when a graph operation cannot be completed."""

    category = "Lineage error"


class AIAnalysisError(ADFIntelligenceError):
    """Raised when AI-powered error analysis fails.

    This can be due to:
    - GPT4All not installed
    - Model download failure
    - Model inference error
    - Response parsing error
    """

    category = "AI analysis error"
