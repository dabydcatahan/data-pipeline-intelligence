"""Normalized ADF pipeline activity model."""

from __future__ import annotations

from pydantic import Field

from app.models.common import DatasetReference, ModelBase

#: Activity ``type`` values that execute a Mapping Data Flow.
EXECUTE_DATA_FLOW_TYPES = frozenset({"ExecuteDataFlow"})

#: Activity ``type`` values that contain nested activities (control flow).
CONTAINER_ACTIVITY_TYPES = frozenset({"ForEach", "Until", "If", "IfCondition", "Switch"})


class ActivityDependency(ModelBase):
    """A ``dependsOn`` edge between two activities."""

    activity: str
    conditions: tuple[str, ...] = ("Succeeded",)


class Activity(ModelBase):
    """A single activity within a pipeline.

    ``type_properties`` retains the parsed (secret-scrubbed) ``typeProperties``
    block so downstream engines can inspect activity-specific details without
    re-reading raw JSON. It never contains linked-service credentials.
    """

    name: str
    type: str
    description: str | None = None
    depends_on: tuple[ActivityDependency, ...] = ()
    inputs: tuple[DatasetReference, ...] = ()
    outputs: tuple[DatasetReference, ...] = ()
    data_flow_reference: str | None = None
    #: ``(source_column, sink_column)`` pairs from a Copy activity translator (F008).
    column_mappings: tuple[tuple[str, str], ...] = ()
    type_properties: dict[str, object] = Field(default_factory=dict)
    is_recognized: bool = True

    @property
    def executes_data_flow(self) -> bool:
        return self.type in EXECUTE_DATA_FLOW_TYPES

    @property
    def is_container(self) -> bool:
        return self.type in CONTAINER_ACTIVITY_TYPES

    @property
    def depends_on_names(self) -> tuple[str, ...]:
        return tuple(dep.activity for dep in self.depends_on)
