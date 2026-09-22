"""Result model for the expression parser subsystem (F021)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from app.models.common import ModelBase

Confidence = Literal["high", "partial", "none"]


class ExpressionDialect(StrEnum):
    """Which ADF expression language a string is written in."""

    DATA_FLOW = "data_flow"
    PIPELINE = "pipeline"


class ExpressionAnalysis(ModelBase):
    """Deterministic, best-effort analysis of one expression.

    ``referenced_columns`` is only trustworthy when ``confidence == "high"``.
    For unsupported constructs the analyzer says so via ``confidence`` /
    ``notes`` rather than guessing (see PROJECT_PLAN.md section 12).
    """

    expression: str
    dialect: ExpressionDialect = ExpressionDialect.DATA_FLOW
    referenced_columns: tuple[str, ...] = ()
    qualified_columns: tuple[str, ...] = ()
    functions: tuple[str, ...] = ()
    parameters: tuple[str, ...] = ()
    confidence: Confidence = "high"
    notes: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        """True when the analyzer is confident it found every column reference."""
        return self.confidence == "high"

    @property
    def summary(self) -> str:
        if self.confidence == "none":
            return "Unable to determine automatically"
        if not self.referenced_columns:
            body = "no column dependencies"
        else:
            body = "depends on " + ", ".join(self.referenced_columns)
        return f"{body} (partial analysis)" if self.confidence == "partial" else body
