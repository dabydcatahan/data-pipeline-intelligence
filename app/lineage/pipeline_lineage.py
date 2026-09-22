"""Column lineage for a pipeline, from Copy-activity column mappings (F008).

Scope: Copy activities that carry an explicit ``translator`` mapping. Nodes are
``dataset::column``; one edge per mapping (DIRECT when the name is unchanged,
RENAME otherwise), tagged with the activity name. Activities without a mapping
(or without input/output datasets) are reported as warnings - their column
lineage cannot be derived from the JSON alone.

Also builds the pipeline's ``ExecuteDataFlow`` execution chain (F026) so a
pipeline with several data-flow activities can be explained as continuity:
"output of data flow A feeds data flow B feeds data flow C".
"""

from __future__ import annotations

from app.lineage.column_lineage import DataFlowLineage
from app.lineage.graph import ColumnLineageGraph
from app.models.activity import Activity
from app.models.lineage import DataFlowChainStep, NodeKind, RelationshipType, StreamKind
from app.models.pipeline import Pipeline


def build_pipeline_lineage(pipeline: Pipeline) -> DataFlowLineage:
    graph = ColumnLineageGraph(pipeline.name)
    warnings: list[str] = []
    bindings: dict[tuple[str, str], None] = {}

    for activity in pipeline.activities:
        if activity.type != "Copy":
            continue
        inputs = [ref.name for ref in activity.inputs]
        outputs = [ref.name for ref in activity.outputs]

        if not inputs or not outputs:
            warnings.append(f"Copy '{activity.name}': missing input or output dataset; skipped.")
            continue
        if not activity.column_mappings:
            warnings.append(
                f"Copy '{activity.name}': no explicit column mapping (translator); "
                "column lineage cannot be derived."
            )
            continue

        for dataset in inputs:
            graph.register_stream(dataset, StreamKind.SOURCE.value)
            bindings[(dataset, "reader")] = None
        for dataset in outputs:
            graph.register_stream(dataset, StreamKind.SINK.value)
            bindings[(dataset, "writer")] = None

        for src_col, sink_col in activity.column_mappings:
            relationship = (
                RelationshipType.DIRECT if src_col == sink_col else RelationshipType.RENAME
            )
            for src_ds in inputs:
                graph.add_column(src_ds, src_col, NodeKind.SOURCE_COLUMN)
            for dst_ds in outputs:
                graph.add_column(dst_ds, sink_col, NodeKind.SINK_COLUMN)
            for src_ds in inputs:
                for dst_ds in outputs:
                    graph.add_dependency(
                        src_ds,
                        src_col,
                        dst_ds,
                        sink_col,
                        relationship=relationship,
                        transformation=activity.name,
                        transformation_type="Copy",
                    )

    return DataFlowLineage(
        data_flow=pipeline.name,
        graph=graph,
        warnings=tuple(warnings),
        kind="pipeline",
        bindings=tuple((stream, stream, role) for (stream, role) in bindings),
    )


def _nearest_data_flow_activities(
    name: str,
    by_name: dict[str, Activity],
    cache: dict[str, tuple[str, ...]],
    visiting: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Names of the closest upstream ``ExecuteDataFlow`` activities reachable via ``dependsOn``.

    Walks through any non-data-flow activities in between (e.g. a staging Copy
    or a stored procedure) so two data flows chained with a step in between are
    still reported as directly continuous.
    """
    if name in cache:
        return cache[name]
    if name in visiting:  # guard against a dependsOn cycle
        return ()
    activity = by_name.get(name)
    if activity is None:
        return ()
    found: list[str] = []
    for dep in activity.depends_on_names:
        dep_activity = by_name.get(dep)
        if dep_activity is None:
            continue
        if dep_activity.executes_data_flow and dep_activity.data_flow_reference:
            found.append(dep)
        else:
            found.extend(_nearest_data_flow_activities(dep, by_name, cache, visiting | {name}))
    result = tuple(dict.fromkeys(found))  # de-dup, preserve discovery order
    cache[name] = result
    return result


def build_data_flow_chain(pipeline: Pipeline) -> tuple[DataFlowChainStep, ...]:
    """Order a pipeline's ``ExecuteDataFlow`` activities by their ``dependsOn`` chain (F026).

    Answers "if this pipeline runs several data flows, in what order, and which
    ones feed into which": each step's ``upstream_data_flows`` lists the data
    flow(s) that must complete before it runs, so the UI can render continuity
    such as "DataFlow A -> DataFlow B -> DataFlow C" even when a Copy or stored
    procedure activity sits between two ``ExecuteDataFlow`` steps.
    """
    by_name = {a.name: a for a in pipeline.activities}
    cache: dict[str, tuple[str, ...]] = {}

    steps: list[DataFlowChainStep] = []
    order = 0
    for name in pipeline.execution_order().order:
        activity = by_name[name]
        if not activity.executes_data_flow or not activity.data_flow_reference:
            continue
        upstream_names = _nearest_data_flow_activities(name, by_name, cache)
        upstream_refs = tuple(
            ref for ref in (by_name[n].data_flow_reference for n in upstream_names) if ref
        )
        steps.append(
            DataFlowChainStep(
                activity_name=name,
                data_flow_reference=activity.data_flow_reference,
                order=order,
                upstream_data_flows=upstream_refs,
            )
        )
        order += 1
    return tuple(steps)
