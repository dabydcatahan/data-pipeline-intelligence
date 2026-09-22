"""Offline, deterministic insight provider (F017).

Turns the sanitized ``request.facts`` into templated prose written for a
**non-technical reader**: plain sentences first, with technical terms (join,
sink, derive, ...) only introduced alongside a short explanation. No
randomness, no network, no dependency - always available and the default. It
never states anything not already present in the facts.
"""

from __future__ import annotations

from typing import Any

from app.insights.interface import InsightProvider, InsightRequest, InsightResult
from app.lineage.plain_language import operator_plain

_GENERIC_NAMES = ("transform", "select", "derive", "new", "output")


def _oxford(items: list[str], limit: int = 6) -> str:
    items = [str(i) for i in items]
    if not items:
        return "none"
    shown = items[:limit]
    suffix = f", and {len(items) - limit} more" if len(items) > limit else ""
    return ", ".join(shown) + suffix


class DeterministicInsightProvider(InsightProvider):
    name = "deterministic"

    def generate(self, request: InsightRequest) -> InsightResult:
        kind = request.facts.get("kind")
        if kind == "pipeline":
            return self._pipeline(request.facts)
        if kind == "data_flow":
            return self._data_flow(request.facts)
        if kind == "column":
            return self._column(request.facts)
        return InsightResult(
            summary="No deterministic template is available for this subject.",
            provider=self.name,
        )

    # ------------------------------------------------------------- pipeline
    def _pipeline(self, f: dict[str, Any]) -> InsightResult:
        order = " then ".join(f["execution_order"]) or "no defined order"
        summary = (
            f"**{f['name']}** is a pipeline (a sequence of automated steps) made up of "
            f"{f['activity_count']} step(s). It runs them in this order: {order}."
        )
        refs = list(f["data_flow_references"])
        if refs:
            noun = "data transformation job" if len(refs) == 1 else "data transformation jobs"
            summary += (
                f' Along the way it runs {len(refs)} {noun} (called "data flows"): {_oxford(refs)}.'
            )

        observations = [
            f"Data comes in from {len(f['input_datasets'])} place(s) and is written out to "
            f"{len(f['output_datasets'])} place(s).",
            f'There are {f["dependency_edge_count"]} explicit "do this after that" rule(s) '
            "between steps.",
        ]
        if not f["annotations"]:
            observations.append(
                "No labels/notes (annotations) are attached, so there's no extra context "
                "for someone new to this pipeline."
            )

        risks: list[str] = []
        if f["has_cycle"]:
            risks.append(
                f"Circular dependency: {_oxford(f['cyclic_activities'])} depend on each other "
                "in a loop, so there's no clear starting point - this usually needs to be "
                "fixed before the pipeline can be trusted to run correctly."
            )
        if f["unrecognized_activities"]:
            risks.append(
                f"{len(f['unrecognized_activities'])} step type(s) aren't recognized by this "
                f"tool yet ({_oxford(f['unrecognized_activities'])}), so we can't explain what "
                "they do."
            )

        recommendations: list[str] = []
        if not f["annotations"]:
            recommendations.append(
                "Add a short description or notes so others understand this pipeline's "
                "purpose at a glance."
            )
        if f["unrecognized_activities"]:
            recommendations.append(
                "Ask someone familiar with the unrecognized step type(s) to document what they do."
            )
        if refs:
            recommendations.append(
                "Generate documentation for each referenced data flow to see the full "
                "picture, step by step."
            )
        return InsightResult(
            summary=summary,
            observations=tuple(observations),
            risks=tuple(risks),
            recommendations=tuple(recommendations),
            provider=self.name,
        )

    # ------------------------------------------------------------ data flow
    def _data_flow(self, f: dict[str, Any]) -> InsightResult:
        step_descriptions = [
            f"{operator_plain(op)} ({op}, {count}x)" for op, count in dict(f["operators"]).items()
        ]
        summary = (
            f"Data flow **{f['name']}** is a data transformation job: it reads data in from "
            f"{f['source_count']} source(s), applies {f['transformation_count']} processing "
            f"step(s), and writes the result out to {f['sink_count']} destination(s) "
            '("sinks").'
        )
        if step_descriptions:
            summary += " In plain terms, it: " + "; ".join(step_descriptions) + "."
        if "column_node_count" in f:
            summary += f" In total, {f['column_node_count']} column(s) are tracked end to end."

        schemaless = [s["name"] for s in f["sources"] if not s["has_schema"]]
        observations: list[str] = []
        if "edge_relationship_counts" in f:
            rels = ", ".join(f"{k}: {v}" for k, v in dict(f["edge_relationship_counts"]).items())
            observations.append(f"Column-level connections found: {rels}.")
        if schemaless:
            observations.append(
                f"Source(s) with no fixed list of columns declared: {_oxford(schemaless)}. "
                "This means the tool can't always tell exactly which columns come from there."
            )

        risks: list[str] = []
        if f["unrecognized_operators"]:
            risks.append(
                f"{_oxford(f['unrecognized_operators'])} use a technique this tool doesn't "
                "fully understand yet, so the lineage through them is approximate (columns are "
                "assumed to pass through unchanged, which may not be accurate)."
            )
        if schemaless:
            risks.append(
                "Because some sources don't declare their columns upfront, the "
                "source-to-destination trail may be incomplete for those columns."
            )
        joins = int(f["operators"].get("join", 0)) + int(f["operators"].get("lookup", 0))
        if joins >= 2:
            risks.append(
                f"This flow combines data from other sources in {joins} place(s) "
                "(joins/lookups) - each one is an extra place where a change could ripple "
                "through and needs to be re-checked together."
            )
        if f.get("lineage_warning_count"):
            risks.append(
                f"{f['lineage_warning_count']} issue(s) were flagged while analyzing this "
                "flow - see the warnings for details."
            )

        recommendations: list[str] = []
        if schemaless:
            recommendations.append(
                "Declare the exact list of expected columns for each source "
                '(turn off "schema drift") so lineage can be tracked precisely.'
            )
        generic = [
            t["name"] for t in f["transformations"] if t["name"].lower().startswith(_GENERIC_NAMES)
        ]
        if generic:
            recommendations.append(
                f"Rename these steps to describe what they actually do in business terms, "
                f"not just the technique used: {_oxford(generic)}."
            )
        wide = [t["name"] for t in f["transformations"] if len(t["columns"]) >= 12]
        if wide:
            recommendations.append(
                f"These step(s) touch a lot of columns at once - consider splitting them into "
                f"smaller, easier-to-follow steps: {_oxford(wide)}."
            )
        return InsightResult(
            summary=summary,
            observations=tuple(observations),
            risks=tuple(risks),
            recommendations=tuple(recommendations),
            provider=self.name,
        )

    # -------------------------------------------------------------- column
    def _column(self, f: dict[str, Any]) -> InsightResult:
        origins = _oxford(f["origins"]) if f["origins"] else "a source system directly"
        sinks = (
            _oxford(f["sink_columns"])
            if f["sink_columns"]
            else "no final destination (it isn't used further downstream)"
        )
        summary = (
            f"The column `{f['stream']}.{f['column']}` comes from {origins} and ends up in "
            f"{sinks}. "
            f"Risk level if you change it: **{f['impact_level'].upper()}** "
            f"(it affects {f['downstream_column_count']} other column(s) if changed)."
        )

        observations = [
            f"On its way, it passes through: {_oxford(f['transformations'])}."
            if f["transformations"]
            else "It doesn't pass through any processing steps - it's used as-is."
        ]
        if f["computed_columns_affected"]:
            observations.append(
                "It's used to calculate other column(s): "
                f"{_oxford(f['computed_columns_affected'])}."
            )

        risks: list[str] = []
        if f["impact_level"] == "high":
            risks.append(
                f"Changing this column could break {f['downstream_column_count']} other "
                f"column(s) that depend on it - talk to whoever owns {sinks} before making "
                "changes."
            )
        if f["joins_affected"]:
            risks.append(
                "It's used to match records with another data source (join): "
                f"{_oxford(f['joins_affected'])}. If its values change unexpectedly, records "
                "may stop matching correctly."
            )
        if f["filters_affected"]:
            risks.append(
                "It's used to decide which rows are kept or dropped "
                f"({_oxford(f['filters_affected'])}); changing its values can change how many "
                "rows come through."
            )
        if f["splits_affected"]:
            risks.append(
                f"It's used to send rows down different paths ({_oxford(f['splits_affected'])}); "
                "changing its values can send data down the wrong path."
            )

        recommendations: list[str] = []
        if f["sink_columns"]:
            recommendations.append(
                f"Before changing this column, test that {_oxford(f['sink_columns'])} still "
                "look correct afterward."
            )
        if f["computed_columns_affected"]:
            recommendations.append(
                "Double-check the calculation(s) that use this column: "
                f"{_oxford(f['computed_columns_affected'])}."
            )
        return InsightResult(
            summary=summary,
            observations=tuple(observations),
            risks=tuple(risks),
            recommendations=tuple(recommendations),
            provider=self.name,
        )
