"""Upstream / downstream traversal and column-journey explanation (F009 T009.10/T009.11)."""

from __future__ import annotations

import networkx as nx

from app.lineage.graph import ColumnLineageGraph
from app.models.lineage import ColumnJourney, ColumnJourneyStep, NodeKind


def ancestors(graph: ColumnLineageGraph, node_id: str) -> set[str]:
    return graph.upstream(node_id)


def descendants(graph: ColumnLineageGraph, node_id: str) -> set[str]:
    return graph.downstream(node_id)


def origins(graph: ColumnLineageGraph, node_id: str) -> list[str]:
    """Upstream nodes that are source columns (or, failing that, in-degree 0)."""
    g = graph.networkx
    ups = graph.upstream(node_id)
    if not ups:
        return []
    sourced = [n for n in ups if g.nodes[n]["kind"] == NodeKind.SOURCE_COLUMN.value]
    if sourced:
        return sorted(sourced)
    return sorted(n for n in ups if g.in_degree(n) == 0)


def sink_columns_reached(graph: ColumnLineageGraph, node_id: str) -> list[str]:
    g = graph.networkx
    return sorted(
        n for n in graph.downstream(node_id) if g.nodes[n]["kind"] == NodeKind.SINK_COLUMN.value
    )


def _ordered(graph: ColumnLineageGraph, nodes: set[str]) -> list[str]:
    sub = graph.networkx.subgraph(nodes)
    try:
        return list(nx.topological_sort(sub))
    except nx.NetworkXUnfeasible:  # pragma: no cover - cycles are guarded upstream
        return sorted(nodes)


def explain(graph: ColumnLineageGraph, node_id: str) -> ColumnJourney:
    """Build a :class:`ColumnJourney` for one column node."""
    if node_id not in graph:
        raise KeyError(node_id)
    g = graph.networkx
    node = graph.node(node_id)

    upstream_set = graph.upstream(node_id) | {node_id}
    steps: list[ColumnJourneyStep] = []
    transformations: dict[str, None] = {}
    for identifier in _ordered(graph, upstream_set):
        data = g.nodes[identifier]
        inbound = [
            graph.edge(src, identifier)
            for src in sorted(g.predecessors(identifier))
            if src in upstream_set
        ]
        for edge in inbound:
            if edge.transformation:
                transformations.setdefault(edge.transformation, None)
        steps.append(
            ColumnJourneyStep(
                node_id=identifier,
                stream=data["stream"],
                column=data["column"],
                stream_kind=graph.streams.get(data["stream"], "transformation"),
                inputs=tuple(inbound),
            )
        )

    downstream = sorted(graph.downstream(node_id))
    for identifier in downstream:
        for src in g.predecessors(identifier):
            edge = graph.edge(src, identifier)
            if edge.transformation:
                transformations.setdefault(edge.transformation, None)

    return ColumnJourney(
        node_id=node_id,
        stream=node.stream,
        column=node.column,
        origins=tuple(origins(graph, node_id)),
        sink_columns=tuple(sink_columns_reached(graph, node_id)),
        upstream_steps=tuple(steps),
        downstream_columns=tuple(downstream),
        transformations=tuple(transformations),
    )
