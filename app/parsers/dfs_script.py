"""Data Flow Script (DFS) parser (F004 - T004.3).

The DFS is the pretty-printed script ADF stores in
``dataFlow.properties.typeProperties.scriptLines``. Each transformation is one
statement of the form::

    [inputStream1, inputStream2, ...] operator(arguments) ~> outputStream[@(branchA, branchB)]

This module tokenises that text deterministically. It never evaluates anything -
string analysis only (see PROJECT_PLAN.md security requirements).
"""

from __future__ import annotations

import re

from app.models.dataflow import (
    RECOGNIZED_OPERATORS,
    DataFlowColumnDef,
    DataFlowScriptStatement,
)
from app.parsers.expression_parser import analyze_expression

_OPEN = "([{"
_CLOSE = ")]}"
_QUOTES = "'\""
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


# --------------------------------------------------------------------------- text
def _strip_block_comments(text: str) -> str:
    return _BLOCK_COMMENT_RE.sub(" ", text)


def split_statements(script: str) -> list[str]:
    """Split a full DFS script into individual statement strings.

    A statement ends at a paren-depth-0 ``~>`` followed by its target stream.
    """
    script = _strip_block_comments(script)
    statements: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    i = 0
    n = len(script)
    while i < n:
        ch = script[i]
        if quote is not None:
            buf.append(ch)
            if ch == "\\" and i + 1 < n:
                buf.append(script[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in _QUOTES:
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch in _OPEN:
            depth += 1
            buf.append(ch)
            i += 1
            continue
        if ch in _CLOSE:
            depth = max(0, depth - 1)
            buf.append(ch)
            i += 1
            continue
        if depth == 0 and script.startswith("~>", i):
            buf.append("~>")
            i += 2
            while i < n and script[i] in " \t\r\n":
                i += 1
            target: list[str] = []
            while i < n and (script[i].isalnum() or script[i] in "_"):
                target.append(script[i])
                i += 1
            if i < n and script[i] == "@":
                target.append("@")
                i += 1
                if i < n and script[i] == "(":
                    d = 0
                    while i < n:
                        target.append(script[i])
                        if script[i] == "(":
                            d += 1
                        elif script[i] == ")":
                            d -= 1
                            if d == 0:
                                i += 1
                                break
                        i += 1
            buf.append(" " + "".join(target))
            statements.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def split_top_level(text: str, sep: str = ",") -> list[str]:
    """Split ``text`` on ``sep`` at paren-depth 0, ignoring quoted regions."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    for ch in text:
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in _QUOTES:
            quote = ch
            buf.append(ch)
            continue
        if ch in _OPEN:
            depth += 1
            buf.append(ch)
            continue
        if ch in _CLOSE:
            depth = max(0, depth - 1)
            buf.append(ch)
            continue
        if depth == 0 and ch == sep:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _rfind_top_level(text: str, token: str) -> int:
    depth = 0
    quote: str | None = None
    found = -1
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if quote is not None:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in _QUOTES:
            quote = ch
            i += 1
            continue
        if ch in _OPEN:
            depth += 1
            i += 1
            continue
        if ch in _CLOSE:
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0 and text.startswith(token, i):
            found = i
            i += len(token)
            continue
        i += 1
    return found


def _match_paren(text: str, open_idx: int) -> int:
    depth = 0
    quote: str | None = None
    for i in range(open_idx, len(text)):
        ch = text[i]
        if quote is not None:
            if ch == quote:
                quote = None
            continue
        if ch in _QUOTES:
            quote = ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _split_first_assignment(text: str) -> tuple[str, str] | None:
    """Split ``lhs = rhs`` on the first top-level ``=`` that is a real assignment."""
    depth = 0
    quote: str | None = None
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if quote is not None:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in _QUOTES:
            quote = ch
            i += 1
            continue
        if ch in _OPEN:
            depth += 1
            i += 1
            continue
        if ch in _CLOSE:
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0 and ch == "=":
            prev = text[i - 1] if i > 0 else ""
            nxt = text[i + 1] if i + 1 < n else ""
            if nxt == "=" or prev in "=!<>~":
                i += 2 if nxt == "=" else 1
                continue
            return text[:i].strip(), text[i + 1 :].strip()
        i += 1
    return None


def _is_identifier(text: str) -> bool:
    s = text.strip()
    if len(s) >= 2 and s.startswith("{") and s.endswith("}"):
        return True
    return bool(_IDENT_RE.match(s))


def _split_streams(text: str) -> tuple[str, ...]:
    # A consumed conditional-split branch is written ``SplitName@branchName``;
    # normalise it to the branch stream name so the stream graph connects.
    return tuple(part.split("@")[-1] for part in re.split(r"[,\s]+", text.strip()) if part)


def _strip_call(token: str, name: str) -> str | None:
    """If ``token`` is ``name(...)`` return the text inside the parens."""
    t = token.strip()
    if not t.startswith(name):
        return None
    rest = t[len(name) :].lstrip()
    if not rest.startswith("("):
        return None
    depth = 0
    for i, ch in enumerate(rest):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return rest[1:i]
    return rest[1:]  # unbalanced - best effort


def _parse_target(target: str) -> tuple[str, tuple[str, ...]]:
    head, sep, rest = target.partition("@")
    name = head.strip()
    if not sep:
        return name, ()
    rest = rest.strip()
    if rest.startswith("("):
        inside = rest[1:]
        if inside.endswith(")"):
            inside = inside[:-1]
        return name, tuple(split_top_level(inside))
    return name, ()


def _parse_body(body: str) -> tuple[str, tuple[str, ...], str]:
    """Return ``(operator, input_streams, raw_arguments)`` for a statement body."""
    quote: str | None = None
    open_idx = -1
    for i, ch in enumerate(body):
        if quote is not None:
            if ch == quote:
                quote = None
            continue
        if ch in _QUOTES:
            quote = ch
            continue
        if ch == "(":
            open_idx = i
            break
    if open_idx == -1:
        return "", _split_streams(body), ""
    close_idx = _match_paren(body, open_idx)
    pre = body[:open_idx].rstrip()
    args = body[open_idx + 1 : close_idx] if close_idx != -1 else body[open_idx + 1 :]
    tokens = pre.split()
    operator = tokens[-1] if tokens else ""
    stream_part = " ".join(tokens[:-1])
    inputs = _split_streams(stream_part) if stream_part else ()
    return operator, inputs, args.strip()


def parse_statement(raw: str) -> DataFlowScriptStatement:
    """Parse one DFS statement string into a :class:`DataFlowScriptStatement`."""
    text = raw.strip()
    idx = _rfind_top_level(text, "~>")
    if idx == -1:
        return DataFlowScriptStatement(output_stream="", raw_text=text, is_recognized=False)
    body = text[:idx].strip()
    out_stream, branches = _parse_target(text[idx + 2 :].strip())
    operator, inputs, args = _parse_body(body)
    return DataFlowScriptStatement(
        output_stream=out_stream,
        output_branches=branches,
        input_streams=inputs,
        operator=operator,
        raw_arguments=args,
        raw_text=text,
        is_recognized=bool(operator) and operator in RECOGNIZED_OPERATORS,
    )


def parse_script(script: str) -> list[DataFlowScriptStatement]:
    return [parse_statement(s) for s in split_statements(script)]


# ------------------------------------------------------------------ column defs
def _analyzed_column(target: str, expression: str, kind: str) -> DataFlowColumnDef:
    """Build a column def whose ``referenced_columns`` come from the expression parser."""
    if kind == "retained":
        return DataFlowColumnDef(
            target=target,
            expression=expression,
            kind=kind,
            referenced_columns=(expression,),
            analysis_confidence="high",
        )
    analysis = analyze_expression(expression)
    return DataFlowColumnDef(
        target=target,
        expression=expression,
        kind=kind,
        referenced_columns=analysis.referenced_columns,
        analysis_confidence=analysis.confidence,
    )


def extract_columns(
    operator: str, raw_arguments: str
) -> tuple[tuple[DataFlowColumnDef, ...], tuple[str, ...]]:
    """Structural column extraction for an operator. Returns ``(columns, group_by)``."""
    parts = split_top_level(raw_arguments)
    columns: list[DataFlowColumnDef] = []
    group_by: tuple[str, ...] = ()

    if operator == "source":
        for part in parts:
            inside = _strip_call(part, "output")
            if inside is None:
                continue
            for entry in split_top_level(inside):
                name, _, dtype = entry.partition(" as ")
                name = name.strip()
                if name:
                    columns.append(
                        DataFlowColumnDef(
                            target=name,
                            data_type=dtype.strip() or None,
                            kind="source_schema",
                            analysis_confidence="high",
                        )
                    )
        return tuple(columns), group_by

    if operator == "derive":
        for part in parts:
            assignment = _split_first_assignment(part)
            if assignment:
                columns.append(_analyzed_column(assignment[0], assignment[1], "derived"))
        return tuple(columns), group_by

    if operator in ("select", "foldDown"):  # foldDown = Flatten, also uses mapColumn(...)
        for part in parts:
            inside = _strip_call(part, "mapColumn")
            if inside is None:
                continue
            for entry in split_top_level(inside):
                assignment = _split_first_assignment(entry)
                if assignment:
                    rhs = assignment[1].strip()
                    kind = "renamed" if _is_identifier(rhs) else "derived"
                    columns.append(_analyzed_column(assignment[0], rhs, kind))
                else:
                    columns.append(_analyzed_column(entry, entry, "retained"))
        return tuple(columns), group_by

    if operator == "aggregate":
        group: list[str] = []
        for part in parts:
            inside = _strip_call(part, "groupBy")
            if inside is not None:
                for entry in split_top_level(inside):
                    assignment = _split_first_assignment(entry)
                    group.append(assignment[0] if assignment else entry)
                continue
            assignment = _split_first_assignment(part)
            if assignment:
                columns.append(_analyzed_column(assignment[0], assignment[1], "aggregate"))
        return tuple(columns), tuple(group)

    return tuple(columns), group_by
