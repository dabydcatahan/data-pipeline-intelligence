"""ADF JSON parsing layer.

This is the *only* package permitted to index into raw ADF JSON dictionaries
(DEC-004). It converts uploaded JSON into the normalized ``app.models`` types.
"""

from app.parsers.base import ParseContext
from app.parsers.dataflow_parser import DataFlowParser
from app.parsers.expression_parser import ExpressionParser, analyze_expression
from app.parsers.json_loader import detect_artifact_kind, load_adf_json
from app.parsers.pipeline_parser import PipelineParser

__all__ = [
    "DataFlowParser",
    "ExpressionParser",
    "ParseContext",
    "PipelineParser",
    "analyze_expression",
    "detect_artifact_kind",
    "load_adf_json",
]
