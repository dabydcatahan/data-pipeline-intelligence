"""A provider wrapper that falls back to a secondary provider on any error."""

from __future__ import annotations

from app.core.logging import get_logger
from app.insights.interface import InsightProvider, InsightRequest, InsightResult

logger = get_logger(__name__)


class SafeInsightProvider(InsightProvider):
    """Try ``primary``; on any exception, use ``fallback`` and annotate the result."""

    def __init__(self, primary: InsightProvider, fallback: InsightProvider) -> None:
        self._primary = primary
        self._fallback = fallback
        self.name = f"{primary.name}->{fallback.name}"

    def generate(self, request: InsightRequest) -> InsightResult:
        try:
            return self._primary.generate(request)
        except Exception as exc:
            logger.warning(
                "insight provider %s failed (%s); falling back to %s",
                self._primary.name,
                type(exc).__name__,
                self._fallback.name,
            )
            result = self._fallback.generate(request)
            return result.model_copy(
                update={
                    "observations": (
                        *result.observations,
                        f"(AI provider '{self._primary.name}' unavailable — "
                        f"showing {self._fallback.name} output.)",
                    ),
                }
            )
