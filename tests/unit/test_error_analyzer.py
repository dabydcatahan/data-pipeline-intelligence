"""Tests for error analysis (F025)."""

import pytest
from app.insights.error_analyzer import analyze_error
from app.models.error_analysis import ErrorSeverity


def test_analyze_data_type_mismatch():
    """Test detection of data type mismatch error."""
    error_text = "Error: Column 'customer_id' type mismatch. Expected 'Integer' but got 'String'"
    result = analyze_error(error_text)

    assert result.inferred_error_type == "data_type_mismatch"
    assert result.severity == ErrorSeverity.ERROR
    assert "data type" in result.error_summary.lower()
    assert len(result.detected_entities) > 0
    assert "customer_id" in [e.name for e in result.detected_entities]


def test_analyze_missing_column():
    """Test detection of missing column error."""
    error_text = "Error: Column 'email' not found in input stream 'CustomerSource'"
    result = analyze_error(error_text)

    assert result.inferred_error_type == "missing_column"
    assert result.severity == ErrorSeverity.ERROR
    assert len(result.detected_entities) >= 1
    assert any(e.name == "email" for e in result.detected_entities)


def test_analyze_null_constraint():
    """Test detection of NULL constraint violation."""
    error_text = "Constraint violation: NULL value not allowed in column 'purchase_date'"
    result = analyze_error(error_text)

    assert "null" in result.inferred_error_type.lower()
    assert len(result.detected_entities) > 0


def test_analyze_join_error():
    """Test detection of join mismatch error."""
    error_text = (
        "Join condition failed: Left table region_id (varchar) cannot join with "
        "right table region_id (integer). Type mismatch in join keys."
    )
    result = analyze_error(error_text)

    # Should detect as either join or type mismatch (both are valid)
    assert result.inferred_error_type in ("join_mismatch", "data_type_mismatch")
    assert result.severity == ErrorSeverity.ERROR
    assert len(result.lineage_checkpoints) > 0


def test_analyze_division_by_zero():
    """Test detection of division by zero."""
    error_text = "Error: divide by zero in column 'profit_ratio' calculation"
    result = analyze_error(error_text)

    assert "division" in result.inferred_error_type.lower()
    assert len(result.lineage_checkpoints) > 0


def test_lineage_checkpoints_generated():
    """Test that appropriate checkpoints are generated."""
    error_text = "Column 'customer_id' not found"
    result = analyze_error(error_text)

    assert len(result.lineage_checkpoints) > 0

    # Should recommend checking source schema and transformations
    checkpoint_types = [cp.checkpoint_type for cp in result.lineage_checkpoints]
    assert any(t in checkpoint_types for t in ["stream", "transformation", "column"])


def test_empty_error_text():
    """Test handling of empty error text."""
    result = analyze_error("")

    assert result.inferred_error_type == "empty"
    assert result.severity == ErrorSeverity.INFO


def test_unknown_error_type():
    """Test handling of unknown error pattern."""
    error_text = "Something went wrong in the processing pipeline"
    result = analyze_error(error_text)

    # Should still generate checkpoints even for unknown errors
    assert len(result.lineage_checkpoints) > 0


def test_entity_extraction_multiple():
    """Test extraction of multiple entities from error."""
    error_text = (
        "Error: Column 'first_name' and column 'last_name' "
        "in table 'customers' have type incompatibility"
    )
    result = analyze_error(error_text)

    entity_names = [e.name for e in result.detected_entities]
    assert "first_name" in entity_names or "last_name" in entity_names


def test_severity_levels():
    """Test severity classification."""
    critical_error = analyze_error("Column 'id' not found")
    assert critical_error.severity == ErrorSeverity.ERROR

    warning_error = analyze_error("Unexpected NULL value encountered")
    assert warning_error.severity in (ErrorSeverity.WARNING, ErrorSeverity.ERROR)


