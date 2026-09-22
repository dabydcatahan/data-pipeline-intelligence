"""Provider abstraction for the AI enhancement layer (F017).

Concrete implementations (``DeterministicInsightProvider``, ``LLMInsightProvider``)
are added with F017. The interface is defined now so the rest of the app can
depend on it and remain LLM-free.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict


class InsightRequest(BaseModel):
    """Sanitized metadata handed to an insight provider.

    Contains only structural facts derived from the normalized model - never raw
    uploaded JSON, connection details, or column values.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str  # e.g. "pipeline:CustomerLoad" or "column:customer_id"
    facts: dict[str, object]


class InsightResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str
    observations: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()
    provider: str = "unknown"


class InsightProvider(ABC):
    """Turns deterministic facts into human-readable insight. Never invents lineage."""

    name: str = "abstract"

    @abstractmethod
    def generate(self, request: InsightRequest) -> InsightResult:
        raise NotImplementedError
