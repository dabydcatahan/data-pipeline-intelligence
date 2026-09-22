"""Plain-English vocabulary for lineage concepts, shared by insights + documentation.

Both ``app.insights`` (AI-adjacent explanations) and ``app.documentation``
(generated Markdown) need to describe the same underlying facts - relationship
types, data-flow operators, impact levels - to non-technical readers. Keeping
the wording here avoids drift between the two and keeps jargon out of the
generated prose while the rest of the codebase can still use the precise
technical labels (:attr:`~app.models.lineage.RelationshipType.label`, etc.).
"""

from __future__ import annotations

from app.models.impact import ImpactLevel
from app.models.lineage import RelationshipType

#: How a downstream column was produced from an upstream one, in plain words.
#: Phrased so it reads naturally as "<column> is {phrase} <other column>".
RELATIONSHIP_PLAIN: dict[RelationshipType, str] = {
    RelationshipType.DIRECT: "copied over as-is from",
    RelationshipType.PASSTHROUGH: "passed through unchanged from",
    RelationshipType.RENAME: "renamed from",
    RelationshipType.DERIVED: "calculated from",
    RelationshipType.AGGREGATE: "summarized (e.g. totaled or counted) from",
    RelationshipType.JOIN_KEY: "matched up with",
    RelationshipType.LOOKUP: "looked up from",
    RelationshipType.FILTER: "used to decide which rows to keep, based on",
    RelationshipType.CONDITIONAL_SPLIT: "used to route rows, based on",
    RelationshipType.UNION: "combined (stacked) with",
    RelationshipType.WINDOW: "calculated across a group of rows using",
    RelationshipType.UNKNOWN: "connected to",
}

#: What a Mapping Data Flow transformation *does*, in plain words, keyed by
#: the lowercase operator name recognized in ``app.models.transformation``.
OPERATOR_PLAIN: dict[str, str] = {
    "source": "reads data in",
    "sink": "writes the final result out",
    "derive": "calculates new value(s)",
    "select": "renames, reorders or drops column(s)",
    "join": "combines data from two sources by matching a key",
    "lookup": "looks up extra details from another source",
    "exists": "checks whether a matching row exists in another source",
    "filter": "keeps only the rows that meet a condition",
    "aggregate": "groups rows and summarizes them (e.g. totals, counts, averages)",
    "union": "stacks rows from multiple sources together",
    "sort": "puts rows in a specific order",
    "window": "calculates running totals, ranks or averages across groups of rows",
    "flatten": "expands nested or list-style data into separate rows",
    "pivot": "turns row values into new columns",
    "unpivot": "turns columns into rows",
    "alterrow": "marks rows to be inserted, updated, deleted or upserted",
    "split": "routes rows down different paths based on a condition",
    "conditionalsplit": "routes rows down different paths based on a condition",
    "parse": "reads structured text (e.g. JSON) out of a column",
    "foldDown": "expands array/object data into separate rows",
    "wrangling": "applies spreadsheet-style data prep steps",
}

#: Impact level -> plain-English risk explanation for a non-technical reader.
IMPACT_PLAIN: dict[ImpactLevel, str] = {
    ImpactLevel.LOW: "Low risk: changing it is unlikely to break anything else.",
    ImpactLevel.MEDIUM: (
        "Medium risk: a handful of other columns or steps depend on it, so double-check "
        "before changing it."
    ),
    ImpactLevel.HIGH: (
        "High risk: many other columns, steps or final reports depend on it - changing it "
        "without care could break things downstream."
    ),
}


def relationship_plain(relationship: RelationshipType) -> str:
    """Plain-English phrase for how a column depends on another (falls back safely)."""
    return RELATIONSHIP_PLAIN.get(relationship, "connected to")


def operator_plain(operator: str | None) -> str:
    """Plain-English phrase for what a data-flow transformation operator does."""
    if not operator:
        return "processes the data"
    return OPERATOR_PLAIN.get(operator.lower(), "processes the data in a specialized way")


def impact_plain(level: ImpactLevel) -> str:
    """Plain-English explanation of an impact level."""
    return IMPACT_PLAIN.get(level, IMPACT_PLAIN[ImpactLevel.LOW])
