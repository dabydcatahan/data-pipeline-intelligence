"""Deterministic column impact analysis + scoring (F012).

Given a column node in a :class:`~app.lineage.graph.ColumnLineageGraph`, walk its
downstream (descendant) sub-graph and quantify the blast radius of changing it.

Scoring methodology (deterministic, documented)
-----------------------------------------------
A raw score is the sum of weighted signals, each capped so no single signal can
dominate, then clamped to 0-100:

===========================  ======  ====================================================
signal                       weight  cap
===========================  ======  ====================================================
sink columns affected          12      60   (each external output that would change)
join / lookup transformations  10      30
conditional splits             8       24
aggregations                   8       24
filters                        6       24
other transformations          4       20
computed downstream columns    3       30   (DERIVED / AGGREGATE / RENAME edges)
direct fan-out                 2       10
longest downstream path        3       15
this column drives a join/     10      10   (immediate JOIN_KEY / FILTER /
  filter/split directly                       CONDITIONAL_SPLIT edge out of it)
===========================  ======  ====================================================

Level is then the first matching rule (override rules win over thresholds):

* **LOW**  - no downstream dependencies at all (score forced to 0).
* **HIGH** - a sink column is reached through a join or a conditional split; or
  >= 3 sink columns are affected; or raw score >= ``HIGH_THRESHOLD`` (60).
* **MEDIUM** - at least one sink column is affected; or raw score >=
  ``MEDIUM_THRESHOLD`` (25).
* **LOW**  - otherwise.
"""

from __future__ import annotations

import networkx as nx

from app.lineage.graph import ColumnLineageGraph
from app.models.impact import ImpactLevel, ImpactReport
from app.models.lineage import NodeKind, RelationshipType

HIGH_THRESHOLD = 60
MEDIUM_THRESHOLD = 25

#: signal -> (weight per unit, contribution cap)
_WEIGHTS: dict[str, tuple[int, int]] = {
    "sink_columns": (12, 60),
    "joins": (10, 30),
    "splits": (8, 24),
    "aggregations": (8, 24),
    "filters": (6, 24),
    "other_transformations": (4, 20),
    "computed_columns": (3, 30),
    "fan_out": (2, 10),
    "depth": (3, 15),
    "drives_gate": (10, 10),
}

_FACTOR_LABELS: dict[str, str] = {
    "sink_columns": "sink column(s) affected",
    "joins": "join/lookup transformation(s)",
    "splits": "conditional split(s)",
    "aggregations": "aggregation(s)",
    "filters": "filter transformation(s)",
    "other_transformations": "other transformation(s)",
    "computed_columns": "computed downstream column(s)",
    "fan_out": "direct dependent column(s)",
    "depth": "longest downstream path",
    "drives_gate": "directly drives a join/filter/split",
}

_JOIN_OPERATORS = {"join", "lookup", "exists"}
_COMPUTED_RELATIONSHIPS = {
    RelationshipType.DERIVED.value,
    RelationshipType.AGGREGATE.value,
    RelationshipType.RENAME.value,
}
_GATE_RELATIONSHIPS = {
    RelationshipType.JOIN_KEY.value,
    RelationshipType.LOOKUP.value,
    RelationshipType.FILTER.value,
    RelationshipType.CONDITIONAL_SPLIT.value,
}


def _capped(name: str, count: int) -> tuple[int, int]:
    weight, cap = _WEIGHTS[name]
    return min(weight * count, cap), count


