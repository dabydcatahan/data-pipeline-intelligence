"""Deterministic Markdown documentation generation (F013 / F014).

Docs are built purely from the normalized models (`app.models`) and, optionally,
a :class:`~app.lineage.column_lineage.DataFlowLineage`. No template dependency -
plain string assembly - so output is stable and diff-friendly. Because the
generator never touches raw ADF JSON, scrubbed ``typeProperties`` (connection
strings, keys, …) cannot leak into a document.

HTML export builds on this Markdown and is handled by F018.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.lineage.column_lineage import DataFlowLineage
from app.lineage.plain_language import operator_plain, relationship_plain
from app.lineage.traversal import explain
from app.models.dataflow import DataFlow
from app.models.lineage import ColumnJourney
from app.models.pipeline import ExecutionOrder, Pipeline

_NONE = "_none_"


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip() or "—"


def _md_table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    rows = list(rows)
    if not rows:
        return "_none_\n"
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(_cell(c) for c in row) + " |" for row in rows)
    return "\n".join(lines) + "\n"


def _joined(values: Iterable[str]) -> str:
    items = [v for v in values if v]
    return ", ".join(items) if items else _NONE


def _pipeline_plain_summary(pipeline: Pipeline, order: ExecutionOrder) -> list[str]:
    """Non-technical, bullet-point explanation of what this pipeline does."""
    lines = [
        f"- This pipeline performs **{pipeline.activity_count} step(s)** to move and process data.",
    ]
    if order.order:
        lines.append("- It runs the steps in this order: " + " then ".join(order.order) + ".")
    if pipeline.data_flow_references:
        lines.append(
            f"- It runs {len(pipeline.data_flow_references)} data transformation job(s) "
            '(called "data flows"): ' + _joined(pipeline.data_flow_references) + "."
        )
    if pipeline.input_datasets or pipeline.output_datasets:
        lines.append(
            "- Data comes from: "
            + _joined(pipeline.input_datasets)
            + "; and ends up in: "
            + _joined(pipeline.output_datasets)
            + "."
        )
    if order.has_cycle:
        lines.append(
            "- ⚠️ Some steps depend on each other in a loop, so there isn't one clear order — "
            "this should be fixed before relying on this pipeline."
        )
    if not pipeline.annotations:
        lines.append("- No extra notes or description are attached beyond what's shown here.")
    return lines


def _data_flow_plain_summary(data_flow: DataFlow) -> list[str]:
    """Non-technical, bullet-point explanation of what this data flow does."""
    lines = [
        f"- This data flow reads data in from **{data_flow.source_count} source(s)**, "
        f"applies **{data_flow.transformation_count} processing step(s)**, and writes the "
        f'result out to **{data_flow.sink_count} destination(s)** (called "sinks").',
    ]
    step_bits = [f"{t.name} ({operator_plain(t.kind)})" for t in data_flow.transformations]
    if step_bits:
        lines.append("- Step by step, it: " + "; ".join(step_bits) + ".")
    if data_flow.unrecognized_operators:
        lines.append(
            "- ⚠️ Some steps use techniques this tool doesn't fully understand yet, so their "
            "effect on the data can't be fully explained: "
            + _joined(data_flow.unrecognized_operators)
            + "."
        )
    return lines


def _plain_narrative(journey: ColumnJourney) -> str:
    """Full-sentence, jargon-light walk from origin(s) to this column and onward."""
    sentences: list[str] = []
    for step in journey.upstream_steps:
        if step.is_origin:
            sentences.append(
                f"- **{step.stream}.{step.column}** is where this data starts (a source column)."
            )
            continue
        for edge in step.inputs:
            src_stream, _, src_col = edge.source.partition("::")
            verb = relationship_plain(edge.relationship)
            detail = f" (using: `{edge.expression}`)" if edge.expression else ""
            sentences.append(
                f"- **{step.stream}.{step.column}** is {verb} **{src_stream}.{src_col}**{detail}."
            )
    if journey.sink_columns:
        sentences.append(
            "- It ends up in the final output column(s): "
            + ", ".join(f"**{s.replace('::', '.')}**" for s in journey.sink_columns)
            + "."
        )
    return "\n".join(sentences) if sentences else "_No lineage steps recorded._"


# --------------------------------------------------------------------------- pipeline
def pipeline_markdown(pipeline: Pipeline) -> str:
    order = pipeline.execution_order()
    out: list[str] = [f"# Pipeline: {pipeline.name}", ""]
    if pipeline.description:
        out += [pipeline.description, ""]

    out += ["## Plain-English Summary", ""]
    out += _pipeline_plain_summary(pipeline, order)
    out += [""]

    out += ["## Overview", ""]
    out += [
        f"- **Folder:** {pipeline.folder or _NONE}",
        f"- **Concurrency:** {pipeline.concurrency if pipeline.concurrency is not None else _NONE}",
        f"- **Annotations:** {_joined(pipeline.annotations)}",
        f"- **Activities:** {pipeline.activity_count}",
        "",
    ]
    out += ["### Parameters", ""]
    out += [
        _md_table(
            ["Name", "Type", "Default"],
            [(p.name, p.type, p.default_value) for p in pipeline.parameters],
        ),
    ]
    out += ["### Variables", ""]
    out += [
        _md_table(
            ["Name", "Type", "Default"],
            [(v.name, v.type, v.default_value) for v in pipeline.variables],
        ),
    ]

    out += ["## Execution Flow", ""]
    if order.order:
        out += [" → ".join(order.order), ""]
    if order.has_cycle:
        out += [f"> ⚠️ Circular dependency among: {', '.join(order.cyclic)}", ""]
    out += [
        "### Dependencies",
        "",
        _md_table(
            ["Upstream", "Downstream"],
            list(pipeline.dependency_edges()),
        ),
    ]

    out += ["## Activities", ""]
    out += [
        _md_table(
            ["Name", "Type", "Container", "Depends on", "Inputs", "Outputs", "Data flow"],
            [
                (
                    a.name,
                    a.type + ("" if a.is_recognized else " (unrecognized)"),
                    f"{a.parent_activity} ({a.container_branch})" if a.is_nested else "—",
                    _joined(a.depends_on_names),
                    _joined(r.name for r in a.inputs),
                    _joined(r.name for r in a.outputs),
                    a.data_flow_reference or "—",
                )
                for a in pipeline.activities
            ],
        ),
    ]

    out += ["## Data Sources", "", _joined(pipeline.input_datasets), ""]
    out += ["## Data Targets", "", _joined(pipeline.output_datasets), ""]
    out += ["## Referenced Data Flows", "", _joined(pipeline.data_flow_references), ""]
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------- data flow
def data_flow_markdown(data_flow: DataFlow, lineage: DataFlowLineage | None = None) -> str:
    out: list[str] = [f"# Mapping Data Flow: {data_flow.name}", ""]
    if data_flow.description:
        out += [data_flow.description, ""]

    out += ["## Plain-English Summary", ""]
    out += _data_flow_plain_summary(data_flow)
    out += [""]

    out += ["## Sources", ""]
    out += [
        _md_table(
            ["Source", "Dataset", "Schema columns"],
            [
                (s.name, s.dataset.name if s.dataset else "—", _joined(s.schema_columns))
                for s in data_flow.sources
            ],
        ),
    ]

    out += ["## Sinks", ""]
    out += [
        _md_table(
            ["Sink", "Dataset", "Fed by"],
            [
                (s.name, s.dataset.name if s.dataset else "—", s.input_stream or "—")
                for s in data_flow.sinks
            ],
        ),
    ]

    out += ["## Transformations", ""]
    for transformation in data_flow.transformations:
        title = f"{transformation.name} ({transformation.kind or 'unknown'})"
        if not transformation.is_recognized:
            title += " — unrecognized operator"
        out += [f"### {title}", ""]
        out += [f"- **Inputs:** {_joined(transformation.input_streams)}"]
        if transformation.group_by:
            out += [f"- **Group by:** {_joined(transformation.group_by)}"]
        if transformation.raw_arguments:
            out += ["", "```", transformation.raw_arguments, "```"]
        if transformation.columns:
            out += [
                "",
                "**Column mapping**",
                "",
                _md_table(
                    ["Target", "Kind", "From", "Confidence", "Expression"],
                    [
                        (
                            c.target,
                            c.kind,
                            _joined(c.referenced_columns),
                            c.analysis_confidence,
                            c.expression or "—",
                        )
                        for c in transformation.columns
                    ],
                ),
            ]
        out += [""]

    if lineage is not None:
        out += _lineage_section(lineage)

    warnings = list(lineage.warnings) if lineage is not None else []
    if warnings:
        out += ["## Warnings", ""]
        out += [f"- {w}" for w in warnings]
        out += [""]

    return "\n".join(out).rstrip() + "\n"


def _lineage_section(lineage: DataFlowLineage) -> list[str]:
    graph = lineage.graph
    sink_nodes = [n.id for n in graph.iter_nodes() if n.kind.value == "sink_column"]
    out: list[str] = ["## Column Lineage", ""]
    if not sink_nodes:
        out += ["_No sink columns resolved._", ""]
        return out

    out += [
        "This shows where the data in each final output column came from, and what happened "
        "to it along the way.",
        "",
    ]

    rows = []
    for node_id in sink_nodes:
        journey = explain(graph, node_id)
        rows.append(
            (
                f"{journey.stream}.{journey.column}",
                _joined(o.replace("::", ".") for o in journey.origins),
                _joined(journey.transformations),
            )
        )
    out += [_md_table(["Sink column", "Originating source column(s)", "Transformations"], rows)]

    out += ["", "### Detailed paths", ""]
    for node_id in sink_nodes:
        journey = explain(graph, node_id)
        out += [f"**{journey.stream}.{journey.column}**", ""]
        out += [_plain_narrative(journey), ""]
        out += ["#### Technical trace", "", "```", journey.narrative(), "```", ""]
    return out


# --------------------------------------------------------------------------- dispatch
def document_pipeline(pipeline: Pipeline) -> str:
    return pipeline_markdown(pipeline)


def document_data_flow(data_flow: DataFlow, lineage: DataFlowLineage | None = None) -> str:
    return data_flow_markdown(data_flow, lineage)
