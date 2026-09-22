"""GPT4All Phi-2 provider for AI-powered error analysis."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.exceptions import AIAnalysisError
from app.models.error_analysis import AISuggestion, EntityReference

logger = logging.getLogger(__name__)

# Import GPT4All at module level so it can be mocked in tests
try:
    from gpt4all import GPT4All
except ImportError:
    GPT4All = None  # Will be caught in _load_model()


class GPT4AllProvider:
    """Wraps GPT4All Phi-2 model for structured error analysis."""

    MODEL_NAME = "phi-2"
    """Model identifier for GPT4All (auto-selects Phi-2 2.7B)."""

    MAX_TOKENS = 1024
    """Maximum tokens to generate in response."""

    TEMPERATURE = 0.7
    """Sampling temperature for response generation (0.7 = balanced creativity)."""

    def __init__(self) -> None:
        """Initialize provider (doesn't load model yet)."""
        self._model: Any = None

    def _load_model(self) -> Any:
        """Load Phi-2 model from cache or download if needed.

        Returns:
            GPT4All model instance.

        Raises:
            AIAnalysisError: If model cannot be loaded or downloaded.
        """
        if GPT4All is None:
            raise AIAnalysisError(
                "GPT4All not installed. Install with: pip install gpt4all"
            )

        try:
            logger.debug(f"Loading model {self.MODEL_NAME}...")
            model = GPT4All(self.MODEL_NAME)
            logger.debug(f"Model {self.MODEL_NAME} loaded successfully")
            return model
        except Exception as e:
            raise AIAnalysisError(f"Failed to load model {self.MODEL_NAME}: {e}") from e

    def analyze_error(
        self,
        error_text: str,
        error_type: str,
        entities: tuple[EntityReference, ...],
        lineage_context: dict[str, Any] | None = None,
    ) -> AISuggestion:
        """Analyze an ADF error using Phi-2 and return structured suggestions.

        Args:
            error_text: Original ADF error message.
            error_type: Detected error type (e.g., "data_type_mismatch").
            entities: Extracted EntityReference objects from error.
            lineage_context: Optional context from column lineage analysis.

        Returns:
            AISuggestion with root_cause, diagnosis, fixes, prevention.

        Raises:
            AIAnalysisError: If model analysis fails.
        """
        if self._model is None:
            self._model = self._load_model()

        try:
            # Build structured context for the model
            context = self._build_context(
                error_text, error_type, entities, lineage_context
            )

            # Create prompt for Phi-2
            prompt = self._create_prompt(context)

            # Generate response from model
            logger.debug("Generating AI analysis...")
            response_text = self._model.generate(
                prompt,
                max_tokens=self.MAX_TOKENS,
                temp=self.TEMPERATURE,
            )

            # Parse response into AISuggestion
            suggestion = self._parse_response(response_text)
            logger.debug(f"AI analysis complete: {suggestion.root_cause[:50]}...")
            return suggestion

        except AIAnalysisError:
            # Re-raise AIAnalysisError (already wrapped)
            raise
        except Exception as e:
            raise AIAnalysisError(f"AI analysis failed: {e}") from e

    def _build_context(
        self,
        error_text: str,
        error_type: str,
        entities: tuple[EntityReference, ...],
        lineage_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build rich context for the model prompt.

        Args:
            error_text: Original error message.
            error_type: Detected error type.
            entities: Extracted entities.
            lineage_context: Optional lineage data.

        Returns:
            Dictionary with structured context.
        """
        context: dict[str, Any] = {
            "error_message": error_text,
            "error_type": error_type,
            "detected_entities": [
                {
                    "type": e.entity_type,
                    "name": e.name,
                    "stream": e.stream,
                    "confidence": e.confidence,
                }
                for e in entities
            ],
        }

        if lineage_context:
            context["lineage_hints"] = lineage_context

        return context

    def _create_prompt(self, context: dict[str, Any]) -> str:
        """Create a prompt for Phi-2 to analyze the error.

        Args:
            context: Structured context from _build_context.

        Returns:
            Prompt string for the model.
        """
        context_json = json.dumps(context, indent=2)

        prompt = f"""You are an Azure Data Factory expert. A user encountered this error in their ADF pipeline:

Error Details:
{context_json}

Analyze this error and provide a JSON response with exactly these fields:
1. "root_cause": One sentence explaining why this error occurred.
2. "diagnosis": 2-3 sentences describing what is broken in the data flow.
3. "fixes": A JSON array of 3-5 specific, actionable steps to fix this issue.
4. "prevention": A JSON array of 2-3 ways to prevent similar errors in the future.
5. "confidence": A number from 0.0 to 1.0 indicating how confident you are in this analysis.

Respond ONLY with a valid JSON object. Focus on ADF-specific solutions."""

        return prompt

    def _parse_response(self, response_text: str) -> AISuggestion:
        """Parse model response into AISuggestion.

        Args:
            response_text: Raw response from GPT4All model.

        Returns:
            AISuggestion with parsed fields.

        Raises:
            AIAnalysisError: If response cannot be parsed.
        """
        # Extract JSON from response (model might include extra text)
        try:
            # Try to find JSON object in response
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start == -1 or end == 0:
                raise ValueError("No JSON object found in response")

            json_text = response_text[start:end]
            data = json.loads(json_text)
        except ValueError as e:
            raise AIAnalysisError(f"No JSON object found in AI response: {e}") from e
        except json.JSONDecodeError as e:
            raise AIAnalysisError(f"Invalid JSON in AI response: {e}") from e

        # Extract fields with defaults
        root_cause = data.get("root_cause", "Analysis incomplete.")
        diagnosis = data.get("diagnosis", "Unable to determine issue.")
        fixes = data.get("fixes", [])
        prevention = data.get("prevention", [])
        confidence = data.get("confidence", 0.5)

        # Normalize to tuples of strings
        if not isinstance(fixes, (list, tuple)):
            fixes = [str(fixes)]
        else:
            fixes = [str(f) for f in fixes]

        if not isinstance(prevention, (list, tuple)):
            prevention = [str(prevention)]
        else:
            prevention = [str(p) for p in prevention]

        return AISuggestion(
            root_cause=str(root_cause),
            diagnosis=str(diagnosis),
            fixes=tuple(fixes),
            prevention=tuple(prevention),
            confidence=float(confidence),
        )


# Singleton instance
_provider: GPT4AllProvider | None = None


def get_provider() -> GPT4AllProvider:
    """Get or create the GPT4All provider singleton.

    Returns:
        GPT4AllProvider instance.
    """
    global _provider
    if _provider is None:
        _provider = GPT4AllProvider()
    return _provider


def analyze_error_with_ai(
    error_text: str,
    error_type: str,
    entities: tuple[EntityReference, ...],
    lineage_context: dict[str, Any] | None = None,
) -> AISuggestion:
    """Convenience function to analyze an error using AI.

    Args:
        error_text: Original error message.
        error_type: Detected error type.
        entities: Extracted entities.
        lineage_context: Optional lineage context.

    Returns:
        AISuggestion with analysis results.

    Raises:
        AIAnalysisError: If analysis fails.
    """
    provider = get_provider()
    return provider.analyze_error(error_text, error_type, entities, lineage_context)