def analyze_impact(graph: ColumnLineageGraph, node_id: str) -> ImpactReport:
    """Build an :class:`ImpactReport` for one column node."""
    if node_id not in graph:
        raise KeyError(node_id)
    g = graph.networkx
    node = graph.node(node_id)

    downstream = graph.downstream(node_id)
    direct = set(g.successors(node_id))
    indirect = downstream - direct

    sink_columns = sorted(n for n in downstream if g.nodes[n]["kind"] == NodeKind.SINK_COLUMN.value)

    # Classify affected transformation streams by operator.
    affected_streams = {graph.node(n).stream for n in downstream}
    joins: list[str] = []
    filters: list[str] = []
    splits: list[str] = []
    aggregations: list[str] = []
    other_transformations: list[str] = []
    for stream in sorted(affected_streams):
        if graph.streams.get(stream) != "transformation":
            continue
        operator = str(graph.stream_meta.get(stream, {}).get("operator", ""))
        if operator in _JOIN_OPERATORS:
            joins.append(stream)
        elif operator == "filter":
            filters.append(stream)
        elif operator == "split":
            splits.append(stream)
        elif operator == "aggregate":
            aggregations.append(stream)
        else:
            other_transformations.append(stream)

    computed = sorted(
        n
        for n in downstream
        if any(
            g.edges[pred, n]["relationship"] in _COMPUTED_RELATIONSHIPS
            for pred in g.predecessors(n)
            if pred in downstream or pred == node_id
        )
    )

    drives_gate = any(
        g.edges[node_id, succ]["relationship"] in _GATE_RELATIONSHIPS for succ in direct
    )

    depth = _longest_downstream_path(g, node_id, downstream)
    affected_stream_names = tuple(sorted(s for s in affected_streams if s != node.stream))
    critical_path = _critical_path(g, node_id, sink_columns)
    sink_reached_through_gate = any(
        g.edges[critical_path[i], critical_path[i + 1]]["relationship"] in _GATE_RELATIONSHIPS
        for i in range(len(critical_path) - 1)
    )

    # ------------------------------------------------------------------ score
    parts = [
        _capped("sink_columns", len(sink_columns)),
        _capped("joins", len(joins)),
        _capped("splits", len(splits)),
        _capped("aggregations", len(aggregations)),
        _capped("filters", len(filters)),
        _capped("other_transformations", len(other_transformations)),
        _capped("computed_columns", len(computed)),
        _capped("fan_out", len(direct)),
        _capped("depth", depth),
        _capped("drives_gate", 1 if drives_gate else 0),
    ]
    names = list(_WEIGHTS)
    raw = sum(value for value, _ in parts)
    score = 0 if not downstream else min(100, raw)

    factors = [
        f"{count} {_FACTOR_LABELS[name]} (+{value})"
        for name, (value, count) in zip(names, parts, strict=True)
        if count
    ]

    level = _level(score, sink_columns, sink_reached_through_gate, bool(downstream))
    factors.append(f"raw score {raw} -> {min(100, raw)} -> {level.label}")

    return ImpactReport(
        node_id=node_id,
        stream=node.stream,
        column=node.column,
        direct_downstream=tuple(sorted(direct)),
        indirect_downstream=tuple(sorted(indirect)),
        all_downstream=tuple(sorted(downstream)),
        sink_columns=tuple(sink_columns),
        transformations_affected=tuple(
            sorted(joins + filters + splits + aggregations + other_transformations)
        ),
        joins_affected=tuple(joins),
        filters_affected=tuple(filters),
        splits_affected=tuple(splits),
        aggregations_affected=tuple(aggregations),
        computed_columns_affected=tuple(computed),
        affected_streams=affected_stream_names,
        critical_path=critical_path,
        score=score,
        level=level,
        factors=tuple(factors),
    )


def _longest_downstream_path(g: nx.DiGraph, node_id: str, downstream: set[str]) -> int:
    if not downstream:
        return 0
    sub = g.subgraph(downstream | {node_id})
    try:
        return int(nx.dag_longest_path_length(sub))
    except nx.NetworkXUnfeasible:  # pragma: no cover - graph is a DAG by construction
        return 0


def _critical_path(g: nx.DiGraph, node_id: str, sink_columns: list[str]) -> tuple[str, ...]:
    """Shortest path from ``node_id`` to the riskiest reachable sink column.

    Prefers paths that cross a join/lookup/filter/split gate (these are the
    edits most likely to silently change row counts or values), breaking ties
    by shortest path length so the result stays deterministic.
    """
    best: tuple[tuple[int, int], list[str]] | None = None
    for sink in sink_columns:
        try:
            path = nx.shortest_path(g, node_id, sink)
        except nx.NetworkXNoPath:  # pragma: no cover - sink came from descendants(node_id)
            continue
        has_gate = any(
            g.edges[path[i], path[i + 1]]["relationship"] in _GATE_RELATIONSHIPS
            for i in range(len(path) - 1)
        )
        key = (0 if has_gate else 1, len(path))
        if best is None or key < best[0]:
            best = (key, path)
    return tuple(best[1]) if best else ()


def _level(
    score: int,
    sink_columns: list[str],
    sink_reached_through_gate: bool,
    has_downstream: bool,
) -> ImpactLevel:
    if not has_downstream:
        return ImpactLevel.LOW
    if sink_columns and sink_reached_through_gate:
        return ImpactLevel.HIGH
    if len(sink_columns) >= 3 or score >= HIGH_THRESHOLD:
        return ImpactLevel.HIGH
    if sink_columns or score >= MEDIUM_THRESHOLD:
        return ImpactLevel.MEDIUM
    return ImpactLevel.LOW
