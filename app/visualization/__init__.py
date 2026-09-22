"""Lineage visualization (F015).

`build_dot` (Graphviz DOT, no extra dependency) is the default renderer;
`to_pyvis_html` is optional and needs the ``viz`` extra.
"""

from app.visualization.lineage import (
    RELATIONSHIP_COLORS,
    build_dot,
    focus_node_set,
    to_pyvis_html,
)

__all__ = ["RELATIONSHIP_COLORS", "build_dot", "focus_node_set", "to_pyvis_html"]
