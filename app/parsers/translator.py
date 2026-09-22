"""Parse a Copy activity's ``translator`` into ``(source_column, sink_column)`` pairs (F008).

Supported shapes of ``typeProperties.translator`` (type ``TabularTranslator``):

* ``mappings``: ``[{"source": {"name": "a"}, "sink": {"name": "b"}}, ...]``
* ``columnMappings`` legacy string: ``"a: b, c: d"``
* ``columnMappings`` legacy object: ``{"a": "b", "c": "d"}``

Anything else (hierarchical / complex-type mappings, ``collectionReference`` …) is
ignored - no pairs are returned and the caller records that no column lineage
could be derived.
"""

from __future__ import annotations

from typing import Any


def _name(node: Any) -> str | None:
    if isinstance(node, dict):
        value = node.get("name")
        return value.strip() if isinstance(value, str) and value.strip() else None
    if isinstance(node, str) and node.strip():
        return node.strip()
    return None


def parse_copy_mappings(type_properties: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    translator = type_properties.get("translator")
    if not isinstance(translator, dict):
        return ()

    pairs: list[tuple[str, str]] = []

    mappings = translator.get("mappings")
    if isinstance(mappings, list):
        for entry in mappings:
            if not isinstance(entry, dict):
                continue
            src = _name(entry.get("source"))
            sink = _name(entry.get("sink"))
            if src and sink:
                pairs.append((src, sink))

    column_mappings = translator.get("columnMappings")
    if isinstance(column_mappings, str) and column_mappings.strip():
        for chunk in column_mappings.split(","):
            left, sep, right = chunk.partition(":")
            if sep and left.strip() and right.strip():
                pairs.append((left.strip(), right.strip()))
    elif isinstance(column_mappings, dict):
        for src, sink in column_mappings.items():
            if isinstance(src, str) and isinstance(sink, str) and src.strip() and sink.strip():
                pairs.append((src.strip(), sink.strip()))

    seen: dict[tuple[str, str], None] = {}
    for pair in pairs:
        seen.setdefault(pair, None)
    return tuple(seen)
