"""Deterministic column-lineage construction for a Mapping Data Flow (F009).

Walks the data flow's streams in topological order, tracking each stream's output
schema, and emits typed dependency edges into a :class:`ColumnLineageGraph`.

Per-operator rules (T009.3 - T009.9):

======================  ==================================================================
operator                behaviour
======================  ==================================================================
source                  seed schema from ``output(...)``; emit ``SOURCE_COLUMN`` nodes
select                  output schema = mapColumn targets; retained -> DIRECT,
                        ``new = old`` -> RENAME, ``new = expr`` -> DERIVED
derive                  keep every input column (PASSTHROUGH) + add derived targets
                        (DERIVED, deps from the expression parser)
filter                  passthrough all columns; record participating columns as FILTER
join / lookup           passthrough all input columns; parse the condition into
                        ``stream@col`` keys -> JOIN_KEY / LOOKUP edges
aggregate               output schema = groupBy keys (DIRECT) + aggregate targets
                        (AGGREGATE); non-key input columns are dropped
union                   output schema = union of input schemas by name; UNION edges
split                   passthrough to the base node and to every branch stream;
                        record condition columns as CONDITIONAL_SPLIT
window                  keep every input column (PASSTHROUGH) + windowColumns() targets
                        (WINDOW, deps from over()/asc()/desc() partition+sort columns and
                        the expression parser)
pivot                   output schema = groupBy keys (DIRECT) + approximated
                        ``<alias>_<value>`` targets when pivotBy() gives a literal value
                        list (AGGREGATE); non-key, non-aggregated columns are dropped -
                        output names are inherently data-dependent, so this is approximate
parse                   keep every input column (PASSTHROUGH) + one new struct-typed
                        target derived from its source column (DERIVED); the struct's
                        inner fields aren't separate columns at this stage - a later
                        select/derive unpacking them via dot notation (``json.field``)
                        already resolves back to this column through the expression parser
surrogateKey /          keep every input column (PASSTHROUGH) + one new generated-sequence
keyGenerate             column with no upstream dependency (it's a row counter, not derived
                        from anything - the column is its own origin)
other / unknown         passthrough all input columns (approximate) + a warning
sink                    output schema = feeding stream's schema; DIRECT edges
======================  ==================================================================
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

from app.core.logging import get_logger
from app.lineage.graph import ColumnLineageGraph
from app.models.dataflow import DataFlow, DataFlowTransformation
from app.models.lineage import NodeKind, RelationshipType, StreamKind
from app.parsers.dfs_script import _split_first_assignment, _strip_call, split_top_level
from app.parsers.expression_parser import analyze_expression

logger = get_logger(__name__)

_JOIN_LIKE = {"join", "lookup", "exists"}
_PASSTHROUGH_OPERATORS = {"sort", "alterRow", "rank", "cast", "assert", "dedupe", "sample"}

#: `each(match(<cond>), ...)` conditions we treat as "matches every remaining column".
_ALWAYS_TRUE = {"true()", "true", "1==1", "1 == 1"}
_RULE_PREFIX_RE = re.compile(r"^'([^']*)'\s*\+\s*\$\$$")
_RULE_SUFFIX_RE = re.compile(r"^\$\$\s*\+\s*'([^']*)'$")


def _select_rules(raw_arguments: str) -> list[tuple[str, str, str]]:
    """Parse `each(match(<cond>), <lhs> = <rhs>)` rules from a select's arguments."""
    rules: list[tuple[str, str, str]] = []
    for part in split_top_level(raw_arguments):
        inside = _strip_call(part, "each")
        if inside is None:
            continue
        items = split_top_level(inside)
        condition = ""
        if items and items[0].lstrip().startswith("match"):
            cond = _strip_call(items[0], "match")
            condition = (cond if cond is not None else items[0]).strip()
            items = items[1:]
        for item in items:
            assignment = _split_first_assignment(item)
            if assignment:
                rules.append((condition, assignment[0].strip(), assignment[1].strip()))
    return rules


