"""Insight layer: offline deterministic insight provider only.

The deterministic engine is authoritative; it turns already-computed
facts into plain-language prose and never invents lineage.
"""

from __future__ import annotations

from app.core.config import AppSettings
from app.insights.deterministic import DeterministicInsightProvider
from app.insights.interface import InsightProvider, InsightRequest, InsightResult
from app.insights.sanitize import column_request, data_flow_request, pipeline_request

__all__ = [
    "DeterministicInsightProvider",
    "InsightProvider",
    "InsightRequest",
    "InsightResult",
    "column_request",
    "data_flow_request",
    "get_provider",
    "pipeline_request",
]


def get_provider(settings: AppSettings) -> InsightProvider:
    """Return the offline deterministic insight provider."""
    return DeterministicInsightProvider()
