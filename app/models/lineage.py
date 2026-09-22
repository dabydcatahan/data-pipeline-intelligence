"""Lineage value types (F009).

The lineage engine (`app/lineage/`) builds a NetworkX ``DiGraph`` whose nodes and
edges carry these payloads. Edges point **downstream** (a source column -> the
column that depends on it), so ``networkx.descendants`` gives downstream / impact
and ``networkx.ancestors`` gives upstream / origin.
"""

from __future__ import annotations

from enum import StrEnum

from app.models.common import ModelBase


class NodeKind(StrEnum):
    SOURCE_DATASET = "source_dataset"
    SOURCE_COLUMN = "source_column"
    ACTIVITY = "activity"
    TRANSFORMATION = "transformation"
    OUTPUT_COLUMN = "output_column"
    SINK_DATASET = "sink_dataset"
    SINK_COLUMN = "sink_column"


class StreamKind(StrEnum):
    """Coarse classification of a data-flow stream (used for grouping / display)."""

    SOURCE = "source"
    TRANSFORMATION = "transformation"
    SINK = "sink"


class RelationshipType(StrEnum):
    DIRECT = "direct"
    PASSTHROUGH = "passthrough"
    RENAME = "rename"
    DERIVED = "derived"
    AGGREGATE = "aggregate"
    JOIN_KEY = "join_key"
    LOOKUP = "lookup"
    FILTER = "filter"
    CONDITIONAL_SPLIT = "conditional_split"
    UNION = "union"
    WINDOW = "window"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {
            RelationshipType.DIRECT: "direct mapping",
            RelationshipType.PASSTHROUGH: "carried through unchanged",
            RelationshipType.RENAME: "renamed",
            RelationshipType.DERIVED: "derived by expression",
            RelationshipType.AGGREGATE: "aggregated",
            RelationshipType.JOIN_KEY: "used as join key",
            RelationshipType.LOOKUP: "used as lookup key",
            RelationshipType.FILTER: "used in filter condition",
            RelationshipType.CONDITIONAL_SPLIT: "used in conditional split",
            RelationshipType.UNION: "unioned",
            RelationshipType.WINDOW: "window expression",
            RelationshipType.UNKNOWN: "relationship undetermined",
        }[self]


class LineageNode(ModelBase):
    id: str
    kind: NodeKind
    label: str
    stream: str
    column: str


class LineageEdge(ModelBase):
    source: str
    target: str
    relationship: RelationshipType = RelationshipType.UNKNOWN
    transformation: str | None = None
    transformation_type: str | None = None
    expression: str | None = None
    passthrough: bool = False


class ColumnJourneyStep(ModelBase):
    """One column node on a column's journey, with how it was produced."""

    node_id: str
    stream: str
    column: str
    stream_kind: str
    #: Inbound dependencies within the traced subgraph.
    inputs: tuple[LineageEdge, ...] = ()

    @property
    def is_origin(self) -> bool:
        return not self.inputs


class ColumnJourney(ModelBase):
    """Deterministic 'where did this column come from / go to' explanation."""

    node_id: str
    stream: str
    column: str
    origins: tuple[str, ...] = ()
    sink_columns: tuple[str, ...] = ()
    upstream_steps: tuple[ColumnJourneyStep, ...] = ()
    downstream_columns: tuple[str, ...] = ()
    transformations: tuple[str, ...] = ()

    def narrative(self) -> str:
        """Plain-language walk from origin(s) to this column and onward."""
        lines: list[str] = []
        for step in self.upstream_steps:
            if step.is_origin:
                lines.append(f"SOURCE  {step.stream}.{step.column}")
                continue
            for edge in step.inputs:
                src_stream, _, src_col = edge.source.partition("::")
                detail = f" [{edge.expression}]" if edge.expression else ""
                lines.append(
                    f"  ↓ {edge.transformation or step.stream} "
                    f"({edge.transformation_type or 'n/a'}): {edge.relationship.label}"
                )
                lines.append(f"{step.stream}.{step.column}  ←  {src_stream}.{src_col}{detail}")
        if self.downstream_columns:
            lines.append("")
            lines.append("Downstream: " + ", ".join(self.downstream_columns))
        if self.sink_columns:
            lines.append("Reaches sink columns: " + ", ".join(self.sink_columns))
        return "\n".join(lines)


class DataFlowChainStep(ModelBase):
    """One ``ExecuteDataFlow`` activity's position in a pipeline's run order (F026).

    Built purely from ``dependsOn`` + activity type (see
    :func:`app.lineage.pipeline_lineage.build_data_flow_chain`); no dataset
    identity is involved here. ``upstream_data_flows`` names the nearest
    upstream ``ExecuteDataFlow`` activities this one (transitively) depends on,
    even when non-data-flow activities (e.g. a staging Copy) sit in between.
    """

    activity_name: str
    data_flow_reference: str
    order: int
    upstream_data_flows: tuple[str, ...] = ()
