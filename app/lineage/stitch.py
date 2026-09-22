"""Merge per-artifact lineage graphs and stitch them by dataset identity (F008).

Given the column-lineage graphs of every uploaded pipeline and data flow, plus
``(stream, dataset, role)`` bindings that say which graph stream reads from or
writes to which ADF dataset, produce ONE combined graph: every node/edge is
copied in, then a ``writer::col -> reader::col`` edge is added for each column a
writer and a reader of the same dataset share.
"""

from __future__ import annotations

from collections import defaultdict

from app.lineage.column_lineage import DataFlowLineage
from app.lineage.graph import ColumnLineageGraph
from app.models.dataflow import DataFlow
from app.models.lineage import NodeKind, RelationshipType


def data_flow_bindings(data_flow: DataFlow) -> tuple[tuple[str, str, str], ...]:
    """``(stream, dataset, role)`` bindings for a data flow's sources/sinks."""
    out: list[tuple[str, str, str]] = []
    for source in data_flow.sources:
        if source.dataset:
            out.append((source.name, source.dataset.name, "reader"))
    for sink in data_flow.sinks:
        if sink.dataset:
            out.append((sink.name, sink.dataset.name, "writer"))
    return tuple(out)


def shared_datasets(upstream: DataFlow, downstream: DataFlow) -> tuple[str, ...]:
    """Dataset names written by ``upstream`` and read by ``downstream``.

    A non-empty result confirms the two data flows are already automatically
    stitched (by :func:`build_combined_lineage`) through a shared staging
    dataset - i.e. the output of ``upstream`` is a documented input of
    ``downstream``. An empty result means no such link could be derived from
    dataset identity alone (e.g. different dataset names, or parameter-only
    linkage the JSON doesn't expose).
    """
    upstream_sinks = {sink.dataset.name for sink in upstream.sinks if sink.dataset}
    downstream_sources = {source.dataset.name for source in downstream.sources if source.dataset}
    return tuple(sorted(upstream_sinks & downstream_sources))


def _copy_into(target: ColumnLineageGraph, source: ColumnLineageGraph) -> None:
    for stream, kind in source.streams.items():
        target.register_stream(stream, kind, **source.stream_meta.get(stream, {}))
    for node in source.iter_nodes():
        target.add_column(node.stream, node.column, node.kind)
    for edge in source.edges():
        s_stream, s_col = _split(edge.source)
        t_stream, t_col = _split(edge.target)
        target.add_dependency(
            s_stream,
            s_col,
            t_stream,
            t_col,
            relationship=edge.relationship,
            transformation=edge.transformation,
            transformation_type=edge.transformation_type,
            expression=edge.expression,
            passthrough=edge.passthrough,
        )


def _split(node_id: str) -> tuple[str, str]:
    stream, _, column = node_id.partition("::")
    return stream, column


def build_combined_lineage(
    lineages: list[DataFlowLineage],
    extra_bindings: list[tuple[str, str, str]] | None = None,
) -> DataFlowLineage:
    combined = ColumnLineageGraph("(combined)")
    for lineage in lineages:
        _copy_into(combined, lineage.graph)

    bindings: list[tuple[str, str, str]] = list(extra_bindings or [])
    for lineage in lineages:
        bindings.extend(lineage.bindings)

    by_dataset: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"reader": set(), "writer": set()}
    )
    for stream, dataset, role in bindings:
        if role in ("reader", "writer"):
            by_dataset[dataset][role].add(stream)

    links = 0
    warnings: list[str] = []
    for dataset, roles in sorted(by_dataset.items()):
        writers, readers = sorted(roles["writer"]), sorted(roles["reader"])
        if not writers or not readers:
            continue
        for writer in writers:
            writer_cols = {n.column for n in combined.iter_nodes() if n.stream == writer}
            for reader in readers:
                if reader == writer:
                    continue
                reader_cols = {n.column for n in combined.iter_nodes() if n.stream == reader}
                for column in sorted(writer_cols & reader_cols):
                    combined.add_column(writer, column, NodeKind.SINK_COLUMN)
                    combined.add_column(reader, column, NodeKind.SOURCE_COLUMN)
                    combined.add_dependency(
                        writer,
                        column,
                        reader,
                        column,
                        relationship=RelationshipType.DIRECT,
                        transformation=f"{dataset}",
                        transformation_type="dataset",
                    )
                    links += 1

    if links:
        warnings.append(f"{links} cross-artifact column link(s) added by shared dataset identity.")
    if combined.has_cycle():
        warnings.append(
            "The combined graph contains a cycle (a dataset is both produced and consumed "
            "across artifacts); traversal still works but longest-path metrics are skipped."
        )
    return DataFlowLineage(
        data_flow="(combined)",
        graph=combined,
        warnings=tuple(warnings),
        kind="combined",
    )
