"""``ColumnLineageGraph`` - a typed wrapper around a NetworkX ``DiGraph`` (F009).

Nodes are individual columns at a point in the flow, keyed ``"<stream>::<column>"``.
Edges point downstream: ``A -> B`` means *column B depends on column A*. So
``networkx.ancestors`` = upstream / origin and ``networkx.descendants`` =
downstream / impact.
"""

from __future__ import annotations

from collections.abc import Iterator

import networkx as nx

from app.models.lineage import LineageEdge, LineageNode, NodeKind, RelationshipType

_SEP = "::"


def node_id(stream: str, column: str) -> str:
    return f"{stream}{_SEP}{column}"


def split_id(identifier: str) -> tuple[str, str]:
    stream, _, column = identifier.partition(_SEP)
    return stream, column


class ColumnLineageGraph:
    """Directed column-dependency graph for one Mapping Data Flow (or, later, pipeline)."""

    def __init__(self, name: str = "") -> None:
        self.name = name
        self._g: nx.DiGraph = nx.DiGraph()
        #: stream name -> StreamKind value ("source" | "transformation" | "sink")
        self.streams: dict[str, str] = {}
        #: stream name -> extra metadata (filter expression, join condition, group_by, ...)
        self.stream_meta: dict[str, dict[str, object]] = {}

    # ---------------------------------------------------------------- mutation
    def register_stream(self, stream: str, kind: str, **meta: object) -> None:
        self.streams[stream] = kind
        if meta:
            self.stream_meta.setdefault(stream, {}).update(meta)

    def add_column(self, stream: str, column: str, kind: NodeKind) -> str:
        identifier = node_id(stream, column)
        if identifier not in self._g:
            self._g.add_node(
                identifier,
                kind=kind.value,
                stream=stream,
                column=column,
                label=f"{stream}.{column}",
            )
        return identifier

    def add_dependency(
        self,
        src_stream: str,
        src_column: str,
        dst_stream: str,
        dst_column: str,
        *,
        relationship: RelationshipType,
        transformation: str | None = None,
        transformation_type: str | None = None,
        expression: str | None = None,
        passthrough: bool = False,
    ) -> None:
        src = node_id(src_stream, src_column)
        dst = node_id(dst_stream, dst_column)
        if src not in self._g or dst not in self._g:
            raise KeyError(f"both columns must be added before linking: {src!r} -> {dst!r}")
        existing = self._g.get_edge_data(src, dst)
        # Keep the most specific relationship if an edge already exists
        # (e.g. a join-key edge should not be downgraded to passthrough).
        if existing is not None and not _more_specific(relationship, existing["relationship"]):
            return
        self._g.add_edge(
            src,
            dst,
            relationship=relationship.value,
            transformation=transformation,
            transformation_type=transformation_type,
            expression=expression,
            passthrough=passthrough,
        )

    # ---------------------------------------------------------------- queries
    @property
    def networkx(self) -> nx.DiGraph:
        return self._g

    def __contains__(self, identifier: str) -> bool:
        return identifier in self._g

    def __len__(self) -> int:
        return int(self._g.number_of_nodes())

    def node_ids(self) -> list[str]:
        return sorted(self._g.nodes)

    def iter_nodes(self) -> Iterator[LineageNode]:
        for identifier in self.node_ids():
            yield self.node(identifier)

    def node(self, identifier: str) -> LineageNode:
        data = self._g.nodes[identifier]
        return LineageNode(
            id=identifier,
            kind=NodeKind(data["kind"]),
            label=data["label"],
            stream=data["stream"],
            column=data["column"],
        )

    def edge(self, src: str, dst: str) -> LineageEdge:
        data = self._g.edges[src, dst]
        return LineageEdge(
            source=src,
            target=dst,
            relationship=RelationshipType(data["relationship"]),
            transformation=data["transformation"],
            transformation_type=data["transformation_type"],
            expression=data["expression"],
            passthrough=data["passthrough"],
        )

    def edges(self) -> list[LineageEdge]:
        return [self.edge(u, v) for u, v in sorted(self._g.edges)]

    def find(self, query: str) -> list[str]:
        """Resolve a (possibly qualified) column name to matching node ids.

        Accepts ``column``, ``stream.column`` or ``stream::column``.
        """
        query = query.strip()
        stream_filter: str | None = None
        column = query
        for sep in (_SEP, ".", "@"):
            if sep in query:
                stream_filter, _, column = query.partition(sep)
                stream_filter = stream_filter.strip()
                column = column.strip()
                break
        matches = [
            identifier
            for identifier, data in self._g.nodes(data=True)
            if data["column"] == column
            and (stream_filter is None or data["stream"] == stream_filter)
        ]
        return sorted(matches, key=lambda i: (self.streams.get(split_id(i)[0], "z"), i))

    def upstream(self, identifier: str) -> set[str]:
        return set(nx.ancestors(self._g, identifier))

    def downstream(self, identifier: str) -> set[str]:
        return set(nx.descendants(self._g, identifier))

    def paths(self, src: str, dst: str) -> list[list[str]]:
        if src not in self._g or dst not in self._g:
            return []
        return [list(p) for p in nx.all_simple_paths(self._g, src, dst)]

    def has_cycle(self) -> bool:
        return not nx.is_directed_acyclic_graph(self._g)

    # ---------------------------------------------------------------- export
    def to_dict(self) -> dict[str, object]:
        """Deterministic plain-dict form (for visualization / snapshot tests)."""
        return {
            "name": self.name,
            "streams": dict(sorted(self.streams.items())),
            "nodes": [
                {
                    "id": n.id,
                    "kind": n.kind.value,
                    "stream": n.stream,
                    "column": n.column,
                }
                for n in self.iter_nodes()
            ],
            "edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "relationship": e.relationship.value,
                    "transformation": e.transformation,
                    "transformation_type": e.transformation_type,
                    "expression": e.expression,
                    "passthrough": e.passthrough,
                }
                for e in self.edges()
            ],
        }


_SPECIFICITY = {
    RelationshipType.UNKNOWN.value: 0,
    RelationshipType.PASSTHROUGH.value: 1,
    RelationshipType.DIRECT.value: 2,
    RelationshipType.UNION.value: 3,
    RelationshipType.RENAME.value: 4,
    RelationshipType.FILTER.value: 4,
    RelationshipType.WINDOW.value: 4,
    RelationshipType.CONDITIONAL_SPLIT.value: 4,
    RelationshipType.LOOKUP.value: 5,
    RelationshipType.JOIN_KEY.value: 5,
    RelationshipType.AGGREGATE.value: 6,
    RelationshipType.DERIVED.value: 6,
}


def _more_specific(candidate: RelationshipType, current: str) -> bool:
    return _SPECIFICITY.get(candidate.value, 0) > _SPECIFICITY.get(current, 0)
