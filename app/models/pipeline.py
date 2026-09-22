"""Normalized ADF pipeline model."""

from __future__ import annotations

from collections import deque

from pydantic import Field

from app.models.activity import Activity
from app.models.common import ModelBase


class PipelineParameter(ModelBase):
    name: str
    type: str = "String"
    default_value: object | None = None


class PipelineVariable(ModelBase):
    name: str
    type: str = "String"
    default_value: object | None = None


class Pipeline(ModelBase):
    """A normalized ADF pipeline definition."""

    name: str
    description: str | None = None
    folder: str | None = None
    concurrency: int | None = None
    annotations: tuple[str, ...] = ()
    parameters: tuple[PipelineParameter, ...] = ()
    variables: tuple[PipelineVariable, ...] = ()
    activities: tuple[Activity, ...] = ()

    # ------------------------------------------------------------------ counts
    @property
    def activity_count(self) -> int:
        return len(self.activities)

    @property
    def activity_types(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for activity in self.activities:
            counts[activity.type] = counts.get(activity.type, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def unrecognized_activities(self) -> tuple[Activity, ...]:
        return tuple(a for a in self.activities if not a.is_recognized)

    def activity(self, name: str) -> Activity | None:
        return next((a for a in self.activities if a.name == name), None)

    # ----------------------------------------------------------- dataset views
    @property
    def input_datasets(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for activity in self.activities:
            for ref in activity.inputs:
                seen.setdefault(ref.name, None)
        return tuple(seen)

    @property
    def output_datasets(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for activity in self.activities:
            for ref in activity.outputs:
                seen.setdefault(ref.name, None)
        return tuple(seen)

    @property
    def data_flow_references(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for activity in self.activities:
            if activity.data_flow_reference:
                seen.setdefault(activity.data_flow_reference, None)
        return tuple(seen)

    # -------------------------------------------------------- execution order
    def dependency_edges(self) -> tuple[tuple[str, str], ...]:
        """Return ``(upstream, downstream)`` pairs from ``dependsOn``."""
        names = {a.name for a in self.activities}
        edges: list[tuple[str, str]] = []
        for activity in self.activities:
            for dep in activity.depends_on_names:
                if dep in names:
                    edges.append((dep, activity.name))
        return tuple(edges)

    def execution_order(self) -> ExecutionOrder:
        """Kahn topological sort of activities by ``dependsOn``.

        Activities involved in a cycle are reported separately and appended in
        declaration order so processing can still proceed.
        """
        indegree: dict[str, int] = {a.name: 0 for a in self.activities}
        adjacency: dict[str, list[str]] = {a.name: [] for a in self.activities}
        for upstream, downstream in self.dependency_edges():
            adjacency[upstream].append(downstream)
            indegree[downstream] += 1

        queue: deque[str] = deque(a.name for a in self.activities if indegree[a.name] == 0)
        ordered: list[str] = []
        while queue:
            node = queue.popleft()
            ordered.append(node)
            for nxt in adjacency[node]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)

        cyclic = tuple(a.name for a in self.activities if a.name not in ordered)
        ordered.extend(cyclic)
        return ExecutionOrder(order=tuple(ordered), cyclic=cyclic)


class ExecutionOrder(ModelBase):
    """Result of :meth:`Pipeline.execution_order`."""

    order: tuple[str, ...] = ()
    cyclic: tuple[str, ...] = Field(default=())

    @property
    def has_cycle(self) -> bool:
        return bool(self.cyclic)