def _resolve_rule_name(lhs: str, column: str) -> str | None:
    """Resolve a rule target expression (`$$`, `'p_' + $$`, `$$ + '_x'`) for one column."""
    if lhs == "$$":
        return column
    prefix = _RULE_PREFIX_RE.match(lhs)
    if prefix:
        return prefix.group(1) + column
    suffix = _RULE_SUFFIX_RE.match(lhs)
    if suffix:
        return column + suffix.group(1)
    return None


#: A DFS option argument such as ``disjoint: false`` or ``joinType: 'inner'``.
_OPTION_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*:")


def _split_as_alias(text: str) -> tuple[str, str | None]:
    """Split ``<expr> as <alias>`` on a top-level (bracket/quote-safe) `` as `` keyword."""
    depth = 0
    quote: str | None = None
    n = len(text)
    for i in range(n - 3):
        ch = text[i]
        if quote is not None:
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            continue
        if ch in "([{":
            depth += 1
            continue
        if ch in ")]}":
            depth = max(0, depth - 1)
            continue
        if depth == 0 and text[i : i + 4] == " as ":
            return text[:i].strip(), text[i + 4 :].strip()
    return text.strip(), None


def _parse_literal_list(text: str) -> list[str] | None:
    """Parse a ``['a', 'b', 'c']``-style literal array into plain string values.

    Returns ``None`` when any element is not a literal (e.g. an expression or a
    parameter reference), since the values then can't be resolved statically.
    """
    t = text.strip()
    if not (t.startswith("[") and t.endswith("]")):
        return None
    values: list[str] = []
    for item in split_top_level(t[1:-1]):
        item = item.strip()
        if len(item) >= 2 and item[0] in "'\"" and item[-1] == item[0]:
            values.append(item[1:-1])
        elif re.fullmatch(r"-?\d+(\.\d+)?", item):
            values.append(item)
        else:
            return None
    return values or None


@dataclass(frozen=True)
class DataFlowLineage:
    """A built column-lineage graph plus the warnings raised while building it.

    Also used for pipeline and combined (stitched) lineage - ``kind`` says which.
    ``bindings`` lists ``(stream, dataset, role)`` tuples used by the stitcher
    (``role`` is ``"reader"`` or ``"writer"``); empty for a plain data flow.
    """

    data_flow: str
    graph: ColumnLineageGraph
    warnings: tuple[str, ...]
    kind: str = "data_flow"
    bindings: tuple[tuple[str, str, str], ...] = ()