def test_analyze_path_not_found():
    """Test detection of path/file not found error (storage issue)."""
    error_text = "Operation failed at Sink 'sink1': The specified path does not exist. 404"
    result = analyze_error(error_text)

    assert result.inferred_error_type == "path_not_found"
    assert result.severity == ErrorSeverity.CRITICAL
    assert "path" in result.error_summary.lower()
    assert len(result.lineage_checkpoints) >= 2  # Path check + upstream check


def test_analyze_sink_write_failed():
    """Test detection of sink write failure."""
    error_text = "Job failed due to reason: at Sink 'sink1': Operation failed: cannot write data"
    result = analyze_error(error_text)

    assert result.inferred_error_type == "sink_write_failed"
    assert result.severity == ErrorSeverity.CRITICAL
    assert len(result.lineage_checkpoints) >= 2  # Full path + schema check


def test_analyze_path_access_denied():
    """Test detection of path access denied error."""
    error_text = "Access denied when accessing path /data/storage. Permission denied."
    result = analyze_error(error_text)

    assert result.inferred_error_type == "path_access_denied"
    assert result.severity == ErrorSeverity.CRITICAL
    notes = result.additional_notes.lower()
    assert "credential" in notes or "permission" in notes


def test_real_production_error():
    """Test with real production error (404 path not found at ADLS sink)."""
    real_error = (
        "Job failed due to reason: at Sink 'sink1': Operation failed: "
        '"The specified path does not exist.", 404, HEAD, '
        "https://p08669fdaadlshotaccnp.dfs.core.windows.net/template-reporting-esg/data"
    )
    result = analyze_error(real_error)

    # Should detect as path_not_found
    assert result.inferred_error_type == "path_not_found"
    assert result.severity == ErrorSeverity.CRITICAL
    # Should have at least one checkpoint (could be from bank pattern or built-in)
    assert len(result.lineage_checkpoints) >= 1
    # Should have advice on how to fix
    assert "path" in result.additional_notes.lower() or "storage" in result.additional_notes.lower()


