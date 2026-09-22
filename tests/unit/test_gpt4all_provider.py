"""Unit tests for GPT4All Phi-2 provider."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.core.exceptions import AIAnalysisError
from app.insights.gpt4all_provider import (
    GPT4AllProvider,
    analyze_error_with_ai,
    get_provider,
)
from app.models.error_analysis import AISuggestion, EntityReference


class TestGPT4AllProvider:
    """Tests for GPT4AllProvider class."""

    @pytest.fixture
    def provider(self) -> GPT4AllProvider:
        """Create a fresh provider instance."""
        return GPT4AllProvider()

    @pytest.fixture
    def sample_entities(self) -> tuple[EntityReference, ...]:
        """Create sample entity references."""
        return (
            EntityReference(entity_type="column", name="user_id", stream="raw_users"),
            EntityReference(
                entity_type="column", name="email", stream="raw_users", confidence=0.95
            ),
        )

    @pytest.fixture
    def sample_error_text(self) -> str:
        """Create a sample ADF error message."""
        return (
            "Error: Column 'user_id' has data type mismatch between source (INT) "
            "and sink (STRING). Data flow failed at transformation step."
        )

    def test_provider_initialization(self, provider: GPT4AllProvider) -> None:
        """Test provider initializes without model loaded."""
        assert provider._model is None
        assert provider.MODEL_NAME == "phi-2"
        assert provider.TEMPERATURE == 0.7

    def test_build_context_with_entities(
        self,
        provider: GPT4AllProvider,
        sample_error_text: str,
        sample_entities: tuple[EntityReference, ...],
    ) -> None:
        """Test context building with entity references."""
        context = provider._build_context(
            sample_error_text, "data_type_mismatch", sample_entities, None
        )

        assert context["error_message"] == sample_error_text
        assert context["error_type"] == "data_type_mismatch"
        assert len(context["detected_entities"]) == 2
        assert context["detected_entities"][0]["name"] == "user_id"
        assert context["detected_entities"][1]["confidence"] == 0.95

    def test_build_context_with_lineage(
        self,
        provider: GPT4AllProvider,
        sample_error_text: str,
        sample_entities: tuple[EntityReference, ...],
    ) -> None:
        """Test context includes lineage data when provided."""
        lineage_context = {
            "upstream_steps": ["raw_users", "transform_1", "sink"],
            "transformations": ["SELECT", "JOIN"],
        }
        context = provider._build_context(
            sample_error_text, "data_type_mismatch", sample_entities, lineage_context
        )

        assert "lineage_hints" in context
        assert context["lineage_hints"]["upstream_steps"][0] == "raw_users"

    def test_create_prompt_format(
        self,
        provider: GPT4AllProvider,
    ) -> None:
        """Test prompt includes required fields and JSON instruction."""
        context = {
            "error_message": "Test error",
            "error_type": "test_type",
            "detected_entities": [],
        }
        prompt = provider._create_prompt(context)

        assert "You are an Azure Data Factory expert" in prompt
        assert "Test error" in prompt
        assert '"root_cause"' in prompt
        assert '"fixes"' in prompt
        assert '"prevention"' in prompt
        assert "JSON" in prompt

    def test_parse_response_valid_json(
        self, provider: GPT4AllProvider
    ) -> None:
        """Test parsing valid JSON response."""
        response = json.dumps(
            {
                "root_cause": "Type mismatch between source and sink columns.",
                "diagnosis": "The source data type INT does not match the sink type STRING. "
                "The transform does not perform proper casting.",
                "fixes": [
                    "Add a CAST or CONVERT function in the transform to convert INT to STRING.",
                    "Verify sink schema matches source data types.",
                    "Add error handling to skip invalid rows.",
                ],
                "prevention": [
                    "Always validate schema alignment before pipeline execution.",
                    "Use data profiling to detect type mismatches early.",
                ],
                "confidence": 0.92,
            }
        )

        suggestion = provider._parse_response(response)

        assert suggestion.root_cause == "Type mismatch between source and sink columns."
        assert suggestion.confidence == 0.92
        assert len(suggestion.fixes) == 3
        assert len(suggestion.prevention) == 2
        assert isinstance(suggestion, AISuggestion)

    def test_parse_response_with_surrounding_text(
        self, provider: GPT4AllProvider
    ) -> None:
        """Test parsing JSON response embedded in surrounding text."""
        response = (
            "Here is the analysis:\n"
            + json.dumps(
                {
                    "root_cause": "Root cause here",
                    "diagnosis": "Diagnosis here",
                    "fixes": ["Fix 1"],
                    "prevention": ["Prevention 1"],
                    "confidence": 0.85,
                }
            )
            + "\n\nEnd of response."
        )

        suggestion = provider._parse_response(response)
        assert suggestion.root_cause == "Root cause here"
        assert suggestion.confidence == 0.85

    def test_parse_response_missing_fields(
        self, provider: GPT4AllProvider
    ) -> None:
        """Test parsing response with missing optional fields."""
        response = json.dumps(
            {
                "root_cause": "Something happened",
                # Missing diagnosis, fixes, prevention
            }
        )

        suggestion = provider._parse_response(response)
        assert suggestion.root_cause == "Something happened"
        assert suggestion.diagnosis == "Unable to determine issue."
        assert len(suggestion.fixes) == 0
        assert len(suggestion.prevention) == 0

    def test_parse_response_invalid_json(
        self, provider: GPT4AllProvider
    ) -> None:
        """Test parsing invalid JSON raises error."""
        response = "This is not JSON at all"

        with pytest.raises(AIAnalysisError, match="No JSON object found"):
            provider._parse_response(response)

    def test_parse_response_malformed_json(
        self, provider: GPT4AllProvider
    ) -> None:
        """Test parsing malformed JSON raises error."""
        response = '{"root_cause": "incomplete", "fixes":'

        with pytest.raises(AIAnalysisError, match="No JSON object found"):
            provider._parse_response(response)

    @patch("app.insights.gpt4all_provider.GPT4All")
    def test_load_model_success(self, mock_gpt4all_class: MagicMock) -> None:
        """Test successful model loading."""
        mock_model = MagicMock()
        mock_gpt4all_class.return_value = mock_model

        provider = GPT4AllProvider()
        model = provider._load_model()

        assert model is mock_model
        mock_gpt4all_class.assert_called_once_with("phi-2")

    def test_load_model_not_installed(self) -> None:
        """Test model loading fails when GPT4All is not installed."""
        # Mock GPT4All as None to simulate import failure
        with patch("app.insights.gpt4all_provider.GPT4All", None):
            provider = GPT4AllProvider()
            with pytest.raises(AIAnalysisError, match="GPT4All not installed"):
                provider._load_model()

    @patch("app.insights.gpt4all_provider.GPT4All")
    def test_load_model_runtime_error(self, mock_gpt4all_class: MagicMock) -> None:
        """Test model loading fails with runtime error."""
        mock_gpt4all_class.side_effect = RuntimeError("Failed")
        provider = GPT4AllProvider()

        with pytest.raises(AIAnalysisError, match="Failed to load model"):
            provider._load_model()

    @patch("app.insights.gpt4all_provider.GPT4All")
    def test_analyze_error_success(
        self,
        mock_gpt4all_class: MagicMock,
        sample_error_text: str,
        sample_entities: tuple[EntityReference, ...],
    ) -> None:
        """Test successful error analysis."""
        mock_model = MagicMock()
        ai_response = json.dumps(
            {
                "root_cause": "Type mismatch",
                "diagnosis": "Columns don't match",
                "fixes": ["Fix 1", "Fix 2"],
                "prevention": ["Prevention 1"],
                "confidence": 0.88,
            }
        )
        mock_model.generate.return_value = ai_response
        mock_gpt4all_class.return_value = mock_model

        provider = GPT4AllProvider()
        suggestion = provider.analyze_error(
            sample_error_text, "data_type_mismatch", sample_entities
        )

        assert isinstance(suggestion, AISuggestion)
        assert suggestion.root_cause == "Type mismatch"
        assert suggestion.confidence == 0.88
        mock_model.generate.assert_called_once()

    @patch("app.insights.gpt4all_provider.GPT4All")
    def test_analyze_error_with_lineage_context(
        self,
        mock_gpt4all_class: MagicMock,
        sample_error_text: str,
        sample_entities: tuple[EntityReference, ...],
    ) -> None:
        """Test error analysis includes lineage context in prompt."""
        mock_model = MagicMock()
        mock_model.generate.return_value = json.dumps(
            {
                "root_cause": "Issue",
                "diagnosis": "Problem",
                "fixes": [],
                "prevention": [],
            }
        )
        mock_gpt4all_class.return_value = mock_model

        lineage_context = {"upstream": ["source"], "downstream": ["sink"]}

        provider = GPT4AllProvider()
        provider.analyze_error(
            sample_error_text,
            "test_error",
            sample_entities,
            lineage_context,
        )

        # Verify generate was called with prompt containing lineage
        call_args = mock_model.generate.call_args
        prompt = call_args[0][0]
        assert "upstream" in prompt or "lineage_hints" in prompt

    @patch("app.insights.gpt4all_provider.GPT4All")
    def test_analyze_error_reuses_loaded_model(
        self,
        mock_gpt4all_class: MagicMock,
        sample_error_text: str,
        sample_entities: tuple[EntityReference, ...],
    ) -> None:
        """Test that model is loaded once and reused."""
        mock_model = MagicMock()
        mock_model.generate.return_value = json.dumps(
            {
                "root_cause": "Test",
                "diagnosis": "Test",
                "fixes": [],
                "prevention": [],
            }
        )
        mock_gpt4all_class.return_value = mock_model

        provider = GPT4AllProvider()

        # First call loads model
        provider.analyze_error(sample_error_text, "error1", sample_entities)
        assert mock_gpt4all_class.call_count == 1

        # Second call reuses model
        provider.analyze_error(sample_error_text, "error2", sample_entities)
        assert mock_gpt4all_class.call_count == 1  # Still 1, not 2

    @patch("app.insights.gpt4all_provider.GPT4All")
    def test_analyze_error_json_parse_error(
        self,
        mock_gpt4all_class: MagicMock,
        sample_error_text: str,
        sample_entities: tuple[EntityReference, ...],
    ) -> None:
        """Test error handling when response cannot be parsed."""
        mock_model = MagicMock()
        mock_model.generate.return_value = "Invalid response, no JSON"
        mock_gpt4all_class.return_value = mock_model

        provider = GPT4AllProvider()

        with pytest.raises(AIAnalysisError):
            provider.analyze_error(sample_error_text, "test_error", sample_entities)

    @patch("app.insights.gpt4all_provider.GPT4All")
    def test_analyze_error_model_error(
        self,
        mock_gpt4all_class: MagicMock,
        sample_error_text: str,
        sample_entities: tuple[EntityReference, ...],
    ) -> None:
        """Test error handling when model.generate fails."""
        mock_model = MagicMock()
        mock_model.generate.side_effect = RuntimeError("GPU out of memory")
        mock_gpt4all_class.return_value = mock_model

        provider = GPT4AllProvider()

        with pytest.raises(AIAnalysisError, match="AI analysis failed"):
            provider.analyze_error(sample_error_text, "test_error", sample_entities)


class TestGPT4AllModuleFunctions:
    """Tests for module-level convenience functions."""

    @patch("app.insights.gpt4all_provider.GPT4AllProvider.analyze_error")
    def test_get_provider_singleton(
        self, mock_analyze: MagicMock
    ) -> None:
        """Test get_provider returns singleton."""
        provider1 = get_provider()
        provider2 = get_provider()

        assert provider1 is provider2

    @patch("app.insights.gpt4all_provider.GPT4All")
    def test_analyze_error_with_ai_convenience(
        self, mock_gpt4all_class: MagicMock
    ) -> None:
        """Test convenience function delegates to provider."""
        mock_model = MagicMock()
        mock_model.generate.return_value = json.dumps(
            {
                "root_cause": "Test",
                "diagnosis": "Test",
                "fixes": ["Fix"],
                "prevention": ["Prevent"],
                "confidence": 0.9,
            }
        )
        mock_gpt4all_class.return_value = mock_model

        entities = (EntityReference(entity_type="column", name="test"),)
        suggestion = analyze_error_with_ai("Error text", "error_type", entities)

        assert isinstance(suggestion, AISuggestion)
        assert suggestion.confidence == 0.9
