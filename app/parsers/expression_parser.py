"""Expression parser subsystem (F021).

Deterministically extracts the column references, function calls and parameters
from an ADF **Mapping Data Flow** expression (and, in a lighter mode, an ADF
**pipeline** expression). It never evaluates anything - it only lexes and
classifies tokens.

Design goals:
* extensible - add a function name to a set, or a token rule to the lexer;
* honest - unsupported / malformed input yields ``confidence != "high"`` and a
  note, never a guessed column list (PROJECT_PLAN.md section 12).

Supported data-flow constructs:
* function calls: ``concat(a, b)``, ``upper(x)``, ``toString(id)``,
  ``iif(isNull(email), alt, email)``, ``substring(name, 1, 5)``, ``sum(amount)``;
* bare column identifiers and ``{braced column names}``;
* string / numeric literals (ignored as dependencies);
* operators, including ``stream@column`` stream-qualified references;
* ``$parameter`` references;
* ``byName('col')`` / ``byNames(['a','b'])`` - the literal *is* the column.

Flagged as ``partial`` (columns still returned best-effort):
* higher-order functions with iterator variables (``#item``, ``#acc`` ...);
* characters the lexer does not recognise.

Flagged as ``none`` (no columns returned):
* unbalanced parentheses / brackets or an unterminated string literal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.expression import Confidence, ExpressionAnalysis, ExpressionDialect

_TOKEN_RE = re.compile(
    r"""
      (?P<WS>\s+)
    | (?P<STRING>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
    | (?P<BRACED>\{[^{}]*\})
    | (?P<NUMBER>\d+\.\d+(?:[eE][+-]?\d+)?[fFdDlL]?|\d+[fFdDlL]?)
    | (?P<PARAM>\$\$?[A-Za-z_][A-Za-z0-9_]*|\$\$)
    | (?P<LAMBDA>\#[A-Za-z_][A-Za-z0-9_]*|\#\#?)
    | (?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<OP><=>|==|!=|<=|>=|&&|\|\||=~|[-+*/%<>!=&|@?:.])
    | (?P<LPAREN>\()
    | (?P<RPAREN>\))
    | (?P<LBRACK>\[)
    | (?P<RBRACK>\])
    | (?P<COMMA>,)
    | (?P<OTHER>.)
    """,
    re.VERBOSE,
)

#: Identifiers that are literals / keywords, never column references.
_KEYWORDS = frozenset({"true", "false", "null"})

#: Common data-flow functions - used only to keep ``functions`` tidy / ordered.
KNOWN_DATA_FLOW_FUNCTIONS = frozenset(
    {
        "concat",
        "upper",
        "lower",
        "trim",
        "ltrim",
        "rtrim",
        "length",
        "replace",
        "regexReplace",
        "substring",
        "left",
        "right",
        "lpad",
        "rpad",
        "split",
        "toString",
        "toInteger",
        "toLong",
        "toDouble",
        "toDecimal",
        "toBoolean",
        "toDate",
        "toTimestamp",
        "iif",
        "iifNull",
        "isNull",
        "isNotNull",
        "coalesce",
        "case",
        "equals",
        "and",
        "or",
        "not",
        "greater",
        "lesser",
        "abs",
        "round",
        "floor",
        "ceil",
        "sum",
        "avg",
        "min",
        "max",
        "count",
        "countDistinct",
        "first",
        "last",
        "collect",
        "stddev",
        "variance",
        "byName",
        "byNames",
        "byPosition",
        "hash",
        "sha2",
        "md5",
        "crc32",
        "currentDate",
        "currentTimestamp",
        "addDays",
        "addMonths",
        "monthsBetween",
        "year",
        "month",
        "dayOfMonth",
        "true",
        "false",
        "null",
        "typeMatch",
        "isError",
        "nvl",
    }
)

_STRING_LITERAL_RE = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
_BYNAME_RE = re.compile(r"byNames?\s*\(\s*(?P<body>\[[^\]]*\]|" + _STRING_LITERAL_RE.pattern + r")")

# Pipeline-expression parameter/variable references.
_PIPELINE_REFS = (
    re.compile(r"pipeline\(\)\s*\.\s*parameters\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"pipeline\(\)\s*\.\s*globalParameters\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"parameters\(\s*'([^']+)'\s*\)"),
    re.compile(r"variables\(\s*'([^']+)'\s*\)"),
)


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str


def _lex(text: str) -> tuple[list[_Token], bool]:
    """Return ``(tokens, ok)``; ``ok`` is False on an unterminated string."""
    tokens: list[_Token] = []
    pos = 0
    for match in _TOKEN_RE.finditer(text):
        if match.start() != pos:
            return tokens, False  # a gap means the regex could not lex something
        pos = match.end()
        kind = match.lastgroup or "OTHER"
        if kind == "WS":
            continue
        tokens.append(_Token(kind, match.group()))
    if pos != len(text):
        return tokens, False
    # An unterminated quote is left as a lone OTHER "'" / '"'.
    if any(t.kind == "OTHER" and t.value in "'\"" for t in tokens):
        return tokens, False
    return tokens, True


def _dedup(values: list[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for value in values:
        if value:
            seen.setdefault(value, None)
    return tuple(seen)


class ExpressionParser:
    """Analyzes expression strings for one :class:`ExpressionDialect`."""

    def __init__(self, dialect: ExpressionDialect = ExpressionDialect.DATA_FLOW) -> None:
        self.dialect = dialect

    # ------------------------------------------------------------------ public
    def analyze(self, expression: str | None) -> ExpressionAnalysis:
        expr = (expression or "").strip()
        if not expr:
            return ExpressionAnalysis(
                expression="",
                dialect=self.dialect,
                confidence="none",
                notes=("Empty expression.",),
            )
        if self.dialect is ExpressionDialect.PIPELINE:
            return self._analyze_pipeline(expr)
        return self._analyze_data_flow(expr)

    # ----------------------------------------------------------- data flow
    def _analyze_data_flow(self, expr: str) -> ExpressionAnalysis:
        tokens, ok = _lex(expr)
        notes: list[str] = []
        confidence: Confidence = "high"

        if not ok or not _balanced(tokens):
            return ExpressionAnalysis(
                expression=expr,
                dialect=self.dialect,
                confidence="none",
                notes=("Unable to determine automatically",),
            )

        columns: list[str] = []
        qualified: list[str] = []
        functions: list[str] = []
        parameters: list[str] = []
        has_lambda = False
        has_other = False

        real = tokens  # already WS-free
        for index, tok in enumerate(real):
            prev = real[index - 1] if index > 0 else None
            nxt = real[index + 1] if index + 1 < len(real) else None

            if tok.kind == "LAMBDA":
                has_lambda = True
                continue
            if tok.kind == "OTHER":
                has_other = True
                continue
            if tok.kind == "PARAM":
                parameters.append(tok.value.lstrip("$"))
                continue
            if tok.kind == "BRACED":
                columns.append(tok.value[1:-1].strip())
                continue
            if tok.kind != "IDENT":
                continue

            # IDENT classification
            if nxt is not None and nxt.kind == "LPAREN":
                functions.append(tok.value)
                continue
            if tok.value in _KEYWORDS:
                continue
            if prev is not None and prev.kind == "OP" and prev.value == ".":
                continue  # member access, e.g. x.output.y
            if nxt is not None and nxt.kind == "OP" and nxt.value == "@":
                continue  # this IDENT is a stream name, not a column
            if prev is not None and prev.kind == "OP" and prev.value == "@":
                columns.append(tok.value)
                stream = real[index - 2].value if index >= 2 else "?"
                qualified.append(f"{stream}@{tok.value}")
                continue
            columns.append(tok.value)

        # byName('x') / byNames(['a','b']) -> the literal is the column
        for match in _BYNAME_RE.finditer(expr):
            for literal in _STRING_LITERAL_RE.findall(match.group("body")):
                columns.append(_unquote(literal))

        if has_lambda:
            confidence = "partial"
            notes.append(
                "Higher-order function with iterator variables; "
                "column dependencies may be incomplete."
            )
        if has_other:
            confidence = "partial"
            notes.append(
                "Expression contains characters the analyzer does not recognise; "
                "results may be incomplete."
            )

        ordered_functions = _order_functions(functions)
        return ExpressionAnalysis(
            expression=expr,
            dialect=self.dialect,
            referenced_columns=_dedup(columns),
            qualified_columns=_dedup(qualified),
            functions=ordered_functions,
            parameters=_dedup(parameters),
            confidence=confidence,
            notes=tuple(notes),
        )

    # ------------------------------------------------------------- pipeline
    def _analyze_pipeline(self, expr: str) -> ExpressionAnalysis:
        body = expr
        if body.startswith("@{") and body.endswith("}"):
            body = body[2:-1]
        elif body.startswith("@"):
            body = body[1:]

        tokens, ok = _lex(body)
        if not ok or not _balanced(tokens):
            return ExpressionAnalysis(
                expression=expr,
                dialect=self.dialect,
                confidence="none",
                notes=("Unable to determine automatically",),
            )

        functions = [
            tok.value
            for i, tok in enumerate(tokens)
            if tok.kind == "IDENT" and i + 1 < len(tokens) and tokens[i + 1].kind == "LPAREN"
        ]
        parameters: list[str] = []
        for pattern in _PIPELINE_REFS:
            parameters.extend(pattern.findall(expr))

        return ExpressionAnalysis(
            expression=expr,
            dialect=self.dialect,
            functions=_order_functions(functions),
            parameters=_dedup(parameters),
            confidence="high",
        )


def _balanced(tokens: list[_Token]) -> bool:
    paren = 0
    brack = 0
    for tok in tokens:
        if tok.kind == "LPAREN":
            paren += 1
        elif tok.kind == "RPAREN":
            paren -= 1
        elif tok.kind == "LBRACK":
            brack += 1
        elif tok.kind == "RBRACK":
            brack -= 1
        if paren < 0 or brack < 0:
            return False
    return paren == 0 and brack == 0


def _order_functions(names: list[str]) -> tuple[str, ...]:
    return _dedup(names)


def _unquote(literal: str) -> str:
    if len(literal) >= 2 and literal[0] in "'\"" and literal[-1] == literal[0]:
        inner = literal[1:-1]
        return inner.replace("\\" + literal[0], literal[0])
    return literal


_DEFAULT_PARSER = ExpressionParser()


def analyze_expression(
    expression: str | None,
    dialect: ExpressionDialect = ExpressionDialect.DATA_FLOW,
) -> ExpressionAnalysis:
    """Convenience wrapper around :class:`ExpressionParser`."""
    if dialect is ExpressionDialect.DATA_FLOW:
        return _DEFAULT_PARSER.analyze(expression)
    return ExpressionParser(dialect).analyze(expression)
