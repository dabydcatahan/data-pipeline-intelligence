"""Render a :class:`~app.lineage.graph.ColumnLineageGraph` for display (F015).

Two renderers:

* :func:`build_dot` - a Graphviz DOT string. No extra dependency; Streamlit's
  ``st.graphviz_chart`` renders it with pan/zoom. This is the always-available
  default and is what the tests pin.
* :func:`to_pyvis_html` - an interactive HTML document, behind the optional
  ``viz`` extra (``pip install '.[viz]'``). Lazily imported; raises a clear
  error when PyVis is not installed.

Both group columns into per-stream clusters and colour edges by
:class:`~app.models.lineage.RelationshipType`. In *focus mode* the view is
restricted to one column's ancestors / descendants and those three sets
(focus / upstream / downstream) are colour-coded.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from typing import Literal

from app.lineage.graph import ColumnLineageGraph
from app.models.lineage import RelationshipType

Direction = Literal["both", "upstream", "downstream"]

DEFAULT_MAX_NODES = 400

_STREAM_FILL = {
    "source": "#E8F0FE",
    "transformation": "#F1F3F4",
    "sink": "#E6F4EA",
}
_STREAM_BORDER = {
    "source": "#4285F4",
    "transformation": "#5F6368",
    "sink": "#34A853",
}

_FOCUS_FILL = "#FDE7E9"
_FOCUS_BORDER = "#EA4335"
_UPSTREAM_BORDER = "#1A73E8"
_DOWNSTREAM_BORDER = "#F9AB00"
_PLAIN_NODE_FILL = "#FFFFFF"

#: Impact-level -> focus node colour, so severity is visible at a glance.
_LEVEL_COLORS = {
    "low": ("#E6F4EA", "#0B8043"),
    "medium": ("#FEF7E0", "#E37400"),
    "high": ("#FDE7E9", "#C5221F"),
}

_CRITICAL_PATH_COLOR = "#C5221F"

RELATIONSHIP_COLORS: dict[RelationshipType, str] = {
    RelationshipType.DIRECT: "#9AA0A6",
    RelationshipType.PASSTHROUGH: "#BDC1C6",
    RelationshipType.RENAME: "#1A73E8",
    RelationshipType.DERIVED: "#8430CE",
    RelationshipType.AGGREGATE: "#B31412",
    RelationshipType.JOIN_KEY: "#0B8043",
    RelationshipType.LOOKUP: "#0B8043",
    RelationshipType.FILTER: "#E37400",
    RelationshipType.CONDITIONAL_SPLIT: "#9334E6",
    RelationshipType.UNION: "#12B5CB",
    RelationshipType.WINDOW: "#5F6368",
    RelationshipType.UNKNOWN: "#9AA0A6",
}

_EXPRESSION_ON = {
    RelationshipType.DERIVED,
    RelationshipType.AGGREGATE,
    RelationshipType.RENAME,
}
_MAX_EDGE_LABEL = 42
_ELLIPSIS = "…"
_CAPTION_SEP = " — "


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _truncate(text: str, limit: int = _MAX_EDGE_LABEL) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + _ELLIPSIS


def focus_node_set(
    graph: ColumnLineageGraph, focus: str, direction: Direction = "both"
) -> set[str]:
    """Node ids to show when focused on ``focus``."""
    if focus not in graph:
        raise KeyError(focus)
    nodes = {focus}
    if direction in ("both", "upstream"):
        nodes |= graph.upstream(focus)
    if direction in ("both", "downstream"):
        nodes |= graph.downstream(focus)
    return nodes


def build_dot(
    graph: ColumnLineageGraph,
    *,
    focus: str | None = None,
    direction: Direction = "both",
    max_nodes: int = DEFAULT_MAX_NODES,
    level: str | None = None,
    critical_path: Sequence[str] | None = None,
) -> str:
    """Return a Graphviz DOT document for ``graph`` (optionally focused).

    ``level`` ("low" / "medium" / "high") colours the focus node by impact
    severity. ``critical_path`` is a node-id path (as returned by
    :func:`app.lineage.impact.analyze_impact`'s ``critical_path``) drawn as a
    thick red trail through the riskiest route to a sink.
    """
    g = graph.networkx
    critical_edges = set(itertools.pairwise(critical_path)) if critical_path else set()

    if focus is not None:
        visible = focus_node_set(graph, focus, direction)
        upstream = graph.upstream(focus)
        downstream = graph.downstream(focus)
    else:
        visible = set(g.nodes)
        upstream = set()
        downstream = set()

    total = len(visible)
    truncated = total > max_nodes
    if truncated:
        visible = set(sorted(visible)[:max_nodes])

    # Group visible nodes by stream, in a stable order.
    by_stream: dict[str, list[str]] = {}
    for node_id in sorted(visible):
        by_stream.setdefault(graph.node(node_id).stream, []).append(node_id)

    lines: list[str] = [
        "digraph lineage {",
        "  rankdir=LR;",
        '  graph [fontname="Helvetica", fontsize=11, splines=true, nodesep=0.35, ranksep=0.7];',
        '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10];',
        '  edge [fontname="Helvetica", fontsize=8];',
    ]
    caption_bits = [f"{graph.name or 'Column lineage'}"]
    if focus is not None:
        caption_bits.append(f"focus: {graph.node(focus).label} ({direction})")
    if truncated:
        caption_bits.append(f"showing {max_nodes} of {total} columns")
    caption = _escape(_CAPTION_SEP.join(caption_bits))
    lines.append(f'  label="{caption}"; labelloc=t;')

    for stream in sorted(by_stream):
        kind = graph.streams.get(stream, "transformation")
        fill = _STREAM_FILL.get(kind, _STREAM_FILL["transformation"])
        border = _STREAM_BORDER.get(kind, _STREAM_BORDER["transformation"])
        lines.append(f'  subgraph "cluster_{_escape(stream)}" {{')
        lines.append(f'    label="{_escape(stream)}"; style="rounded,filled";')
        lines.append(f'    color="{border}"; fillcolor="{fill}"; fontsize=10;')
        for node_id in by_stream[stream]:
            node = graph.node(node_id)
            node_fill, node_border, penwidth = _node_style(
                node_id, focus, upstream, downstream, kind, level
            )
            lines.append(
                f'    "{_escape(node_id)}" [label="{_escape(node.column)}", '
                f'fillcolor="{node_fill}", color="{node_border}", penwidth={penwidth}];'
            )
        lines.append("  }")

    for source, target, data in sorted(g.edges(data=True), key=lambda e: (e[0], e[1])):
        if source not in visible or target not in visible:
            continue
        relationship = RelationshipType(data["relationship"])
        on_critical_path = (source, target) in critical_edges
        color = (
            _CRITICAL_PATH_COLOR
            if on_critical_path
            else RELATIONSHIP_COLORS.get(relationship, "#9AA0A6")
        )
        attrs = [f'color="{color}"', f'tooltip="{_escape(relationship.label)}"']
        if on_critical_path:
            attrs.append("penwidth=2.6")
        if data.get("passthrough"):
            attrs.append("style=dashed")
        label = _edge_label(relationship, data)
        if label:
            attrs.append(f'label="{_escape(_truncate(label))}"')
            attrs.append(f'fontcolor="{color}"')
        lines.append(f'  "{_escape(source)}" -> "{_escape(target)}" [{", ".join(attrs)}];')

    lines.append("}")
    return "\n".join(lines)


def _node_style(
    node_id: str,
    focus: str | None,
    upstream: set[str],
    downstream: set[str],
    stream_kind: str,
    level: str | None = None,
) -> tuple[str, str, float]:
    if focus is not None and node_id == focus:
        fill, border = _LEVEL_COLORS.get(level or "", (_FOCUS_FILL, _FOCUS_BORDER))
        return fill, border, 2.4
    if node_id in upstream:
        return _PLAIN_NODE_FILL, _UPSTREAM_BORDER, 1.6
    if node_id in downstream:
        return _PLAIN_NODE_FILL, _DOWNSTREAM_BORDER, 1.6
    return _PLAIN_NODE_FILL, _STREAM_BORDER.get(stream_kind, "#5F6368"), 1.0


def _edge_label(relationship: RelationshipType, data: dict[str, object]) -> str:
    expression = data.get("expression")
    if relationship in _EXPRESSION_ON and isinstance(expression, str) and expression:
        return expression
    if relationship in (RelationshipType.DIRECT, RelationshipType.PASSTHROUGH):
        return ""
    return relationship.label


def to_pyvis_html(
    graph: ColumnLineageGraph,
    *,
    focus: str | None = None,
    direction: Direction = "both",
    max_nodes: int = DEFAULT_MAX_NODES,
    height: str = "620px",
) -> str:
    """Return an interactive PyVis HTML document. Requires the ``viz`` extra."""
    try:
        from pyvis.network import Network
    except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Interactive visualization needs the optional 'viz' extra: "
            "pip install 'data-pipeline-intelligence[viz]'"
        ) from exc

    g = graph.networkx
    if focus is not None:
        visible = focus_node_set(graph, focus, direction)
        upstream = graph.upstream(focus)
        downstream = graph.downstream(focus)
    else:
        visible = set(g.nodes)
        upstream = set()
        downstream = set()
    visible = set(sorted(visible)[:max_nodes])

    net = Network(height=height, width="100%", directed=True, notebook=False)
    net.barnes_hut(spring_length=140)
    for node_id in sorted(visible):
        node = graph.node(node_id)
        kind = graph.streams.get(node.stream, "transformation")
        fill, border, penwidth = _node_style(node_id, focus, upstream, downstream, kind)
        net.add_node(
            node_id,
            label=node.column,
            title=f"{node.label} ({kind})",
            group=node.stream,
            color={"background": fill, "border": border},
            borderWidth=penwidth,
        )
    for source, target, data in sorted(g.edges(data=True), key=lambda e: (e[0], e[1])):
        if source not in visible or target not in visible:
            continue
        relationship = RelationshipType(data["relationship"])
        net.add_edge(
            source,
            target,
            color=RELATIONSHIP_COLORS.get(relationship, "#9AA0A6"),
            title=relationship.label,
            dashes=bool(data.get("passthrough")),
        )
    html = net.generate_html(notebook=False)
    assert isinstance(html, str)
    return html
