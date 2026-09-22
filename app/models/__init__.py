"""Normalized internal model of Azure Data Factory artifacts.

Everything outside ``app.parsers`` works against these models rather than raw
ADF JSON. Models are Pydantic v2 ``BaseModel`` subclasses so they validate on
construction and serialize cleanly for the AI sanitization layer.
"""

from app.models.activity import Activity, ActivityDependency
from app.models.artifact import ArtifactKind, ParsedArtifact
from app.models.common import DatasetReference
from app.models.dataflow import (
    DataFlow,
    DataFlowColumnDef,
    DataFlowScriptStatement,
    DataFlowSink,
    DataFlowSource,
    DataFlowTransformation,
)
from app.models.expression import ExpressionAnalysis, ExpressionDialect
from app.models.pipeline import Pipeline, PipelineParameter, PipelineVariable

__all__ = [
    "Activity",
    "ActivityDependency",
    "ArtifactKind",
    "DataFlow",
    "DataFlowColumnDef",
    "DataFlowScriptStatement",
    "DataFlowSink",
    "DataFlowSource",
    "DataFlowTransformation",
    "DatasetReference",
    "ExpressionAnalysis",
    "ExpressionDialect",
    "ParsedArtifact",
    "Pipeline",
    "PipelineParameter",
    "PipelineVariable",
]
