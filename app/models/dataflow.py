"""Normalized Mapping Data Flow model (F004).

The authoritative transformation logic in an exported Mapping Data Flow lives in
the ``typeProperties.scriptLines`` array - the Data Flow Script (DFS). The
``transformations`` array usually carries only names. We therefore parse the DFS
into structured statements and enrich sources / sinks / transformations from it.

Column *dependency* resolution (which upstream columns feed an expression) is the
job of the expression parser (F021) and lineage engine (F009). This layer only
captures each column's target name and its raw expression text.
"""

from __future__ import annotations

from app.models.common import DatasetReference, ModelBase

#: DFS operators we understand structurally. Anything else is kept but flagged.
RECOGNIZED_OPERATORS = frozenset(
    {
        "source",
        "sink",
        "derive",
        "select",
        "filter",
        "join",
        "lookup",
        "aggregate",
        "union",
        "split",
        "pivot",
        "unpivot",
        "window",
        "sort",
        "alterRow",
        "exists",
        "flatten",
        "foldDown",
        "unroll",
        "parse",
        "cast",
        "rank",
        "surrogateKey",
        "keyGenerate",
        "sample",
        "assert",
        "dedupe",
        "stringify",
        "unfold",
        "externalCall",
        "rankAndDenseRank",
    }
)

#: Column-def ``kind`` values.
COLUMN_KINDS = frozenset(
    {"source_schema", "retained", "renamed", "derived", "aggregate", "group_by"}
)


class DataFlowColumnDef(ModelBase):
    """A column produced, retained or grouped-by inside a transformation.

    ``referenced_columns`` lists the upstream columns feeding ``expression`` as
    resolved by the expression parser (F021). It is trustworthy only when
    ``analysis_confidence == "high"``; ``"none"`` means the expression could not
    be analyzed and dependencies are unknown.
    """

    target: str
    expression: str | None = None
    data_type: str | None = None
    kind: str = "derived"
    referenced_columns: tuple[str, ...] = ()
    analysis_confidence: str = "high"


class DataFlowScriptStatement(ModelBase):
    """One parsed DFS statement: ``[inputs] operator(args) ~> output``."""

    output_stream: str
    output_branches: tuple[str, ...] = ()
    input_streams: tuple[str, ...] = ()
    operator: str = ""
    raw_arguments: str = ""
    raw_text: str = ""
    is_recognized: bool = True


class DataFlowSource(ModelBase):
    name: str
    dataset: DatasetReference | None = None
    linked_service_only: bool = False
    schema_columns: tuple[str, ...] = ()


class DataFlowSink(ModelBase):
    name: str
    dataset: DatasetReference | None = None
    input_stream: str | None = None


class DataFlowTransformation(ModelBase):
    name: str
    #: DFS operator, e.g. "select", "derive", "join", "aggregate".
    kind: str | None = None
    input_streams: tuple[str, ...] = ()
    raw_arguments: str = ""
    raw_script: str = ""
    columns: tuple[DataFlowColumnDef, ...] = ()
    group_by: tuple[str, ...] = ()
    is_recognized: bool = True

    @property
    def derived_columns(self) -> tuple[DataFlowColumnDef, ...]:
        return tuple(c for c in self.columns if c.kind in {"derived", "aggregate"})

    @property
    def renamed_columns(self) -> tuple[DataFlowColumnDef, ...]:
        return tuple(c for c in self.columns if c.kind == "renamed")


class DataFlow(ModelBase):
    """A normalized Mapping Data Flow definition."""

    name: str
    description: str | None = None
    folder: str | None = None
    annotations: tuple[str, ...] = ()
    sources: tuple[DataFlowSource, ...] = ()
    sinks: tuple[DataFlowSink, ...] = ()
    transformations: tuple[DataFlowTransformation, ...] = ()
    statements: tuple[DataFlowScriptStatement, ...] = ()
    script_lines: tuple[str, ...] = ()

    @property
    def source_count(self) -> int:
        return len(self.sources)

    @property
    def sink_count(self) -> int:
        return len(self.sinks)

    @property
    def transformation_count(self) -> int:
        return len(self.transformations)

    @property
    def stream_names(self) -> tuple[str, ...]:
        names: dict[str, None] = {}
        for source in self.sources:
            names.setdefault(source.name, None)
        for transformation in self.transformations:
            names.setdefault(transformation.name, None)
        for sink in self.sinks:
            names.setdefault(sink.name, None)
        return tuple(names)

    @property
    def unrecognized_operators(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for st in self.statements:
            if not st.is_recognized:
                seen.setdefault(st.operator or "?", None)
        return tuple(seen)

    def transformation(self, name: str) -> DataFlowTransformation | None:
        return next((t for t in self.transformations if t.name == name), None)

    def statement_for(self, stream: str) -> DataFlowScriptStatement | None:
        return next((s for s in self.statements if s.output_stream == stream), None)

    def stream_edges(self) -> tuple[tuple[str, str], ...]:
        """``(upstream_stream, downstream_stream)`` pairs from the DFS graph."""
        edges: list[tuple[str, str]] = []
        for st in self.statements:
            targets = (st.output_stream, *st.output_branches)
            for upstream in st.input_streams:
                for target in targets:
                    if upstream and target:
                        edges.append((upstream, target))
        return tuple(edges)