class _Builder:
    def __init__(self, df: DataFlow) -> None:
        self.df = df
        self.graph = ColumnLineageGraph(df.name)
        self.schema: dict[str, list[str]] = {}
        self.warnings: list[str] = []
        self._branches: dict[str, tuple[str, ...]] = {
            st.output_stream: st.output_branches for st in df.statements if st.output_branches
        }

    # -------------------------------------------------------------- helpers
    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def _input_schema(self, stream: str) -> list[str]:
        if stream not in self.schema:
            self.warn(
                f"Data flow '{self.df.name}': stream '{stream}' is consumed before it is "
                "defined; treating its schema as empty."
            )
            return []
        return self.schema[stream]

    def _locate(self, column: str, inputs: list[str]) -> list[str]:
        """Return the input streams (in order) whose schema contains ``column``."""
        return [s for s in inputs if column in self.schema.get(s, [])]

    def _add_output(self, stream: str, column: str) -> None:
        self.graph.add_column(stream, column, NodeKind.OUTPUT_COLUMN)

    # -------------------------------------------------------------- ordering
    def _ordered_streams(self) -> list[str]:
        universe: dict[str, None] = {}
        for src in self.df.sources:
            universe.setdefault(src.name, None)
        for tr in self.df.transformations:
            universe.setdefault(tr.name, None)
            for branch in self._branches.get(tr.name, ()):
                universe.setdefault(branch, None)
        for sink in self.df.sinks:
            universe.setdefault(sink.name, None)

        indegree = dict.fromkeys(universe, 0)
        adjacency: dict[str, list[str]] = {name: [] for name in universe}
        for upstream, downstream in self.df.stream_edges():
            if upstream in universe and downstream in universe:
                adjacency[upstream].append(downstream)
                indegree[downstream] += 1

        queue = deque(name for name in universe if indegree[name] == 0)
        ordered: list[str] = []
        while queue:
            node = queue.popleft()
            ordered.append(node)
            for nxt in adjacency[node]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)
        leftover = [name for name in universe if name not in ordered]
        if leftover:
            self.warn(
                f"Data flow '{self.df.name}': stream dependency cycle among "
                + ", ".join(sorted(leftover))
            )
            ordered.extend(leftover)
        return ordered

    # -------------------------------------------------------------- build
    def build(self) -> DataFlowLineage:
        sources = {s.name: s for s in self.df.sources}
        transformations = {t.name: t for t in self.df.transformations}
        sinks = {s.name: s for s in self.df.sinks}
        branch_parents = {
            branch: parent for parent, branches in self._branches.items() for branch in branches
        }

        for stream in self._ordered_streams():
            if stream in sources:
                self._build_source(stream)
            elif stream in transformations:
                self._build_transformation(transformations[stream])
            elif stream in branch_parents:
                self._build_branch(stream, branch_parents[stream])
            elif stream in sinks:
                self._build_sink(stream)

        return DataFlowLineage(
            data_flow=self.df.name,
            graph=self.graph,
            warnings=tuple(self.warnings),
        )

    def _build_source(self, name: str) -> None:
        source = next(s for s in self.df.sources if s.name == name)
        columns = list(source.schema_columns)
        self.graph.register_stream(name, StreamKind.SOURCE.value)
        for column in columns:
            self.graph.add_column(name, column, NodeKind.SOURCE_COLUMN)
        self.schema[name] = columns
        if not columns:
            self.warn(
                f"Data flow '{self.df.name}': source '{name}' has no declared schema "
                "(schema drift); lineage originating here is incomplete."
            )

    def _build_sink(self, name: str) -> None:
        sink = next(s for s in self.df.sinks if s.name == name)
        self.graph.register_stream(name, StreamKind.SINK.value)
        feeding = sink.input_stream
        if feeding is None:
            self.warn(f"Data flow '{self.df.name}': sink '{name}' has no input stream.")
            self.schema[name] = []
            return
        columns = self._input_schema(feeding)
        for column in columns:
            self.graph.add_column(name, column, NodeKind.SINK_COLUMN)
            self.graph.add_dependency(
                feeding,
                column,
                name,
                column,
                relationship=RelationshipType.DIRECT,
                transformation=name,
                transformation_type="sink",
            )
        self.schema[name] = list(columns)

    def _build_branch(self, branch: str, parent: str) -> None:
        parent_tr = next((t for t in self.df.transformations if t.name == parent), None)
        inputs = list(parent_tr.input_streams) if parent_tr else []
        cols = _dedup(col for stream in inputs for col in self.schema.get(stream, []))
        self.graph.register_stream(branch, StreamKind.TRANSFORMATION.value, split_branch_of=parent)
        for column in cols:
            self._add_output(branch, column)
            for src in self._locate(column, inputs):
                self.graph.add_dependency(
                    src,
                    column,
                    branch,
                    column,
                    relationship=RelationshipType.CONDITIONAL_SPLIT,
                    transformation=branch,
                    transformation_type="split",
                    passthrough=True,
                )
        self.schema[branch] = cols

    # ----------------------------------------------------- transformation
    def _build_transformation(self, tr: DataFlowTransformation) -> None:
        op = tr.kind or "unknown"
        inputs = list(tr.input_streams)
        self.graph.register_stream(tr.name, StreamKind.TRANSFORMATION.value, operator=op)
        if not inputs:
            self.warn(
                f"Data flow '{self.df.name}': transformation '{tr.name}' ({op}) has no "
                "input stream; skipped."
            )
            self.schema[tr.name] = []
            return

        handler = {
            "select": self._op_select,
            "foldDown": self._op_select,  # Flatten: mapColumn(...) like a select
            "derive": self._op_derive,
            "filter": self._op_filter,
            "aggregate": self._op_aggregate,
            "union": self._op_union,
            "split": self._op_split,
            "window": self._op_window,
            "pivot": self._op_pivot,
            "parse": self._op_parse,
            "surrogateKey": self._op_surrogate_key,
            "keyGenerate": self._op_surrogate_key,
        }.get(op)
        if handler is not None:
            handler(tr, inputs)
        elif op in _JOIN_LIKE:
            self._op_join_like(tr, inputs)
        else:
            self._op_passthrough(tr, inputs, note=op not in _PASSTHROUGH_OPERATORS)

    def _passthrough(
        self,
        tr: DataFlowTransformation,
        inputs: list[str],
        *,
        relationship: RelationshipType = RelationshipType.PASSTHROUGH,
        skip: set[str] | None = None,
    ) -> list[str]:
        skip = skip or set()
        produced = _dedup(col for stream in inputs for col in self.schema.get(stream, []))
        for column in produced:
            if column in skip:
                continue
            self._add_output(tr.name, column)
            for src in self._locate(column, inputs):
                self.graph.add_dependency(
                    src,
                    column,
                    tr.name,
                    column,
                    relationship=relationship,
                    transformation=tr.name,
                    transformation_type=tr.kind,
                    passthrough=True,
                )
        return produced

    def _op_passthrough(self, tr: DataFlowTransformation, inputs: list[str], *, note: bool) -> None:
        produced = self._passthrough(tr, inputs)
        self.schema[tr.name] = produced
        if note:
            self.warn(
                f"Data flow '{self.df.name}': lineage through '{tr.name}' "
                f"({tr.kind or 'unknown'}) is approximate - columns are assumed to pass "
                "through unchanged."
            )

    def _op_select(self, tr: DataFlowTransformation, inputs: list[str]) -> None:
        produced: list[str] = []
        consumed: set[str] = set()
        for col in tr.columns:
            consumed.add(col.target)
            consumed.update(col.referenced_columns)
            self._add_output(tr.name, col.target)
            relationship = {
                "retained": RelationshipType.DIRECT,
                "renamed": RelationshipType.RENAME,
                "derived": RelationshipType.DERIVED,
            }.get(col.kind, RelationshipType.DIRECT)
            self._link_expression_deps(
                tr,
                inputs,
                col.target,
                col.referenced_columns,
                relationship,
                col.expression,
                col.analysis_confidence,
                local=produced,
            )
            produced.append(col.target)

        produced.extend(self._apply_select_rules(tr, inputs, consumed))

        if not produced:
            # A select / Flatten that auto-maps (no explicit mapColumn, no rules).
            self.schema[tr.name] = self._passthrough(tr, inputs)
            return
        self.schema[tr.name] = _dedup(produced)

    def _apply_select_rules(
        self, tr: DataFlowTransformation, inputs: list[str], consumed: set[str]
    ) -> list[str]:
        """Handle `each(match(...), $$ = $$)` rule-based (pattern) column mapping."""
        rules = _select_rules(tr.raw_arguments)
        if not rules:
            return []
        input_cols = _dedup(col for stream in inputs for col in self.schema.get(stream, []))
        remaining = [c for c in input_cols if c not in consumed]
        produced: list[str] = []
        unresolved = False
        for condition, lhs, rhs in rules:
            if condition and condition not in _ALWAYS_TRUE:
                self.warn(
                    f"Data flow '{self.df.name}': select '{tr.name}' rule "
                    f"match({condition}) is not evaluated; remaining columns are assumed "
                    "to match it."
                )
            rel = (
                RelationshipType.DIRECT
                if rhs == "$$" and lhs == "$$"
                else RelationshipType.RENAME
                if rhs == "$$"
                else RelationshipType.DERIVED
            )
            for column in remaining:
                target = _resolve_rule_name(lhs, column)
                if target is None:
                    unresolved = True
                    continue
                self._add_output(tr.name, target)
                for src in self._locate(column, inputs):
                    self.graph.add_dependency(
                        src,
                        column,
                        tr.name,
                        target,
                        relationship=rel,
                        transformation=tr.name,
                        transformation_type="select",
                    )
                produced.append(target)
        if unresolved:
            self.warn(
                f"Data flow '{self.df.name}': select '{tr.name}' has a rule-based output "
                "name this tool cannot resolve; those columns are omitted from lineage."
            )
        return produced

    def _op_derive(self, tr: DataFlowTransformation, inputs: list[str]) -> None:
        targets = {c.target for c in tr.columns}
        carried = self._passthrough(tr, inputs, skip=targets)
        produced: list[str] = []
        for col in tr.columns:
            self._add_output(tr.name, col.target)
            self._link_expression_deps(
                tr,
                inputs,
                col.target,
                col.referenced_columns,
                RelationshipType.DERIVED,
                col.expression,
                col.analysis_confidence,
                local=produced,
            )
            produced.append(col.target)
        self.schema[tr.name] = _dedup([*carried, *produced])

    def _op_filter(self, tr: DataFlowTransformation, inputs: list[str]) -> None:
        produced = self._passthrough(tr, inputs)
        analysis = analyze_expression(tr.raw_arguments)
        self.graph.register_stream(
            tr.name,
            StreamKind.TRANSFORMATION.value,
            operator="filter",
            filter_expression=tr.raw_arguments,
            filter_columns=tuple(analysis.referenced_columns),
        )
        for column in analysis.referenced_columns:
            for src in self._locate(column, inputs):
                self.graph.add_dependency(
                    src,
                    column,
                    tr.name,
                    column,
                    relationship=RelationshipType.FILTER,
                    transformation=tr.name,
                    transformation_type="filter",
                    expression=tr.raw_arguments,
                )
        self.schema[tr.name] = produced

    def _op_aggregate(self, tr: DataFlowTransformation, inputs: list[str]) -> None:
        produced: list[str] = []
        for key in tr.group_by:
            produced.append(key)
            self._add_output(tr.name, key)
            for src in self._locate(key, inputs):
                self.graph.add_dependency(
                    src,
                    key,
                    tr.name,
                    key,
                    relationship=RelationshipType.DIRECT,
                    transformation=tr.name,
                    transformation_type="aggregate",
                )
            if not self._locate(key, inputs):
                self.warn(
                    f"Data flow '{self.df.name}': aggregate '{tr.name}' groups by unknown "
                    f"column '{key}'."
                )
        for col in tr.columns:
            self._add_output(tr.name, col.target)
            self._link_expression_deps(
                tr,
                inputs,
                col.target,
                col.referenced_columns,
                RelationshipType.AGGREGATE,
                col.expression,
                col.analysis_confidence,
                local=produced,
            )
            produced.append(col.target)
        self.schema[tr.name] = _dedup(produced)

    def _op_union(self, tr: DataFlowTransformation, inputs: list[str]) -> None:
        produced = _dedup(col for stream in inputs for col in self.schema.get(stream, []))
        for column in produced:
            self._add_output(tr.name, column)
            for src in self._locate(column, inputs):
                self.graph.add_dependency(
                    src,
                    column,
                    tr.name,
                    column,
                    relationship=RelationshipType.UNION,
                    transformation=tr.name,
                    transformation_type="union",
                )
        self.schema[tr.name] = produced

    def _op_split(self, tr: DataFlowTransformation, inputs: list[str]) -> None:
        produced = self._passthrough(tr, inputs, relationship=RelationshipType.CONDITIONAL_SPLIT)
        conditions = [
            part for part in split_top_level(tr.raw_arguments) if not _OPTION_RE.match(part)
        ]
        condition_columns: list[str] = []
        for condition in conditions:
            analysis = analyze_expression(condition)
            for column in analysis.referenced_columns:
                condition_columns.append(column)
                for src in self._locate(column, inputs):
                    self.graph.add_dependency(
                        src,
                        column,
                        tr.name,
                        column,
                        relationship=RelationshipType.CONDITIONAL_SPLIT,
                        transformation=tr.name,
                        transformation_type="split",
                        expression=condition,
                    )
        self.graph.register_stream(
            tr.name,
            StreamKind.TRANSFORMATION.value,
            operator="split",
            split_conditions=tuple(conditions),
            split_columns=tuple(_dedup(condition_columns)),
        )
        self.schema[tr.name] = produced

    def _op_window(self, tr: DataFlowTransformation, inputs: list[str]) -> None:
        """`over(...)` = partition columns, `asc/desc(...)` = sort columns,
        `windowColumns(...)` = new output columns (`target = expr`)."""
        partition_cols: list[str] = []
        sort_cols: list[str] = []
        window_col_parts: list[str] = []
        for part in split_top_level(tr.raw_arguments):
            inside = _strip_call(part, "over")
            if inside is not None:
                partition_cols.extend(c.strip() for c in split_top_level(inside) if c.strip())
                continue
            matched = False
            for kw in ("asc", "desc"):
                inside = _strip_call(part, kw)
                if inside is not None:
                    items = split_top_level(inside)
                    if items:
                        sort_cols.append(items[0].strip())
                    matched = True
                    break
            if matched:
                continue
            inside = _strip_call(part, "windowColumns")
            if inside is not None:
                window_col_parts = split_top_level(inside)

        targets: set[str] = set()
        window_columns: list[tuple[str, str]] = []
        for part in window_col_parts:
            assignment = _split_first_assignment(part)
            if assignment:
                targets.add(assignment[0].strip())
                window_columns.append((assignment[0].strip(), assignment[1].strip()))

        carried = self._passthrough(tr, inputs, skip=targets)
        context_columns = _dedup([*partition_cols, *sort_cols])

        self.graph.register_stream(
            tr.name,
            StreamKind.TRANSFORMATION.value,
            operator="window",
            window_partition_by=tuple(_dedup(partition_cols)),
            window_order_by=tuple(_dedup(sort_cols)),
        )

        produced = list(carried)
        produced_targets: list[str] = []
        for target, expr in window_columns:
            self._add_output(tr.name, target)
            analysis = analyze_expression(expr)
            referenced = _dedup([*analysis.referenced_columns, *context_columns])
            if not referenced:
                self.warn(
                    f"Data flow '{self.df.name}': could not determine the source columns "
                    f"for '{tr.name}.{target}' (window expression: {expr!r})."
                )
            local_set = set(produced_targets)
            for column in referenced:
                located = self._locate(column, inputs)
                if not located and column in local_set:
                    self.graph.add_dependency(
                        tr.name,
                        column,
                        tr.name,
                        target,
                        relationship=RelationshipType.WINDOW,
                        transformation=tr.name,
                        transformation_type="window",
                        expression=expr,
                    )
                    continue
                for src in located:
                    self.graph.add_dependency(
                        src,
                        column,
                        tr.name,
                        target,
                        relationship=RelationshipType.WINDOW,
                        transformation=tr.name,
                        transformation_type="window",
                        expression=expr,
                    )
            produced.append(target)
            produced_targets.append(target)
        self.schema[tr.name] = _dedup(produced)

    def _op_pivot(self, tr: DataFlowTransformation, inputs: list[str]) -> None:
        """Decompose what's statically knowable; pivoted column *names* are data-dependent.

        `groupBy(...)` columns survive unchanged (DIRECT). The aggregate
        expression(s) - `<expr> as <alias>` - are linked (AGGREGATE) to their
        source columns; when `pivotBy(col, [literal, values])` gives an explicit
        literal value list, output columns are approximated as
        `<alias>_<value>` (flagged as approximate - ADF's actual `columnNaming`
        template may order/format them differently). Columns that are neither a
        groupBy key nor referenced by an aggregate expression do **not** survive
        a pivot and are correctly dropped, unlike the generic passthrough rule.
        """
        parts = split_top_level(tr.raw_arguments)
        group_cols: list[str] = []
        pivot_column: str | None = None
        pivot_values: list[str] | None = None
        agg_parts: list[str] = []
        for part in parts:
            inside = _strip_call(part, "groupBy")
            if inside is not None:
                group_cols.extend(c.strip() for c in split_top_level(inside) if c.strip())
                continue
            inside = _strip_call(part, "pivotBy")
            if inside is not None:
                items = split_top_level(inside)
                if items:
                    pivot_column = items[0].strip()
                if len(items) > 1:
                    pivot_values = _parse_literal_list(items[1])
                continue
            if _OPTION_RE.match(part):
                continue
            agg_parts.append(part)

        produced: list[str] = []
        for key in _dedup(group_cols):
            produced.append(key)
            self._add_output(tr.name, key)
            for src in self._locate(key, inputs):
                self.graph.add_dependency(
                    src,
                    key,
                    tr.name,
                    key,
                    relationship=RelationshipType.DIRECT,
                    transformation=tr.name,
                    transformation_type="pivot",
                )

        approximated_names = False
        for part in agg_parts:
            expr, alias = _split_as_alias(part)
            analysis = analyze_expression(expr)
            source_columns = _dedup(
                [*analysis.referenced_columns, *([pivot_column] if pivot_column else [])]
            )
            if pivot_values and alias:
                approximated_names = True
                for value in pivot_values:
                    target = f"{alias}_{value}"
                    produced.append(target)
                    self._add_output(tr.name, target)
                    for column in source_columns:
                        for src in self._locate(column, inputs):
                            self.graph.add_dependency(
                                src,
                                column,
                                tr.name,
                                target,
                                relationship=RelationshipType.AGGREGATE,
                                transformation=tr.name,
                                transformation_type="pivot",
                                expression=expr,
                            )
            # Without a literal pivotBy() value list, the output column names are
            # genuinely data-dependent; we cannot link the aggregate expression to
            # a named output column at all, so nothing further is added here.

        if approximated_names:
            self.warn(
                f"Data flow '{self.df.name}': pivot '{tr.name}' output column names are "
                "approximated as `<alias>_<pivotValue>` from the literal pivotBy() values; "
                "ADF's actual columnNaming template may format them differently."
            )
        if not pivot_values or not agg_parts:
            self.warn(
                f"Data flow '{self.df.name}': pivot '{tr.name}' produces data-dependent "
                "output columns that cannot be fully determined from the JSON alone; only "
                "the groupBy columns are represented in lineage."
            )

        self.graph.register_stream(
            tr.name,
            StreamKind.TRANSFORMATION.value,
            operator="pivot",
            pivot_by=pivot_column or "",
            pivot_group_by=tuple(_dedup(group_cols)),
        )
        self.schema[tr.name] = _dedup(produced)

    def _op_parse(self, tr: DataFlowTransformation, inputs: list[str]) -> None:
        """`<target> = <source> ? (<field> as <type>, ...), format: '...', ...`.

        At the DFS level, `parse` introduces exactly ONE new struct-typed column
        (`target`), derived from `source`; the declared fields are that struct's
        *inner* schema, not separate top-level columns (a later `select`/`derive`
        unpacks them via dot notation, e.g. `trade = json.trade`, which the
        existing `select`/`derive` handling already resolves via the expression
        parser). Every other existing column passes through unchanged.
        """
        target: str | None = None
        source_expr: str | None = None
        for part in split_top_level(tr.raw_arguments):
            if _OPTION_RE.match(part):
                continue
            assignment = _split_first_assignment(part)
            if assignment:
                target, rhs = assignment
                source_expr = rhs.split("?", 1)[0].strip()
                break

        carried = self._passthrough(tr, inputs, skip={target} if target else set())
        produced = list(carried)
        if target:
            produced.append(target)
            self._add_output(tr.name, target)
            analysis = analyze_expression(source_expr or "")
            self._link_expression_deps(
                tr,
                inputs,
                target,
                analysis.referenced_columns,
                RelationshipType.DERIVED,
                source_expr,
                analysis.confidence,
            )
        else:
            self.warn(
                f"Data flow '{self.df.name}': parse '{tr.name}' has no recognizable "
                "'<target> = <source> ? (...)' assignment; treated as passthrough."
            )
        self.schema[tr.name] = _dedup(produced)

    def _op_surrogate_key(self, tr: DataFlowTransformation, inputs: list[str]) -> None:
        """`<stream> keyGenerate(output(<col> as long), startAt: 1L) ~> Name`.

        Adds one synthetic, sequentially-generated column with no upstream
        column dependency (there is nothing to derive it *from* - it's a row
        counter) on top of every existing column passing through unchanged.
        """
        inside = None
        for part in split_top_level(tr.raw_arguments):
            candidate = _strip_call(part, "output")
            if candidate is not None:
                inside = candidate
                break

        key_column: str | None = None
        if inside:
            first = split_top_level(inside)[0] if split_top_level(inside) else ""
            key_column = first.split(" as ", 1)[0].strip() or None

        carried = self._passthrough(tr, inputs, skip={key_column} if key_column else set())
        produced = list(carried)
        if key_column:
            produced.append(key_column)
            self._add_output(tr.name, key_column)
            # Deliberately no add_dependency(): a generated sequence has no
            # source column, so this node is its own origin (no inbound edges).
        else:
            self.warn(
                f"Data flow '{self.df.name}': surrogate key '{tr.name}' has no recognizable "
                "output(<col> as ...) clause; treated as passthrough."
            )
        self.graph.register_stream(
            tr.name, StreamKind.TRANSFORMATION.value, operator="surrogateKey"
        )
        self.schema[tr.name] = _dedup(produced)

    def _op_join_like(self, tr: DataFlowTransformation, inputs: list[str]) -> None:
        produced = self._passthrough(tr, inputs)
        condition = split_top_level(tr.raw_arguments)[:1]
        analysis = analyze_expression(condition[0]) if condition else analyze_expression("")
        relationship = RelationshipType.LOOKUP if tr.kind == "lookup" else RelationshipType.JOIN_KEY
        self.graph.register_stream(
            tr.name,
            StreamKind.TRANSFORMATION.value,
            operator=tr.kind,
            join_condition=condition[0] if condition else "",
            join_key_columns=tuple(analysis.referenced_columns),
        )
        for qualified in analysis.qualified_columns:
            stream, _, column = qualified.partition("@")
            if column in self.schema.get(stream, []):
                self.graph.add_dependency(
                    stream,
                    column,
                    tr.name,
                    column,
                    relationship=relationship,
                    transformation=tr.name,
                    transformation_type=tr.kind,
                    expression=condition[0] if condition else None,
                )
        # Fallback when the condition used bare (unqualified) column names.
        if not analysis.qualified_columns:
            for column in analysis.referenced_columns:
                for src in self._locate(column, inputs):
                    self.graph.add_dependency(
                        src,
                        column,
                        tr.name,
                        column,
                        relationship=relationship,
                        transformation=tr.name,
                        transformation_type=tr.kind,
                        expression=condition[0] if condition else None,
                    )
        self.schema[tr.name] = produced

    # ------------------------------------------------------------- shared
    def _link_expression_deps(
        self,
        tr: DataFlowTransformation,
        inputs: list[str],
        target: str,
        referenced: tuple[str, ...],
        relationship: RelationshipType,
        expression: str | None,
        confidence: str,
        *,
        local: Iterable[str] = (),
    ) -> None:
        """Link ``target``'s referenced columns back to their source(s).

        ``local`` lists columns this same transformation has already produced
        earlier in its own block (e.g. an earlier ``derive``/``select`` entry).
        A reference to one of those chains through it (an edge from the
        earlier same-stream column) instead of being reported as unresolved -
        DFS blocks are evaluated top-to-bottom and commonly build one column
        from another (``fullNameUpper = upper(fullName)``).
        """
        if not referenced:
            if confidence == "none":
                self.warn(
                    f"Data flow '{self.df.name}': could not determine the source columns "
                    f"for '{tr.name}.{target}' (expression: {expression!r})."
                )
            return
        local_set = {c for c in local if c != target}
        for column in referenced:
            located = self._locate(column, inputs)
            if not located and column in local_set:
                self.graph.add_dependency(
                    tr.name,
                    column,
                    tr.name,
                    target,
                    relationship=relationship,
                    transformation=tr.name,
                    transformation_type=tr.kind,
                    expression=expression,
                )
                continue
            if not located:
                self.warn(
                    f"Data flow '{self.df.name}': '{tr.name}.{target}' references column "
                    f"'{column}' which is not in any input stream."
                )
                continue
            for src in located:
                self.graph.add_dependency(
                    src,
                    column,
                    tr.name,
                    target,
                    relationship=relationship,
                    transformation=tr.name,
                    transformation_type=tr.kind,
                    expression=expression,
                )


def _dedup(values: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen)


def build_data_flow_lineage(data_flow: DataFlow) -> DataFlowLineage:
    """Build a deterministic column-lineage graph for one Mapping Data Flow."""
    return _Builder(data_flow).build()
