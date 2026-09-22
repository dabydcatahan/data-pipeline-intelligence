"""Error analysis and lineage inspection recommendations (F025).

The regex -> error_type -> severity classification table, and the per-error-type
summary / next-steps text, are data-driven from ``error_patterns.json`` next to this
file - not hardcoded here - so a new error pattern can be added by editing that file,
with no Python change or redeploy. See its ``_readme`` field for the schema and
ordering rules. Checkpoint generation (:func:`_generate_checkpoints`) stays in Python
since it's branching logic, not flat data; an error_type with no matching branch there
still gets a safe generic checkpoint.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.logging import get_logger
from app.lineage.graph import ColumnLineageGraph
from app.lineage.traversal import explain
from app.models.error_analysis import (
    EntityReference,
    ErrorAnalysis,
    ErrorSeverity,
    LineageCheckpoint,
)
from app.models.lineage import ColumnJourney

logger = get_logger(__name__)

_PATTERNS_FILE = Path(__file__).with_name("error_patterns.json")


def _load_patterns_file() -> tuple[
    tuple[tuple[re.Pattern[str], str, ErrorSeverity], ...],
    dict[str, str],
    dict[str, str],
]:
    """Load and compile the classification table from ``error_patterns.json``.

    Order is preserved from the JSON array (first regex match wins, see
    :func:`_classify_error`), matching the file's own specific-before-generic rule.
    """
    data = json.loads(_PATTERNS_FILE.read_text(encoding="utf-8"))
    patterns = tuple(
        (re.compile(p["regex"], re.IGNORECASE), p["error_type"], ErrorSeverity(p["severity"]))
        for p in data["patterns"]
    )
    return patterns, dict(data["summaries"]), dict(data["next_steps"])


_PATTERNS, _SUMMARY_TEMPLATES, _NEXT_STEPS = _load_patterns_file()

# Entity name patterns (try to extract columns, datasets, etc.). Quotes are required
# around the captured name: without them, common English words following "column"/
# "table" (e.g. "column does not allow nulls") would be misdetected as entity names.
_ENTITY_PATTERNS = [
    (r"(?:column|field)\s+['\"]([a-zA-Z_][a-zA-Z0-9_]*)['\"]", "column"),
    (r"(?:dataset|table|view)\s+['\"]([a-zA-Z_][a-zA-Z0-9_.]*)['\"]", "dataset"),
    (r"(?:stream|transformation)\s+['\"]([a-zA-Z_][a-zA-Z0-9_]*)['\"]", "stream"),
    (r"constraint\s+['\"]([a-zA-Z_][a-zA-Z0-9_]*)['\"]", "constraint"),
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
    for pattern, error_type, severity in _PATTERNS:
        if pattern.search(error_text):
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

    # Foreign key violation: a referenced parent row is missing at the sink
    elif "foreign_key" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="column",
                description="Verify the referenced parent table already contains the key value",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="A foreign key violation means the sink table's parent/reference "
                "table doesn't yet have a matching row for this key.",
            )
        )
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="path",
                description="Check pipeline activity order: parent tables must load before "
                "child tables that reference them",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="If parent and child loads run out of order (or in parallel without "
                "a dependency), the child insert can race ahead of the parent.",
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

    # Throttling: concurrent runs hitting an integration runtime / service limit
    elif "throttling" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="path",
                description="Review how many pipelines/data flows run concurrently on the "
                "same integration runtime",
                target_entities=tuple(entities),
                severity=ErrorSeverity.WARNING,
                rationale="Throttling isn't a data or lineage bug - it means too many "
                "concurrent executions are competing for the same compute.",
            )
        )

    # Resource exhaustion: cluster ran out of memory/disk during execution
    elif "resource_exhausted" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="transformation",
                description="Review transformations most likely to skew partition size "
                "(joins, aggregates, broadcasts, wide unions)",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="Out-of-memory errors are usually caused by data skew or an "
                "undersized cluster, not by the lineage/mapping itself.",
            )
        )

    # Limit exceeded: activity/loop hit a hard ADF service limit
    elif "limit_exceeded" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="path",
                description="Check the activity's input size against ADF's documented limits "
                "(e.g. ForEach item count, parameter/array size)",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="This is an ADF service limit (documented maximum), not a data "
                "quality or lineage issue - it needs a pipeline redesign (e.g. batching).",
            )
        )

    # Schema mismatch: source/sink shapes don't line up (column count, names, order)
    elif "schema_mismatch" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="column",
                description="Compare source and sink schemas: column count, names, order and types",
                target_entities=tuple(entities),
                severity=ErrorSeverity.ERROR,
                rationale="Schema mismatches are structural, not single-column - the whole "
                "shape of the source and sink need to line up.",
            )
        )

    # Transient DB error: deadlock/timeout/dropped connection - retry, not a data bug
    elif "transient_db_error" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="path",
                description="Retry the activity; if it recurs, check for other jobs holding "
                "long locks on the same table",
                target_entities=tuple(entities),
                severity=ErrorSeverity.WARNING,
                rationale="Deadlocks, timeouts and dropped connections are usually transient "
                "database/network conditions, not lineage or mapping problems.",
            )
        )

    # Debug session error: the Data Flow debug cluster is unavailable
    elif "debug_session_error" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="path",
                description="Restart the Data Flow debug session",
                target_entities=tuple(entities),
                severity=ErrorSeverity.WARNING,
                rationale="Debug clusters expire (default 60 min idle) or get recycled under "
                "parallel ForEach load - this is unrelated to lineage or mapping.",
            )
        )

    # Integration Runtime error: the IR itself is the problem, not the data
    elif "integration_runtime_error" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="path",
                description="Check the Integration Runtime's status, version and host "
                "connectivity/dependencies",
                target_entities=tuple(entities),
                severity=ErrorSeverity.CRITICAL,
                rationale="Offline nodes, missing Java, or a stale self-hosted IR version are "
                "infrastructure problems - lineage tracing won't help here.",
            )
        )

    # Component not found: a broken JSON reference, not a runtime data issue
    elif "component_not_found" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="path",
                description="Verify the referenced dataset/linked service/pipeline/trigger "
                "name exists and is published",
                target_entities=tuple(entities),
                severity=ErrorSeverity.CRITICAL,
                rationale="This is a broken reference in the pipeline/data flow JSON itself "
                "(often from an incomplete Git merge), not a data or lineage problem.",
            )
        )

    # Business validation error: a custom data-quality rule fired, not a technical fault
    elif "business_validation_error" in error_type:
        checkpoints.append(
            LineageCheckpoint(
                checkpoint_type="path",
                description="Review the source data volume/values for the run in question "
                "against the business rule's expected range",
                target_entities=tuple(entities),
                severity=ErrorSeverity.WARNING,
                rationale="This is a custom business-rule threshold breach (e.g. an outlier "
                "or row-count check), not a pipeline bug - the pipeline itself ran "
                "correctly, so lineage tracing won't explain it.",
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
) -> ErrorAnalysis:
    """
    Analyze an ADF error message and generate lineage inspection recommendations.

    Args:
        error_text: The ADF error message or output
        graph: Optional ColumnLineageGraph to cross-reference entities

    Returns:
        ErrorAnalysis with detected entities and checkpoints
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

    return ErrorAnalysis(
        error_text=error_text,
        error_summary=summary,
        detected_entities=tuple(entities),
        inferred_error_type=error_type,
        severity=severity,
        lineage_checkpoints=tuple(checkpoints),
        suggested_lineage=suggested_journeys,
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

    template = _SUMMARY_TEMPLATES.get(
        error_type, "Error affecting {entities}. Review lineage and data flow."
    )
    return template.format(entities=entity_str)


def _suggest_next_steps(error_type: str) -> str:
    """Suggest follow-up steps for resolving the error."""
    return _NEXT_STEPS.get(
        error_type, "Next: Use the lineage visualization to trace the error path."
    )
