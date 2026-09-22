"""Small shared model types."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ModelBase(BaseModel):
    """Base for all internal models: immutable, forbids unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class DatasetReference(ModelBase):
    """A reference from an activity to an ADF dataset.

    We deliberately keep only the logical name and any bound parameters; linked
    services, connection strings and credentials are never carried here.
    """

    name: str
    parameters: dict[str, str] = Field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name