# --------------------------------------------------------------------------------------
# Real-world error text, collected from Microsoft's official troubleshooting docs
# (data-flow-troubleshoot-guide, connector-troubleshoot-guide) and verbatim error strings
# reported on Microsoft Q&A / Stack Overflow. These guard against regressions on the kind
# of error text a user actually pastes in, not just synthetic test strings.
REAL_WORLD_ERRORS = [
    # Copy activity column mapping (Microsoft Q&A / tech-findings.com)
    (
        "ErrorCode=UserErrorInvalidColumnMappingColumnNotFound,'Type=Microsoft.DataTransfer."
        "Common.Shared.HybridDeliveryException,Message=Column 'COUNTDATE' specified in column "
        "mapping cannot be found in source data.,Source=Microsoft.DataTransfer.ClientLibrary,'",
        "missing_column",
    ),
    # Classic SQL Server sink errors (ADF surfaces the underlying engine text verbatim)
    ("Invalid column name 'CustomerID'.", "missing_column"),
    (
        "Violation of PRIMARY KEY constraint 'PK_Customer'. Cannot insert duplicate key in "
        "object 'dbo.Customer'. The duplicate key value is (1002).",
        "duplicate_key_violation",
    ),
    (
        "Cannot insert the value NULL into column 'Email', table 'Staging.dbo.Customer'; "
        "column does not allow nulls. INSERT fails.",
        "null_constraint_violation",
    ),
    (
        "String or binary data would be truncated in table 'dbo.Customer', column "
        "'LastName'. Truncated value: 'Montgomery-Richard'.",
        "column_length_exceeded",
    ),
    (
        "Conversion failed when converting the varchar value 'N/A' to data type int.",
        "type_conversion_error",
    ),
    (
        "The INSERT statement conflicted with the FOREIGN KEY constraint "
        '"FK_Orders_Customer". The conflict occurred in database "Sales", table '
        "\"dbo.Customer\", column 'CustomerID'.",
        "foreign_key_violation",
    ),
    # Data flow executor errors (Microsoft Learn: data-flow-troubleshoot-guide)
    ("Column name used in expression is unavailable or invalid.", "missing_column"),
    ("Converting to a date or time failed due to an invalid character", "type_conversion_error"),
    ("Field in struct does not exist.", "missing_column"),
    (
        "The column in source configuration cannot be found in source data's schema.",
        "missing_column",
    ),
    ("The result has 0 output columns. Ensure at least one column is mapped.", "missing_column"),
    ("Expression cannot be parsed.", "invalid_expression"),
    (
        "Your target table has a column with (n)varchar or (n)varbinary type that has a "
        "smaller column length limitation than real data, please either adjust the column "
        "definition in your target table or change the source data.",
        "column_length_exceeded",
    ),
    (
        "Path does not resolve to any files. Make sure the file/folder exists and is not hidden.",
        "path_not_found",
    ),
    (
        "System is not able to resolve the IP address of the host. Please verify that your "
        "host name is correct or check if your DNS server is able to resolve the host to an "
        "IP address successfully",
        "connection_failed",
    ),
    ("JSON parsing error, unsupported encoding or multiline", "format_error"),
    # Copy activity / connector general (Microsoft Learn: connector-troubleshoot-guide)
    (
        "The underlying connection was closed: An unexpected error occurred on a send.",
        "connection_failed",
    ),
    (
        "The storage connection string is invalid. Format of the initialization string "
        "does not conform to specification.",
        "connection_failed",
    ),
    (
        "Operation on target Copy1 failed: ErrorCode=2200,'Type=Microsoft.DataTransfer."
        "Common.Shared.HybridDeliveryException,Message=Upload file failed at path "
        "customer/2026/data.parquet. Error: Forbidden. This request is not authorized to "
        "perform this operation.",
        "path_access_denied",
    ),
    (
        "Operation on target Lookup1 failed: The Lookup Activity result is empty. Please "
        "make sure the query used returns some data.",
        "lookup_failed",
    ),
    # Throttling / resource exhaustion / service limits
    (
        "There are substantial concurrent MappingDataflow executions that are causing "
        "failures due to throttling under Integration Runtime.",
        "throttling_error",
    ),
    (
        "Cluster ran into out of memory issue during execution, please retry using an "
        "integration runtime with bigger core count and/or memory optimized compute type",
        "resource_exhausted",
    ),
    (
        "ForEach activity failed with error: Total entries to process cannot exceed 100,000.",
        "limit_exceeded",
    ),
    # Reported live via the "Flag this error" workflow (session follow-up)
    ("Table does not exists", "dataset_not_found"),
    ("Invalid object name 'dbo.Customer'.", "dataset_not_found"),
]

