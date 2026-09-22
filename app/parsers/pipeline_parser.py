"""Parse ADF Pipeline JSON into :class:`app.models.pipeline.Pipeline` (F003)."""

from __future__ import annotations

import re
from typing import Any

from app.core.exceptions import ParseError
from app.core.logging import get_logger
from app.models.activity import Activity, ActivityDependency
from app.models.common import DatasetReference
from app.models.pipeline import Pipeline, PipelineParameter, PipelineVariable
from app.parsers.base import ParseContext, scrub_secrets
from app.parsers.translator import parse_copy_mappings

logger = get_logger(__name__)

# Matches ARM names like: [concat(parameters('factoryName'), '/MyPipeline')]
_ARM_NAME_RE = re.compile(r"/([^'\"/\]]+)'?\)?\]?\s*$")


def _clean_name(raw_name: Any, ctx: ParseContext, *, fallback: str) -> str:
    if not isinstance(raw_name, str) or not raw_name.strip():
        ctx.warn(f"Artifact has no usable name; using '{fallback}'.")
        return fallback
    name = raw_name.strip()
    if name.startswith("[") and name.endswith("]"):
        match = _ARM_NAME_RE.search(name)
        if match:
            return match.group(1)
        ctx.warn(f"Could not fully resolve ARM expression name '{name}'; kept as-is.")
    return name


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value if isinstance(item, (str, int, float)))
    return ()


def _parse_parameters(block: Any, model: type) -> tuple[Any, ...]:
    if not isinstance(block, dict):
        return ()
    out = []
    for name, spec in block.items():
        spec = spec if isinstance(spec, dict) else {}
        out.append(
            model(
                name=str(name),
                type=str(spec.get("type", "String")),
                default_value=spec.get("defaultValue"),
            )
        )
    return tuple(out)


def _parse_dataset_refs(value: Any) -> tuple[DatasetReference, ...]:
    if not isinstance(value, list):
        return ()
    refs: list[DatasetReference] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = item.get("referenceName")
        if not isinstance(name, str):
            continue
        params = item.get("parameters")
        bound = {str(k): str(v) for k, v in params.items()} if isinstance(params, dict) else {}
        refs.append(DatasetReference(name=name, parameters=bound))
    return tuple(refs)


def _parse_depends_on(value: Any) -> tuple[ActivityDependency, ...]:
    if not isinstance(value, list):
        return ()
    deps: list[ActivityDependency] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        activity = item.get("activity")
        if not isinstance(activity, str):
            continue
        conditions = _as_str_tuple(item.get("dependencyConditions")) or ("Succeeded",)
        deps.append(ActivityDependency(activity=activity, conditions=conditions))
    return tuple(deps)


_KNOWN_ACTIVITY_TYPES = frozenset(
    {
        "Copy",
        "ExecuteDataFlow",
        "ExecutePipeline",
        "Lookup",
        "GetMetadata",
        "SetVariable",
        "AppendVariable",
        "Wait",
        "ForEach",
        "Until",
        "IfCondition",
        "Switch",
        "Filter",
        "WebActivity",
        "WebHook",
        "AzureFunctionActivity",
        "DatabricksNotebook",
        "SqlServerStoredProcedure",
        "Script",
        "Fail",
        "Validation",
        "Delete",
    }
)


def _parse_activity(raw: Any, ctx: ParseContext) -> Activity | None:
    if not isinstance(raw, dict):
        ctx.warn("Skipped an activity entry that was not an object.")
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        ctx.warn("Skipped an activity with no name.")
        return None
    act_type = raw.get("type")
    act_type = act_type if isinstance(act_type, str) and act_type.strip() else "Unknown"
    recognized = act_type in _KNOWN_ACTIVITY_TYPES
    if not recognized:
        ctx.warn(f"Activity '{name}' has unrecognized type '{act_type}'; kept with limited detail.")

    type_props_raw = raw.get("typeProperties")
    type_props = scrub_secrets(type_props_raw) if isinstance(type_props_raw, dict) else {}
    assert isinstance(type_props, dict)

    data_flow_ref: str | None = None
    df = type_props.get("dataflow") or type_props.get("dataFlow")
    if isinstance(df, dict) and isinstance(df.get("referenceName"), str):
        data_flow_ref = df["referenceName"]

    column_mappings = parse_copy_mappings(type_props) if act_type == "Copy" else ()

    return Activity(
        name=name.strip(),
        type=act_type,
        description=raw.get("description") if isinstance(raw.get("description"), str) else None,
        depends_on=_parse_depends_on(raw.get("dependsOn")),
        inputs=_parse_dataset_refs(raw.get("inputs")),
        outputs=_parse_dataset_refs(raw.get("outputs")),
        data_flow_reference=data_flow_ref,
        column_mappings=column_mappings,
        type_properties=type_props,
        is_recognized=recognized,
    )


class PipelineParser:
    """Converts a parsed ADF pipeline JSON object into a :class:`Pipeline`."""

    def parse(
        self,
        obj: dict[str, Any],
        *,
        source_filename: str = "upload.json",
        ctx: ParseContext | None = None,
    ) -> tuple[Pipeline, ParseContext]:
        ctx = ctx or ParseContext()
        fallback = source_filename.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "pipeline"
        name = _clean_name(obj.get("name"), ctx, fallback=fallback)

        properties = obj.get("properties")
        props: dict[str, Any] = properties if isinstance(properties, dict) else {}
        if not properties:
            # Some exports put activities at the top level.
            props = obj

        raw_activities = props.get("activities")
        if not isinstance(raw_activities, list):
            raw_activities = []
            ctx.warn("Pipeline has no 'activities' array; treating as empty.")

        activities = tuple(
            a for a in (_parse_activity(item, ctx) for item in raw_activities) if a is not None
        )

        folder = None
        folder_block = props.get("folder")
        if isinstance(folder_block, dict) and isinstance(folder_block.get("name"), str):
            folder = folder_block["name"]

        concurrency = props.get("concurrency")
        concurrency = concurrency if isinstance(concurrency, int) else None

        try:
            pipeline = Pipeline(
                name=name,
                description=props.get("description")
                if isinstance(props.get("description"), str)
                else None,
                folder=folder,
                concurrency=concurrency,
                annotations=_as_str_tuple(props.get("annotations")),
                parameters=_parse_parameters(props.get("parameters"), PipelineParameter),
                variables=_parse_parameters(props.get("variables"), PipelineVariable),
                activities=activities,
            )
        except Exception as exc:  # pragma: no cover - defensive
            raise ParseError(f"Failed to build pipeline model for '{name}': {exc}") from exc

        order = pipeline.execution_order()
        if order.has_cycle:
            ctx.warn("Circular activity dependency detected among: " + ", ".join(order.cyclic))
        return pipeline, ctx
