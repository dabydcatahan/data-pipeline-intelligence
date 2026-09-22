"""Error analysis models for lineage inspection recommendations (F025)."""

from __future__ import annotations

from enum import StrEnum

from app.models.common import ModelBase
from app.models.lineage import ColumnJourney


class ErrorSeverity(StrEnum):
    """Severity level of an ADF error."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class EntityReference(ModelBase):
    """Reference to a column, stream, or dataset in the uploaded artifacts."""

    entity_type: str  # "column", "stream", "dataset", "activity", "transformation"
    name: str
    stream: str | None = None  # For column references: stream::column
    confidence: float = 1.0  # 0.0-1.0; how confident we are this entity was in the error


class LineageCheckpoint(ModelBase):
    """A specific lineage path or column to inspect."""

    checkpoint_type: str  # "column", "stream", "path", "transformation"
    description: str  # "Check full lineage path from source to sink"
    target_entities: tuple[EntityReference, ...] = ()  # Which columns/streams to inspect
    severity: ErrorSeverity = ErrorSeverity.WARNING
    rationale: str = ""  # Why this checkpoint matters for the error


class AISuggestion(ModelBase):
    """AI-powered suggestions for fixing an ADF error."""

    root_cause: str
    """One sentence explaining why the error occurred."""

    diagnosis: str
    """2-3 sentences describing what is broken in the data flow."""

    fixes: tuple[str, ...]
    """Specific, actionable steps to resolve the error (3-5 items)."""

    prevention: tuple[str, ...]
    """Strategies to prevent similar errors in the future (2-3 items)."""

    confidence: float = 0.5
    """Confidence score 0.0-1.0; how sure is the AI about this analysis."""


class ErrorAnalysis(ModelBase):
    """Analysis of an ADF error and recommended lineage checks."""

    error_text: str  # Original error message
    error_summary: str  # Human-readable summary of what the error means
    detected_entities: tuple[EntityReference, ...] = ()  # Columns/streams mentioned in error
    inferred_error_type: str = "unknown"  # error type slug, e.g. "data_type_mismatch"
    severity: ErrorSeverity = ErrorSeverity.ERROR
    lineage_checkpoints: tuple[LineageCheckpoint, ...] = ()  # Recommendations
    suggested_lineage: tuple[ColumnJourney, ...] = ()  # Column lineage journeys to inspect
    ai_suggestions: AISuggestion | None = None  # AI-powered analysis (optional)
    additional_notes: str = ""  # Extra context or follow-up steps


class ErrorAnalysisRecord(ModelBase):
    """A stored record of a past error analysis for history/search."""

    error_text_preview: str  # First 200 chars of error for display
    error_type: str  # The inferred error type
    severity: ErrorSeverity  # Severity level
    analyzed_at: str  # ISO 8601 timestamp when analyzed
    full_error_text: str  # Full error text for reload
    error_summary: str  # The computed summary
