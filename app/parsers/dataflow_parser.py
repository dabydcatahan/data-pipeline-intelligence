"""Parse ADF Mapping Data Flow JSON into :class:`app.models.dataflow.DataFlow` (F004).

Top-level structure comes from ``typeProperties`` (sources / sinks /
transformations). The transformation *logic* comes from the Data Flow Script in
``typeProperties.scriptLines`` (see :mod:`app.parsers.dfs_script`).
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.models.common import DatasetReference
from app.models.dataflow import (
    DataFlow,
    DataFlowSink,
    DataFlowSource,
    DataFlowTransformation,
)
from app.parsers.base import ParseContext, scrub_secrets
from app.parsers.dfs_script import extract_columns, parse_script

logger = get_logger(__name__)


def _dataset_ref(item: dict[str, Any]) -> DatasetReference | None:
    dataset = item.get("dataset")
    if isinstance(dataset, dict) and isinstance(dataset.get("referenceName"), str):
        return DatasetReference(name=dataset["referenceName"])
    return None


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


class DataFlowParser:
    """Converts a parsed Mapping Data Flow JSON object into a :class:`DataFlow`."""

    def parse(
        self,
        obj: dict[str, Any],
        *,
        source_filename: str = "upload.json",
        ctx: ParseContext | None = None,
    ) -> tuple[DataFlow, ParseContext]:
        ctx = ctx or ParseContext()
        fallback = source_filename.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "dataflow"
        raw_name = obj.get("name")
        name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else fallback

        properties = obj.get("properties")
        props: dict[str, Any] = properties if isinstance(properties, dict) else {}
        type_props = props.get("typeProperties")
        # Data flow source/sink typeProperties don't carry secrets in ADF's own
        # schema (credentials live in a separately referenced, never-parsed linked
        # service), but scrub defensively anyway for consistency with
        # pipeline_parser.py, in case a future field read adds one without noticing.
        type_props = scrub_secrets(type_props) if isinstance(type_props, dict) else None
        tp: dict[str, Any] = type_props if isinstance(type_props, dict) else {}

        raw_sources = _dicts(tp.get("sources"))
        raw_sinks = _dicts(tp.get("sinks"))
        raw_transforms = _dicts(tp.get("transformations"))
        script_lines = tuple(
            str(line) for line in tp.get("scriptLines", []) if isinstance(line, str)
        )

        statements = tuple(parse_script("\n".join(script_lines)))
        by_stream = {st.output_stream: st for st in statements if st.output_stream}

        # --- sources -------------------------------------------------------
        sources: list[DataFlowSource] = []
        for index, item in enumerate(raw_sources):
            src_name = str(item.get("name", f"source{index}"))
            statement = by_stream.get(src_name)
            schema: tuple[str, ...] = ()
            if statement is not None and statement.operator == "source":
                cols, _ = extract_columns("source", statement.raw_arguments)
                schema = tuple(c.target for c in cols)
            sources.append(
                DataFlowSource(
                    name=src_name,
                    dataset=_dataset_ref(item),
                    linked_service_only=("linkedService" in item and "dataset" not in item),
                    schema_columns=schema,
                )
            )
        source_names = {s.name for s in sources}

        # --- sinks -------------------------------------------------------
        sinks: list[DataFlowSink] = []
        for index, item in enumerate(raw_sinks):
            sink_name = str(item.get("name", f"sink{index}"))
            statement = by_stream.get(sink_name)
            sinks.append(
                DataFlowSink(
                    name=sink_name,
                    dataset=_dataset_ref(item),
                    input_stream=(
                        statement.input_streams[0]
                        if statement is not None and statement.input_streams
                        else None
                    ),
                )
            )
        sink_names = {s.name for s in sinks}

        # --- transformations (driven by the script, in script order) -----
        declared = {str(item.get("name", f"transform{i}")) for i, item in enumerate(raw_transforms)}
        transformations: list[DataFlowTransformation] = []
        for statement in statements:
            if not statement.output_stream:
                ctx.warn(f"Data flow '{name}': skipped a script statement with no output stream.")
                continue
            if statement.operator in {"source", "sink"}:
                continue
            if statement.output_stream in source_names or statement.output_stream in sink_names:
                continue
            columns, group_by = extract_columns(statement.operator, statement.raw_arguments)
            if not statement.is_recognized:
                ctx.warn(
                    f"Data flow '{name}': transformation '{statement.output_stream}' uses "
                    f"the operator '{statement.operator or '?'}', which this tool does not "
                    "decompose yet. The step is kept and its columns are treated as "
                    "passing through unchanged (lineage through it is approximate)."
                )
            transformations.append(
                DataFlowTransformation(
                    name=statement.output_stream,
                    kind=statement.operator or None,
                    input_streams=statement.input_streams,
                    raw_arguments=statement.raw_arguments,
                    raw_script=statement.raw_text,
                    columns=columns,
                    group_by=group_by,
                    is_recognized=statement.is_recognized,
                )
            )

        seen = {t.name for t in transformations}
        for declared_name in sorted(declared - seen - source_names - sink_names):
            transformations.append(DataFlowTransformation(name=declared_name))
            ctx.warn(
                f"Data flow '{name}': transformation '{declared_name}' has no script "
                "statement; recorded by name only."
            )

        if not script_lines:
            ctx.warn(f"Data flow '{name}' has no scriptLines; transformation logic is unavailable.")
        if not sources and not sinks:
            ctx.warn(f"Data flow '{name}' has no recognizable sources or sinks.")

        data_flow = DataFlow(
            name=name,
            description=props.get("description")
            if isinstance(props.get("description"), str)
            else None,
            annotations=tuple(str(a) for a in props.get("annotations", []) if isinstance(a, str)),
            sources=tuple(sources),
            sinks=tuple(sinks),
            transformations=tuple(transformations),
            statements=statements,
            script_lines=script_lines,
        )
        return data_flow, ctx
