"""Column identity model.

Placeholder for the lineage milestone (F008/F009). A column is identified by the
dataset / transformation it belongs to plus its name, enabling qualified search
(``customer.customer_id``).
"""

from __future__ import annotations

from app.models.common import ModelBase


class ColumnRef(ModelBase):
    """A fully-qualified reference to a column at a point in the pipeline."""

    #: Owning entity: a dataset name, source name, or transformation name.
    owner: str
    name: str

    @property
    def qualified(self) -> str:
        return f"{self.owner}.{self.name}"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.qualified
