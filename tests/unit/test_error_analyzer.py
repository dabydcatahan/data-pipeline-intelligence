"""Tests for error analysis (F025)."""

from unittest.mock import patch, MagicMock

import pytest

from app.insights.error_analyzer import analyze_error
from app.models.error_analysis import ErrorSeverity, AISuggestion


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
    error_text = "Join condition failed: Left table region_id (varchar) cannot join with " \
                 "right table region_id (integer). Type mismatch in join keys."
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
    assert "credential" in result.additional_notes.lower() or "permission" in result.additional_notes.lower()


def test_real_production_error():
    """Test with real production error (404 path not found at ADLS sink)."""
    real_error = (
        "Job failed due to reason: at Sink 'sink1': Operation failed: "
        '"The specified path does not exist.", 404, HEAD, '
        'https://p08669fdaadlshotaccnp.dfs.core.windows.net/template-reporting-esg/data'
    )
    result = analyze_error(real_error)

    # Should detect as path_not_found
    assert result.inferred_error_type == "path_not_found"
    assert result.severity == ErrorSeverity.CRITICAL
    # Should have at least one checkpoint (could be from bank pattern or built-in)
    assert len(result.lineage_checkpoints) >= 1
    # Should have advice on how to fix
    assert "path" in result.additional_notes.lower() or "storage" in result.additional_notes.lower()


class TestAIIntegration:
    """Tests for AI-powered error analysis feature."""

    def test_analyze_error_without_ai_by_default(self):
        """Test that AI is not called by default."""
        error_text = "Column 'customer_id' type mismatch"
        result = analyze_error(error_text)

        # Should have deterministic analysis
        assert result.inferred_error_type == "data_type_mismatch"
        # Should NOT have AI suggestions by default
        assert result.ai_suggestions is None

    def test_analyze_error_use_ai_false_explicitly(self):
        """Test that AI is skipped when use_ai=False."""
        error_text = "Column 'order_id' not found"
        result = analyze_error(error_text, use_ai=False)

        assert result.inferred_error_type == "missing_column"
        assert result.ai_suggestions is None

    @patch("app.insights.gpt4all_provider.analyze_error_with_ai")
    def test_analyze_error_with_ai_enabled(self, mock_ai: MagicMock) -> None:
        """Test that AI is called when use_ai=True."""
        mock_ai.return_value = AISuggestion(
            root_cause="Type mismatch between columns",
            diagnosis="The source uses INT but sink expects STRING",
            fixes=("Add CAST to STRING in derived column",),
            prevention=("Always validate schema before join",),
            confidence=0.85,
        )

        error_text = "Column 'customer_id' type mismatch"
        result = analyze_error(error_text, use_ai=True)

        # Should have deterministic analysis
        assert result.inferred_error_type == "data_type_mismatch"
        # Should have AI suggestions
        assert result.ai_suggestions is not None
        assert result.ai_suggestions.root_cause == "Type mismatch between columns"
        assert result.ai_suggestions.confidence == 0.85

    @patch("app.insights.gpt4all_provider.analyze_error_with_ai")
    def test_analyze_error_with_ai_graceful_fallback(self, mock_ai: MagicMock) -> None:
        """Test graceful fallback if AI analysis fails."""
        mock_ai.side_effect = Exception("Model not available")

        error_text = "Column 'email' not found"
        result = analyze_error(error_text, use_ai=True)

        # Should still have deterministic analysis even if AI fails
        assert result.inferred_error_type == "missing_column"
        # AI suggestions should be None due to error
        assert result.ai_suggestions is None
        # Deterministic checkpoints should still be present
        assert len(result.lineage_checkpoints) > 0

    @patch("app.insights.gpt4all_provider.analyze_error_with_ai")
    def test_analyze_error_ai_called_with_correct_params(self, mock_ai: MagicMock) -> None:
        """Test that AI is called with correct parameters."""
        mock_ai.return_value = AISuggestion(
            root_cause="Test", diagnosis="Test", fixes=(), prevention=(), confidence=0.5
        )

        error_text = "Duplicate key in 'user_id'"
        result = analyze_error(error_text, use_ai=True)

        # Verify AI was called
        assert mock_ai.called
        call_args = mock_ai.call_args

        # Check parameters
        assert call_args.kwargs["error_text"] == error_text
        assert call_args.kwargs["error_type"] == "duplicate_key_violation"
        assert "entities" in call_args.kwargs
        assert isinstance(call_args.kwargs["entities"], tuple)

    def test_analyze_error_deterministic_only_with_import_error(self) -> None:
        """Test that analyze_error works even if gpt4all is not installed."""
        error_text = "Column mismatch in 'revenue'"
        # This should work even though GPT4All may not be installed
        result = analyze_error(error_text, use_ai=True)

        # Should still get deterministic analysis
        assert result.inferred_error_type is not None
        assert len(result.lineage_checkpoints) > 0

