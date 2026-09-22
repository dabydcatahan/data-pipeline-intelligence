"""Container for a single parsed upload."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from app.models.common import ModelBase
from app.models.dataflow import DataFlow
from app.models.pipeline import Pipeline


class ArtifactKind(StrEnum):
    PIPELINE = "pipeline"
    MAPPING_DATA_FLOW = "mapping_data_flow"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {
            ArtifactKind.PIPELINE: "Pipeline",
            ArtifactKind.MAPPING_DATA_FLOW: "Mapping Data Flow",
            ArtifactKind.UNKNOWN: "Unknown / unsupported",
        }[self]


class ParsedArtifact(ModelBase):
    """The normalized result of parsing one uploaded ADF JSON file."""

    source_filename: str
    kind: ArtifactKind
    name: str
    content_sha256: str
    pipeline: Pipeline | None = None
    data_flow: DataFlow | None = None
    warnings: tuple[str, ...] = Field(default=())

    @property
    def is_supported(self) -> bool:
        return self.kind is not ArtifactKind.UNKNOWN

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings)