# SQL Server / PostgreSQL / Oracle / MySQL / Parquet / Integration Runtime / missing
# ADF components / debug session errors, sourced from Microsoft's official
# troubleshooting docs (self-hosted-integration-runtime-troubleshoot-guide,
# connector-troubleshoot-parquet) plus well-known verbatim engine error text.
REAL_WORLD_ERRORS_DATABASES_AND_INFRA = [
    # SQL Server
    (
        "Login failed for user 'intraconnections'. (Microsoft SQL Server, Error: 18456)",
        "connection_failed",
    ),
    (
        'Cannot open database "Staging" requested by the login. The login failed.',
        "connection_failed",
    ),
    (
        "A network-related or instance-specific error occurred. SqlErrorNumber=53",
        "connection_failed",
    ),
    ("Incorrect syntax near 'FROM'.", "invalid_expression"),
    (
        "Transaction (Process ID 64) was deadlocked on lock resources with another "
        "process and has been chosen as the deadlock victim.",
        "transient_db_error",
    ),
    (
        "Timeout expired. The timeout period elapsed prior to completion of the operation.",
        "transient_db_error",
    ),
    # PostgreSQL
    (
        'ERROR: duplicate key value violates unique constraint "customer_pkey"',
        "duplicate_key_violation",
    ),
    (
        'ERROR: null value in column "email" violates not-null constraint',
        "null_constraint_violation",
    ),
    ('ERROR: relation "customer_staging" does not exist', "dataset_not_found"),
    ('ERROR: column "cust_id" does not exist', "missing_column"),
    ("ERROR: value too long for type character varying(50)", "column_length_exceeded"),
    ('FATAL: password authentication failed for user "etl_user"', "connection_failed"),
    ("could not connect to server: Connection refused", "connection_failed"),
    ("ERROR: permission denied for table customer", "path_access_denied"),
    # Oracle
    ("ORA-00942: table or view does not exist", "dataset_not_found"),
    ("ORA-01017: invalid username/password; logon denied", "connection_failed"),
    # MySQL
    ("Table 'sales.customer_staging' doesn't exist", "dataset_not_found"),
    ("Access denied for user 'etl'@'10.0.0.5' (using password: YES)", "connection_failed"),
    # Parquet
    ("File is not a valid Parquet file.", "format_error"),
    ("Incorrect format of 'N/A' for converting to Int32.", "type_conversion_error"),
    (
        "The data count in a row '12' does not match the column count '10' in given schema.",
        "schema_mismatch",
    ),
    (
        "Message = Arithmetic Overflow., Source = Microsoft.DataTransfer.Common",
        "type_conversion_error",
    ),
    # Integration Runtime
    (
        "The Integration Runtime (Self-hosted) node has encountered an error during "
        "registration. Cannot connect to the Integration Runtime (Self-hosted) Host "
        "Service.",
        "integration_runtime_error",
    ),
    ("Authentication Key is not assigned yet", "integration_runtime_error"),
    ("Self-hosted integration runtime node1 is offline.", "integration_runtime_error"),
    # Missing ADF components
    ("Could not find Dataset 'CustomerStaging'.", "component_not_found"),
    ("Could not find LinkedService 'AzureSqlDb1'.", "component_not_found"),
    # Debug session
    ("Spark cluster not found.", "debug_session_error"),
    ("Debug session has expired. Please restart data flow debug.", "debug_session_error"),
]


@pytest.mark.parametrize("error_text,expected_type", REAL_WORLD_ERRORS)
def test_real_world_error_is_classified(error_text: str, expected_type: str) -> None:
    result = analyze_error(error_text)
    assert result.inferred_error_type == expected_type
    assert result.severity != ErrorSeverity.INFO
    assert len(result.lineage_checkpoints) > 0
    # No entity extraction garbage (e.g. "does"/"with"/"in" mistaken for a column name).
    stopwords = {"does", "with", "in", "is", "has", "or", "the", "a", "not"}
    assert all(e.name.lower() not in stopwords for e in result.detected_entities)


@pytest.mark.parametrize("error_text,expected_type", REAL_WORLD_ERRORS_DATABASES_AND_INFRA)
def test_database_and_infra_error_is_classified(error_text: str, expected_type: str) -> None:
    result = analyze_error(error_text)
    assert result.inferred_error_type == expected_type
    assert result.severity != ErrorSeverity.INFO
    assert len(result.lineage_checkpoints) > 0


def test_new_infra_error_types_get_tailored_not_generic_checkpoints():
    """schema_mismatch/transient_db_error/debug_session_error/integration_runtime_error/
    component_not_found must not silently fall through to the generic 'trace complete
    lineage path' checkpoint - tracing column lineage is irrelevant for e.g. an offline
    Integration Runtime, so a tailored checkpoint is the whole point."""
    generic = "Trace complete lineage path from source to sink"
    cases = {
        "The data count in a row '12' does not match the column count '10' in given schema.",
        "Transaction (Process ID 64) was deadlocked on lock resources.",
        "Spark cluster not found.",
        "The Integration Runtime (Self-hosted) node has encountered an error during registration.",
        "Could not find Dataset 'CustomerStaging'.",
    }
    for error_text in cases:
        result = analyze_error(error_text)
        assert result.lineage_checkpoints[0].description != generic, error_text
