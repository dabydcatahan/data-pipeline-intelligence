"""Error analysis and lineage inspection recommendations (F025)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.lineage.graph import ColumnLineageGraph
from app.lineage.traversal import explain
from app.models.error_analysis import (
    AISuggestion,
    EntityReference,
    ErrorAnalysis,
    ErrorSeverity,
    LineageCheckpoint,
)
from app.models.lineage import ColumnJourney

if TYPE_CHECKING:
    from app.insights.gpt4all_provider import analyze_error_with_ai  # For type hints only

logger = get_logger(__name__)

# Common ADF error patterns and what lineage to check
# NOTE: Order matters! More specific patterns should be checked before generic ones.
_ERROR_PATTERNS = {
    # Common ADF runtime errors (F025 additions) - checked first for specificity
    r"staging blob not accessible": ("staging_blob_not_accessible", ErrorSeverity.CRITICAL),
    r"column length validation error": ("column_length_exceeded", ErrorSeverity.ERROR),
    r"lookup activity failed": ("lookup_failed", ErrorSeverity.ERROR),
    r"(no matching records found).*lookup": ("lookup_failed", ErrorSeverity.ERROR),
    r"(spark operation|spark.*failed)": ("spark_operation_failed", ErrorSeverity.ERROR),
    r"a value error was found.*field": ("value_error", ErrorSeverity.ERROR),
    # Data type mismatches
    r"(type|datatype|data type).*mismatch": ("data_type_mismatch", ErrorSeverity.ERROR),
    r"(cannot convert|invalid cast|type conversion)": (
        "type_conversion_error",
        ErrorSeverity.ERROR,
    ),
    r"(varchar|string|integer|numeric|decimal).*incompatible": (
        "incompatible_type",
        ErrorSeverity.ERROR,
    ),
    # Missing columns
    r"(column|field).*not found": ("missing_column", ErrorSeverity.ERROR),
    r"(does not contain|no such column)": ("missing_column", ErrorSeverity.ERROR),
    r"unknown column": ("missing_column", ErrorSeverity.ERROR),
    # Null/empty issues
    r"(null|empty|blank).*not allowed": ("null_constraint_violation", ErrorSeverity.ERROR),
    r"(null|undefined|missing).*value": ("null_value_encountered", ErrorSeverity.WARNING),
    # Row/data issues
    r"(row|record).*rejected": ("row_rejected", ErrorSeverity.ERROR),
    r"(duplicate|unique|primary key)": ("duplicate_key_violation", ErrorSeverity.ERROR),
    r"(no matching record|join).*failed": ("join_mismatch", ErrorSeverity.ERROR),
    # Transformation/expression errors
    r"(expression|formula).*invalid": ("invalid_expression", ErrorSeverity.ERROR),
    r"(cannot execute|operator.*failed)": ("transformation_failed", ErrorSeverity.ERROR),
    r"divide by zero": ("division_by_zero", ErrorSeverity.ERROR),
    # Encoding/format issues
    r"(encoding|charset|unicode).*invalid": ("encoding_error", ErrorSeverity.ERROR),
    r"(invalid format|malformed)": ("format_error", ErrorSeverity.ERROR),
    # Dataset/connection issues
    r"(dataset|table|view).*not found": ("dataset_not_found", ErrorSeverity.ERROR),
    r"(connection|link).*failed": ("connection_failed", ErrorSeverity.ERROR),
    # File/path/storage issues (generic patterns checked after specific ones)
    r"(path|file|blob|object).*not (found|exist)": ("path_not_found", ErrorSeverity.CRITICAL),
    r"404.*head": ("path_not_found", ErrorSeverity.CRITICAL),
    r"specified path does not exist": ("path_not_found", ErrorSeverity.CRITICAL),
    r"(access denied|permission denied|unauthorized).*path": (
        "path_access_denied",
        ErrorSeverity.CRITICAL,
    ),
    r"(sink|dataset).*operation failed": ("sink_write_failed", ErrorSeverity.CRITICAL),
}

# Entity name patterns (try to extract columns, datasets, etc.)
_ENTITY_PATTERNS = [
    (r"(?:column|field)\s+['\"]?([a-zA-Z_][a-zA-Z0-9_]*)['\"]?", "column"),
    (r"(?:dataset|table|view)\s+['\"]?([a-zA-Z_][a-zA-Z0-9_\.]*)['\"]?", "dataset"),
    (r"(?:stream|transformation)\s+['\"]?([a-zA-Z_][a-zA-Z0-9_]*)['\"]?", "stream"),
    (r"['\"]([a-zA-Z_][a-zA-Z0-9_]*)['\"].*(?:column|field)", "column"),
]


def _extract_entities(error_text: str) -> list[EntityReference]:
    """Extract likely entity names (columns, datasets, streams) from error text."""
    entities: list[EntityReference] = []
    seen: set[str] = set()

    for pattern, entity_type in _ENTITY_PATTERNS:
        for match in re.finditer(pattern, error_text, re.IGNORECASE):
            name = match.group(1)
            if name.lower() not in seen:
                entities.append(EntityReference(entity_type=entity_type, name=name))
                seen.add(name.lower())

    return entities


def _classify_error(error_text: str) -> tuple[str, ErrorSeverity]:
    """Classify the error type based on patterns in the error message."""
    error_lower = error_text.lower()

    for pattern, (error_type, severity) in _ERROR_PATTERNS.items():
        if re.search(pattern, error_lower, re.IGNORECASE):
            return error_type, severity

    return "unknown_error", ErrorSeverity.WARNING


def _generate_checkpoints(
    error_type: str,
    entities: list[EntityReference],
    graph: ColumnLineageGraph | None = None,
) -> list[LineageCheckpoint]:
    """Generate specific lineage checkpoints to inspect based on error type."""
    checkpoints: list[LineageCheckpoint] = []

    # Data type errors: check source and all transformations
    if "type" in error_type or "data_type" in error_type or "incompatible" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="path",
                description="Check complete lineage path from source to sink",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="Data type issues often stem from a transformation or source. "
                "Trace the full path to see where the type changed or was mismatched.",
            )
        )
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="transformation",
                description="Review all transformations (derive, cast, select) affecting these "
                "columns",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="Type mismatches often occur in derived columns or casting operations.",
            )
        )

    # Missing column: check source schema, joins, filters
    elif "missing_column" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="stream",
                description="Verify source columns exist in source schema",
                target_entities=tuple(entities),
                severity=ErrorSeverity.CRITICAL,
                rationale="Missing column error means the column doesn't exist upstream.",
            )
        )
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="transformation",
                description="Check select/derive transformations for column name changes",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="A select or derive might have renamed or dropped the column.",
            )
        )

    # Null constraint: check filter conditions and aggregations
    elif "null" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="column",
                description="Check filter conditions excluding NULL values",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="If a downstream process rejects NULLs, verify if the source allows "
                "them.",
            )
        )
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="transformation",
                description="Review aggregate and join operations for NULL handling",
                target_entities=tuple(entities),
                severity=ErrorSeverity.WARNING,
                rationale="Aggregates and joins can introduce or propagate NULLs.",
            )
        )

    # Join errors: check join conditions and key columns
    elif "join" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="transformation",
                description="Inspect join condition: keys, types, NULL handling",
                target_entities=tuple(entities),
                severity=ErrorSeverity.CRITICAL,
                rationale="Join failures often stem from key type mismatches or missing keys.",
            )
        )
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="column",
                description="Check that join key columns have expected values",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="Verify join keys are populated and the correct type.",
            )
        )

    # Expression/transformation errors
    elif "expression" in error_type or "transformation" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="transformation",
                description="Review transformation expression (derive, aggregate, etc.)",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="Expression errors need debugging at the transformation level.",
            )
        )
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="column",
                description="Check input columns referenced in the expression",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="Expression may reference missing or wrong-type columns.",
            )
        )

    # Duplicate key: check aggregate grouping and source data
    elif "duplicate" in error_type or "unique" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="transformation",
                description="Check aggregate groupBy and distinct operations",
                target_entities=tuple(entities),
                severity=ErrorSeverity.CRITICAL,
                rationale="Duplicate key errors often stem from improper aggregation.",
            )
        )
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="stream",
                description="Verify source data doesn't already contain duplicates",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="If source has duplicates, downstream operations may fail.",
            )
        )

    # Path/file not found: check sink dataset configuration and upstream output
    elif "path_not_found" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="column",
                description="Verify sink dataset path exists and is accessible",
                target_entities=tuple(entities),
                severity=ErrorSeverity.CRITICAL,
                rationale="404 or path not found error means the sink destination doesn't "
                "exist or is unreachable.",
            )
        )
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="path",
                description="Check if upstream transformations are producing output",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="Verify data is actually flowing to the sink from source.",
            )
        )
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="transformation",
                description="Review partition/folder structure in sink configuration",
                target_entities=tuple(entities),
                severity=ErrorSeverity.WARNING,
                rationale="Dynamic paths or partition structures may not be creating expected "
                "directories.",
            )
        )

    # Path access denied: check credentials and permissions
    elif "path_access_denied" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="column",
                description="Verify sink dataset credentials and access permissions",
                target_entities=tuple(entities),
                severity=ErrorSeverity.CRITICAL,
                rationale="Access denied typically means credentials are invalid or user lacks "
                "permissions.",
            )
        )
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="stream",
                description="Check dataset/linked service configuration for auth details",
                target_entities=tuple(entities),
                severity=ErrorSeverity.CRITICAL,
                rationale="Connection string, API key, or authentication method may be incorrect.",
            )
        )

    # Sink write failed: check entire pipeline from source to sink
    elif "sink_write_failed" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="path",
                description="Trace complete lineage from source through all transformations "
                "to sink",
                target_entities=tuple(entities),
                severity=ErrorSeverity.CRITICAL,
                rationale="Sink failures can originate anywhere upstream; need full lineage "
                "context.",
            )
        )
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="column",
                description="Verify sink schema matches the data being written",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="Schema mismatch, data type incompatibility, or missing columns can "
                "cause sink failures.",
            )
        )
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="transformation",
                description="Check any last-stage transformations before sink",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="Errors in the immediate upstream transformation can cascade to "
                "sink failure.",
            )
        )

    # Staging blob not accessible: storage path/credentials issue
    elif "staging_blob_not_accessible" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="column",
                description="Verify sink dataset storage path is correctly configured",
                target_entities=tuple(entities),
                severity=ErrorSeverity.CRITICAL,
                rationale="Staging blob error means ADF cannot access the sink storage "
                "(blob, ADLS, etc.). Check path, container, and folder existence.",
            )
        )
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="stream",
                description="Check linked service credentials for sink dataset",
                target_entities=tuple(entities),
                severity=ErrorSeverity.CRITICAL,
                rationale="Invalid or expired credentials prevent access to storage. "
                "Verify API keys, connection strings, or managed identity permissions.",
            )
        )

    # Column length exceeded: schema validation error
    elif "column_length_exceeded" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="column",
                description="Review sink dataset column length constraints",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="Data exceeds the max length defined in the sink schema. "
                "Check varchar/string column lengths and truncate if needed.",
            )
        )
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="transformation",
                description="Inspect any select/derive transformations that build this column",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="A transformation may be concatenating or calculating a value "
                "that exceeds the target length.",
            )
        )

    # Lookup activity failed: join/lookup mismatch
    elif "lookup_failed" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="transformation",
                description="Inspect lookup/join condition (keys, types, expression)",
                target_entities=tuple(entities),
                severity=ErrorSeverity.CRITICAL,
                rationale="Lookup failures stem from key type mismatches, NULL keys, "
                "or incorrect join conditions.",
            )
        )
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="column",
                description="Verify lookup key columns match in type and value",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="Join keys must have the same data type and be populated. "
                "Check for NULLs and unexpected formats.",
            )
        )
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="stream",
                description="Verify lookup dataset exists and contains expected records",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="If the lookup reference dataset is empty, slow-changing dimension, "
                "or filtered, the join may fail.",
            )
        )

    # Spark operation failed: Spark compute error
    elif "spark_operation_failed" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="transformation",
                description="Review Spark-based transformation logic (SQL, Python UDF, etc.)",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="Spark failures can be from invalid SQL, UDF errors, or memory issues. "
                "Check the transformation expression syntax.",
            )
        )
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="column",
                description="Verify input columns referenced in Spark operation exist and "
                "have correct types",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="Spark SQL may fail if columns are missing or the wrong data type.",
            )
        )

    # Value error: general transformation error
    elif "value_error" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="transformation",
                description="Inspect the transformation where the value error occurred",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="Value errors mean a transformation tried to set an invalid or "
                "incompatible value to a field. Check data type, length, format, or constraints.",
            )
        )
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="column",
                description="Check source data schema and values feeding into this field",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="The error likely originates from source data that doesn't match "
                "the expected schema or constraints.",
            )
        )

    # Default: general lineage inspection
    if not checkpoints:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="path",
                description="Trace complete lineage path from source to sink",
                target_entities=tuple(entities),
                severity=ErrorSeverity.WARNING,
                rationale="For unmapped error types, inspect the full data flow.",
            )
        )

    return checkpoints


def analyze_error(
    error_text: str,
    graph: ColumnLineageGraph | None = None,
    use_ai: bool = False,
) -> ErrorAnalysis:
    """
    Analyze an ADF error message and generate lineage inspection recommendations.

    Args:
        error_text: The ADF error message or output
        graph: Optional ColumnLineageGraph to cross-reference entities
        use_ai: If True, attempt to enhance analysis with AI suggestions (optional).
                Requires GPT4All to be installed and model cached.

    Returns:
        ErrorAnalysis with detected entities, checkpoints, and optional AI suggestions
    """
    if not error_text or not error_text.strip():
        return ErrorAnalysis(
            error_text="",
            error_summary="No error text provided.",
            inferred_error_type="empty",
            severity=ErrorSeverity.INFO,
        )

    # Classify error using built-in rules
    error_type, severity = _classify_error(error_text)

    # Extract entities
    entities = _extract_entities(error_text)

    # Validate entities against graph if available
    if graph and entities:
        validated_entities: list[EntityReference] = []
        for entity in entities:
            if entity.entity_type == "column":
                matches = graph.find(entity.name)
                if matches:
                    validated_entities.append(
                        entity.model_copy(update={"confidence": min(1.0, entity.confidence + 0.5)})
                    )
            else:
                validated_entities.append(entity)
        entities = validated_entities

    # Generate checkpoints
    checkpoints = _generate_checkpoints(error_type, entities, graph)

    # Generate suggested column lineage journeys
    suggested_journeys = _generate_lineage_suggestions(entities, graph)

    # Create summary
    summary = _summarize_error(error_type, entities)
    next_steps = _suggest_next_steps(error_type)

    # Try to get AI suggestions if requested
    ai_suggestions: AISuggestion | None = None
    if use_ai:
        ai_suggestions = _get_ai_suggestions(
            error_text,
            error_type,
            tuple(entities),
            _build_lineage_context(suggested_journeys),
        )

    return ErrorAnalysis(
        error_text=error_text,
        error_summary=summary,
        detected_entities=tuple(entities),
        inferred_error_type=error_type,
        severity=severity,
        lineage_checkpoints=tuple(checkpoints),
        suggested_lineage=suggested_journeys,
        ai_suggestions=ai_suggestions,
        additional_notes=next_steps,
    )


def _generate_lineage_suggestions(
    entities: list[EntityReference],
    graph: ColumnLineageGraph | None = None,
) -> tuple[ColumnJourney, ...]:
    """Generate column lineage journeys for detected entities.

    For each column entity detected in the error, generates a ColumnJourney
    that shows the full lineage path (upstream sources and downstream sinks).

    Args:
        entities: List of detected entities from error message
        graph: Optional ColumnLineageGraph for lineage lookup

    Returns:
        Tuple of ColumnJourney objects for relevant columns
    """
    if not graph or not entities:
        return ()

    journeys: list[ColumnJourney] = []
    for entity in entities:
        if entity.entity_type != "column":
            continue

        # Try to find the column in the graph by matching column name
        matches = graph.find(entity.name)
        for match in matches:
            try:
                journey = explain(graph, match)
                journeys.append(journey)
                # Only include the first match per entity name to avoid duplication
                break
            except (KeyError, ValueError):
                logger.debug(f"Could not generate lineage for column {entity.name}")
                continue

    return tuple(journeys)


def _summarize_error(error_type: str, entities: list[EntityReference]) -> str:
    """Generate a human-readable summary of the error."""
    entity_str = ", ".join(f"`{e.name}`" for e in entities[:3]) if entities else "unknown"
    if len(entities) > 3:
        entity_str += f", and {len(entities) - 3} more"

    summaries = {
        "data_type_mismatch": f"Data type mismatch affecting {entity_str}. "
        "Check if columns have consistent types across transformations.",
        "type_conversion_error": f"Failed to convert type for {entity_str}. "
        "Ensure source data matches expected type.",
        "incompatible_type": f"Type incompatibility: {entity_str} may be wrong type. "
        "Verify type casting in transformations.",
        "missing_column": f"Column(s) {entity_str} not found. "
        "Check if they exist in the source or were dropped by a transformation.",
        "null_constraint_violation": f"NULL value not allowed for {entity_str}. "
        "Verify source data is not null or add handling.",
        "null_value_encountered": f"Unexpected NULL in {entity_str}. "
        "Check upstream data quality and NULL handling.",
        "row_rejected": f"Row(s) rejected involving {entity_str}. "
        "Review constraints and validation rules.",
        "duplicate_key_violation": f"Duplicate key in {entity_str}. "
        "Check aggregation and grouping logic.",
        "join_mismatch": f"Join failed for {entity_str}. Verify join keys and key types match.",
        "invalid_expression": f"Expression error in {entity_str}. "
        "Review the transformation formula or function call.",
        "transformation_failed": f"Transformation failed in {entity_str}. "
        "Check operator configuration and input data.",
        "division_by_zero": f"Division by zero in {entity_str}. "
        "Add NULL check or condition to avoid zero divisor.",
        "encoding_error": f"Encoding issue in {entity_str}. "
        "Verify character encoding is compatible.",
        "format_error": f"Format mismatch in {entity_str}. "
        "Check data format (date, number, etc.) compatibility.",
        "dataset_not_found": f"Dataset {entity_str} not found. "
        "Verify dataset exists and is accessible.",
        "connection_failed": f"Connection issue for {entity_str}. "
        "Check credentials and dataset configuration.",
        "path_not_found": f"File or path {entity_str} not found (404 error). "
        "The sink destination path may not exist or is unreachable.",
        "path_access_denied": f"Access denied for path {entity_str}. "
        "Check credentials and permissions for the storage account.",
        "sink_write_failed": f"Failed to write to sink {entity_str}. "
        "Check data format, schema compatibility, and path accessibility.",
        "unknown_error": f"Unknown error type affecting {entity_str}. "
        "Check the full error message and lineage.",
    }

    return summaries.get(error_type, f"Error affecting {entity_str}. Review lineage and data flow.")


def _suggest_next_steps(error_type: str) -> str:
    """Suggest follow-up steps for resolving the error."""
    suggestions = {
        "data_type_mismatch": "Next: Check source column types vs. sink requirements. "
        "Consider adding explicit type casting in a derive transformation.",
        "missing_column": "Next: Verify the column name in all transformations. "
        "Use the Column Lineage page to trace its origin.",
        "null_constraint_violation": "Next: Either filter NULLs upstream or handle them "
        "in a derive transformation (e.g., coalesce).",
        "duplicate_key_violation": "Next: Ensure proper GROUP BY in aggregate operations. "
        "Verify no duplicate records in source.",
        "join_mismatch": "Next: Check join key types and values. "
        "Consider adding a filter to remove NULLs before the join.",
        "invalid_expression": "Next: Review the expression syntax. "
        "Use simple expressions to isolate the issue.",
        "type_conversion_error": "Next: Check source data format. "
        "Add a cast/conversion step if needed.",
        "path_not_found": "Next: Verify the sink dataset path exists in storage. "
        "Check if ADLS/blob container and folder structure are correct. "
        "Ensure partition folders are created if using dynamic paths.",
        "path_access_denied": "Next: Review storage account credentials and access permissions. "
        "Verify the linked service/connection string is valid and the user has write "
        "permissions.",
        "sink_write_failed": "Next: Start with the source data validation. Trace through each "
        "transformation to verify data types and schema match sink requirements.",
    }

    return suggestions.get(
        error_type, "Next: Use the lineage visualization to trace the error path."
    )


def _build_lineage_context(
    suggested_journeys: tuple[ColumnJourney, ...],
) -> dict[str, object]:
    """Build lineage context from suggested column journeys for AI analysis.

    Args:
        suggested_journeys: Tuple of ColumnJourney objects from lineage analysis

    Returns:
        Dictionary with lineage metadata for AI prompt
    """
    if not suggested_journeys:
        return {}

    context: dict[str, object] = {
        "journey_count": len(suggested_journeys),
        "journeys_summary": [],
    }

    for journey in suggested_journeys[:3]:  # Limit to first 3 journeys for context size
        journey_info = {
            "column": journey.column_id,
            "origin_stream": journey.origin_stream,
            "final_stream": journey.final_stream,
            "upstream_steps": len(journey.upstream_steps),
            "transformations_count": len(journey.transformations),
        }
        context["journeys_summary"].append(journey_info)  # type: ignore

    return context


def _get_ai_suggestions(
    error_text: str,
    error_type: str,
    entities: tuple[EntityReference, ...],
    lineage_context: dict[str, object],
) -> AISuggestion | None:
    """Get AI-powered suggestions for error analysis.

    This function attempts to call the GPT4All provider for enhanced analysis.
    If the model is not available or analysis fails, returns None with a warning.

    Args:
        error_text: Original error message
        error_type: Detected error type from classification
        entities: Extracted entity references
        lineage_context: Lineage metadata from column journeys

    Returns:
        AISuggestion if successful, None if AI is unavailable or fails
    """
    try:
        # Import at function level so tests can patch it
        from app.insights.gpt4all_provider import analyze_error_with_ai
        from app.core.exceptions import AIAnalysisError

        logger.debug(f"Attempting AI analysis for error type: {error_type}")
        ai_suggestions = analyze_error_with_ai(
            error_text=error_text,
            error_type=error_type,
            entities=entities,
            lineage_context=lineage_context if lineage_context else None,
        )
        logger.debug(
            f"AI analysis successful, confidence: {ai_suggestions.confidence:.2f}"
        )
        return ai_suggestions

    except ImportError:
        logger.debug("GPT4All not installed; AI analysis skipped")
        return None
    except Exception as e:
        logger.warning(f"AI analysis failed, using deterministic only: {e}")
        return None
