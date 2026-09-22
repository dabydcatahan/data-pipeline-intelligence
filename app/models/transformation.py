"""Transformation-level model.

Placeholder for the lineage milestone (F007/F008). The Mapping Data Flow parser
will populate rich per-transformation column mapping here; kept as a stub now so
imports and the package layout are stable.
"""

from __future__ import annotations

from app.models.common import ModelBase


class ColumnMapping(ModelBase):
    """A single source-column -> target-column relationship inside a transformation."""

    target: str
    sources: tuple[str, ...] = ()
    expression: str | None = None
    relationship: str = "direct"  # direct | rename | derived | aggregate | join | ...


class Transformation(ModelBase):
    """Normalized transformation with resolved column mappings (F007/F008)."""

    name: str
    kind: str
    inputs: tuple[str, ...] = ()
    mappings: tuple[ColumnMapping, ...] = ()
