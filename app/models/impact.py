"""Column impact-analysis result model (F012)."""

from __future__ import annotations

from enum import StrEnum

from app.models.common import ModelBase


class ImpactLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def label(self) -> str:
        return self.value.upper()


class ImpactReport(ModelBase):
    """Deterministic downstream-impact assessment for one column.

    All collections hold lineage-graph node ids (``"<stream>::<column>"``) except
    the ``*_affected`` transformation lists, which hold stream (transformation)
    names.
    """

    node_id: str
    stream: str
    column: str

    direct_downstream: tuple[str, ...] = ()
    indirect_downstream: tuple[str, ...] = ()
    all_downstream: tuple[str, ...] = ()
    sink_columns: tuple[str, ...] = ()

    transformations_affected: tuple[str, ...] = ()
    joins_affected: tuple[str, ...] = ()
    filters_affected: tuple[str, ...] = ()
    splits_affected: tuple[str, ...] = ()
    aggregations_affected: tuple[str, ...] = ()
    computed_columns_affected: tuple[str, ...] = ()
    affected_streams: tuple[str, ...] = ()  # distinct downstream stream names (any kind)

    #: Node-id path (this column -> ... -> the riskiest reachable sink column),
    #: preferring paths that pass through a join/lookup/filter/split gate.
    #: Empty when there is no downstream at all.
    critical_path: tuple[str, ...] = ()

    score: int = 0
    level: ImpactLevel = ImpactLevel.LOW
    factors: tuple[str, ...] = ()

    @property
    def is_terminal(self) -> bool:
        """True when nothing depends on this column."""
        return not self.all_downstream

    @property
    def downstream_column_count(self) -> int:
        return len(self.all_downstream)

    @property
    def affected_stream_count(self) -> int:
        return len(self.affected_streams)

    def summary(self) -> str:
        if self.is_terminal:
            return f"{self.column}: no downstream dependencies (impact {self.level.label})."
        return (
            f"{self.column}: impact {self.level.label} (score {self.score}) - affects "
            f"{len(self.all_downstream)} downstream column(s) across "
            f"{len(self.affected_streams)} stream(s), "
            f"{len(self.sink_columns)} sink column(s), "
            f"{len(self.transformations_affected)} transformation(s)."
        )
