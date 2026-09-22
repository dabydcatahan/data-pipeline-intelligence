"""Build sanitized :class:`InsightRequest` objects for the AI layer (F017).

Only *structural* facts derived from the normalized model are included: names,
counts, operators, relationship types and transformation expressions. Raw
uploaded JSON, connection details and column *values* are never present here.
(String literals inside a transformation expression - e.g. ``'CUST-'`` - are
transformation logic, not data, and are kept.)
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from app.insights.interface import InsightRequest
from app.lineage.column_lineage import DataFlowLineage
from app.models.dataflow import DataFlow
from app.models.impact import ImpactReport
from app.models.lineage import ColumnJourney

if TYPE_CHECKING:  # avoid a runtime import cycle (services -> insights)
    from app.services.analysis_service import PipelineSummary


def pipeline_request(summary: PipelineSummary) -> InsightRequest:
    facts: dict[str, object] = {
        "kind": "pipeline",
        "name": summary.name,
        "activity_count": summary.activity_count,
        "activity_types": dict(summary.activity_types),
        "parameters": list(summary.parameter_names),
        "variables": list(summary.variable_names),
        "annotations": list(summary.annotations),
        "execution_order": list(summary.execution_order),
        "has_cycle": bool(summary.cyclic_activities),
        "cyclic_activities": list(summary.cyclic_activities),
        "dependency_edge_count": len(summary.dependency_edges),
        "input_datasets": list(summary.input_datasets),
        "output_datasets": list(summary.output_datasets),
        "data_flow_references": list(summary.data_flow_references),
        "unrecognized_activities": list(summary.unrecognized_activities),
    }
    return InsightRequest(subject=f"pipeline:{summary.name}", facts=facts)


def data_flow_request(
    data_flow: DataFlow, lineage: DataFlowLineage | None = None
) -> InsightRequest:
    operators = Counter(t.kind or "unknown" for t in data_flow.transformations)
    facts: dict[str, object] = {
        "kind": "data_flow",
        "name": data_flow.name,
        "source_count": data_flow.source_count,
        "sink_count": data_flow.sink_count,
        "transformation_count": data_flow.transformation_count,
        "operators": dict(operators),
        "unrecognized_operators": list(data_flow.unrecognized_operators),
        "sources": [
            {
                "name": s.name,
                "has_schema": bool(s.schema_columns),
                "column_count": len(s.schema_columns),
            }
            for s in data_flow.sources
        ],
        "sinks": [{"name": s.name, "fed_by": s.input_stream or "?"} for s in data_flow.sinks],
        "transformations": [
            {
                "name": t.name,
                "operator": t.kind or "unknown",
                "recognized": t.is_recognized,
                "input_count": len(t.input_streams),
                "group_by": list(t.group_by),
                "columns": [
                    {
                        "target": c.target,
                        "kind": c.kind,
                        "from": list(c.referenced_columns),
                        "confidence": c.analysis_confidence,
                        "expression": c.expression,
                    }
                    for c in t.columns
                ],
            }
            for t in data_flow.transformations
        ],
    }
    if lineage is not None:
        graph = lineage.graph
        rel_counts = Counter(e.relationship.value for e in graph.edges())
        facts["column_node_count"] = len(graph)
        facts["edge_relationship_counts"] = dict(rel_counts)
        facts["lineage_warning_count"] = len(lineage.warnings)
        facts["lineage_warnings"] = list(lineage.warnings)[:10]
    return InsightRequest(subject=f"dataflow:{data_flow.name}", facts=facts)


def column_request(journey: ColumnJourney, impact: ImpactReport) -> InsightRequest:
    facts: dict[str, object] = {
        "kind": "column",
        "column": journey.column,
        "stream": journey.stream,
        "origins": [o.replace("::", ".") for o in journey.origins],
        "sink_columns": [s.replace("::", ".") for s in journey.sink_columns],
        "downstream_column_count": impact.downstream_column_count,
        "transformations": list(journey.transformations),
        "impact_level": impact.level.value,
        "impact_score": impact.score,
        "impact_factors": list(impact.factors),
        "joins_affected": list(impact.joins_affected),
        "filters_affected": list(impact.filters_affected),
        "splits_affected": list(impact.splits_affected),
        "aggregations_affected": list(impact.aggregations_affected),
        "computed_columns_affected": [
            c.replace("::", ".") for c in impact.computed_columns_affected
        ],
        "narrative": journey.narrative(),
    }
    return InsightRequest(subject=f"column:{journey.stream}.{journey.column}", facts=facts)
