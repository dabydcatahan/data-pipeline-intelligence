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


def _parse_activity(
    raw: Any,
    ctx: ParseContext,
    *,
    parent: str | None = None,
    branch: str | None = None,
) -> Activity | None:
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
        parent_activity=parent,
        container_branch=branch,
    )


#: For each container activity type, the ``typeProperties`` keys holding a flat
#: nested-activities array, and the branch label to tag them with (F027).
_CONTAINER_ACTIVITY_LISTS: dict[str, tuple[tuple[str, str], ...]] = {
    "ForEach": (("activities", "loop"),),
    "Until": (("activities", "loop"),),
    "IfCondition": (("ifTrueActivities", "true"), ("ifFalseActivities", "false")),
    "If": (("ifTrueActivities", "true"), ("ifFalseActivities", "false")),
}


def _parse_activities(
    raw_activities: list[Any],
    ctx: ParseContext,
    *,
    parent: str | None = None,
    branch: str | None = None,
    seen_names: set[str] | None = None,
) -> list[Activity]:
    """Recursively flatten container activities (ForEach/Until/If/Switch) (F027).

    ADF requires activity names to be unique across the whole pipeline (even
    across nested scopes), so a single flat list - each entry tagged with its
    ``parent_activity``/``container_branch`` - lets every existing consumer of
    ``Pipeline.activities`` (lineage, documentation, inventory) see nested Copy
    and ExecuteDataFlow activities without any special-casing.
    """
    seen_names = seen_names if seen_names is not None else set()
    out: list[Activity] = []
    for item in raw_activities:
        activity = _parse_activity(item, ctx, parent=parent, branch=branch)
        if activity is None:
            continue
        if activity.name in seen_names:
            ctx.warn(
                f"Activity '{activity.name}' name is used more than once in the pipeline "
                "(possibly across nested scopes); later occurrence kept, lineage/dependency "
                "links may be ambiguous."
            )
        seen_names.add(activity.name)
        out.append(activity)

        if not isinstance(item, dict):
            continue
        type_props_raw = item.get("typeProperties")
        type_props = type_props_raw if isinstance(type_props_raw, dict) else {}

        for key, child_branch in _CONTAINER_ACTIVITY_LISTS.get(activity.type, ()):
            nested = type_props.get(key)
            if isinstance(nested, list) and nested:
                out.extend(
                    _parse_activities(
                        nested,
                        ctx,
                        parent=activity.name,
                        branch=child_branch,
                        seen_names=seen_names,
                    )
                )

        if activity.type == "Switch":
            cases = type_props.get("cases")
            if isinstance(cases, list):
                for case in cases:
                    if not isinstance(case, dict):
                        continue
                    case_activities = case.get("activities")
                    if not isinstance(case_activities, list) or not case_activities:
                        continue
                    value = case.get("value")
                    label = f"case:{value}" if isinstance(value, str) else "case"
                    out.extend(
                        _parse_activities(
                            case_activities,
                            ctx,
                            parent=activity.name,
                            branch=label,
                            seen_names=seen_names,
                        )
                    )
            default_activities = type_props.get("defaultActivities")
            if isinstance(default_activities, list) and default_activities:
                out.extend(
                    _parse_activities(
                        default_activities,
                        ctx,
                        parent=activity.name,
                        branch="default",
                        seen_names=seen_names,
                    )
                )
    return out


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

        activities = tuple(_parse_activities(raw_activities, ctx))

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
