"""Deterministic column-level lineage engine (F009 / F008).

Public surface:

* :class:`ColumnLineageGraph` - typed wrapper around a NetworkX ``DiGraph``;
* :func:`build_data_flow_lineage` - lineage for one Mapping Data Flow;
* :func:`build_pipeline_lineage` - lineage from a pipeline's Copy activities;
* :func:`build_combined_lineage` - merge + stitch every artifact's graph by
  dataset identity;
* :func:`explain` - a :class:`~app.models.lineage.ColumnJourney` for one column;
* :func:`analyze_impact` and traversal helpers.
"""

from app.lineage.column_lineage import DataFlowLineage, build_data_flow_lineage
from app.lineage.graph import ColumnLineageGraph, node_id, split_id
from app.lineage.impact import analyze_impact
from app.lineage.pipeline_lineage import build_data_flow_chain, build_pipeline_lineage
from app.lineage.plain_language import impact_plain, operator_plain, relationship_plain
from app.lineage.stitch import build_combined_lineage, data_flow_bindings, shared_datasets
from app.lineage.traversal import (
    ancestors,
    descendants,
    explain,
    origins,
    sink_columns_reached,
)

__all__ = [
    "ColumnLineageGraph",
    "DataFlowLineage",
    "analyze_impact",
    "ancestors",
    "build_combined_lineage",
    "build_data_flow_chain",
    "build_data_flow_lineage",
    "build_pipeline_lineage",
    "data_flow_bindings",
    "descendants",
    "explain",
    "impact_plain",
    "node_id",
    "operator_plain",
    "origins",
    "relationship_plain",
    "shared_datasets",
    "sink_columns_reached",
    "split_id",
]
