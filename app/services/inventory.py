"""Cross-artifact discovery: sources, sinks, transformations, datasets (F005-F007).

Pure aggregation over the normalized models - given the parsed pipelines and
data flows in a session, produce flat, UI-ready inventory tables.

Linked-service names and dataset formats are *not* inventoried: they live in
Dataset / LinkedService JSON, which this tool classifies as "unknown" and does
not parse (and connection details must never be surfaced anyway).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.dataflow import DataFlow
from app.models.pipeline import Pipeline


@dataclass(frozen=True)
class SourceEntry:
    artifact: str  # "Data flow: X" or "Pipeline: Y / activity Z"
    name: str
    dataset: str
    column_count: int


@dataclass(frozen=True)
class SinkEntry:
    artifact: str
    name: str
    dataset: str
    column_count: int


@dataclass(frozen=True)
class TransformationEntry:
    artifact: str
    name: str
    kind: str
    column_count: int
    recognized: bool


@dataclass(frozen=True)
class DatasetEntry:
    name: str
    read_by: tuple[str, ...]
    written_by: tuple[str, ...]
    column_count: int


@dataclass(frozen=True)
class Inventory:
    sources: tuple[SourceEntry, ...] = ()
    sinks: tuple[SinkEntry, ...] = ()
    transformations: tuple[TransformationEntry, ...] = ()
    datasets: tuple[DatasetEntry, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.sources or self.sinks or self.transformations)


class _DatasetAccumulator:
    def __init__(self) -> None:
        self.readers: dict[str, set[str]] = {}
        self.writers: dict[str, set[str]] = {}
        self.columns: dict[str, int] = {}

    def read(self, dataset: str, artifact: str, columns: int = 0) -> None:
        self.readers.setdefault(dataset, set()).add(artifact)
        self.columns[dataset] = max(self.columns.get(dataset, 0), columns)

    def write(self, dataset: str, artifact: str, columns: int = 0) -> None:
        self.writers.setdefault(dataset, set()).add(artifact)
        self.columns[dataset] = max(self.columns.get(dataset, 0), columns)

    def entries(self) -> tuple[DatasetEntry, ...]:
        names = sorted(set(self.readers) | set(self.writers) | set(self.columns))
        return tuple(
            DatasetEntry(
                name=name,
                read_by=tuple(sorted(self.readers.get(name, set()))),
                written_by=tuple(sorted(self.writers.get(name, set()))),
                column_count=self.columns.get(name, 0),
            )
            for name in names
        )


def build_inventory(pipelines: list[Pipeline], data_flows: list[DataFlow]) -> Inventory:
    sources: list[SourceEntry] = []
    sinks: list[SinkEntry] = []
    transformations: list[TransformationEntry] = []
    datasets = _DatasetAccumulator()

    for pipeline in pipelines:
        label = f"Pipeline: {pipeline.name}"
        for activity in pipeline.activities:
            transformations.append(
                TransformationEntry(
                    artifact=label,
                    name=activity.name,
                    kind=activity.type,
                    column_count=len(activity.column_mappings),
                    recognized=activity.is_recognized,
                )
            )
            if activity.type != "Copy":
                continue
            cols = len(activity.column_mappings)
            for ref in activity.inputs:
                sources.append(
                    SourceEntry(
                        artifact=f"{label} / {activity.name}",
                        name=ref.name,
                        dataset=ref.name,
                        column_count=cols,
                    )
                )
                datasets.read(ref.name, label, cols)
            for ref in activity.outputs:
                sinks.append(
                    SinkEntry(
                        artifact=f"{label} / {activity.name}",
                        name=ref.name,
                        dataset=ref.name,
                        column_count=cols,
                    )
                )
                datasets.write(ref.name, label, cols)

    for data_flow in data_flows:
        label = f"Data flow: {data_flow.name}"
        for source in data_flow.sources:
            dataset = source.dataset.name if source.dataset else "(inline)"
            cols = len(source.schema_columns)
            sources.append(
                SourceEntry(artifact=label, name=source.name, dataset=dataset, column_count=cols)
            )
            if source.dataset:
                datasets.read(dataset, label, cols)
        for sink in data_flow.sinks:
            dataset = sink.dataset.name if sink.dataset else "(inline)"
            sinks.append(SinkEntry(artifact=label, name=sink.name, dataset=dataset, column_count=0))
            if sink.dataset:
                datasets.write(dataset, label, 0)
        for transformation in data_flow.transformations:
            transformations.append(
                TransformationEntry(
                    artifact=label,
                    name=transformation.name,
                    kind=transformation.kind or "unknown",
                    column_count=len(transformation.columns),
                    recognized=transformation.is_recognized,
                )
            )

    return Inventory(
        sources=tuple(sources),
        sinks=tuple(sinks),
        transformations=tuple(transformations),
        datasets=datasets.entries(),
    )
