"""A small DAX expression parser.

The orchestrator needed to reason about the *shape* of a DAX
expression, not just search it with regular expressions: a linter
matches text, but "is this ``IF`` nested inside another ``IF`` on the
same test?" and "does this subexpression appear twice?" are questions
about structure.

So this module tokenises and parses the subset of DAX that measures
use, into a small tree that can be walked, matched and printed back.

What is supported:

- literals: numbers, quoted strings, ``TRUE()`` / ``FALSE()``
- column references: ``[Column]`` and ``Table[Column]``
- table references: a bare identifier used where a table is meant
- function calls with positional arguments
- the operators ``+ - * / ^ & = <> < > <= >= && || !``
- ``VAR`` / ``RETURN`` blocks
- table constructors: ``{1, 2, 3}``
- ``//`` and ``/* */`` comments, which are discarded

What is not supported, and what happens when it shows up: a construct
this parser does not understand raises :class:`DaxParseError` with the
offset. The caller is expected to treat that as "this expression is
beyond what we can reason about" and leave it alone — never to guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "BinaryOp",
    "ColumnRef",
    "DaxParseError",
    "FunctionCall",
    "Literal",
    "Node",
    "TableLiteral",
    "TableRef",
    "UnaryOp",
    "VarBlock",
    "parse_dax",
    "print_dax",
]


class DaxParseError(ValueError):
    """The expression is not something this parser understands.

    Carries the offset so the caller can point at the problem.
    """

    def __init__(self, message: str, offset: int) -> None:
        super().__init__(f"{message} (at offset {offset})")
        self.offset = offset


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


@dataclass
class Node:
    """Base class for every node in the tree."""

    def children(self) -> list[Node]:
        return []


@dataclass
class Literal(Node):
    """A number, a string, or a boolean."""

    value: Any
    kind: str = "number"  # number | string | boolean


@dataclass
class ColumnRef(Node):
    """``[Column]`` or ``Table[Column]``."""

    column: str
    table: str | None = None

    @property
    def qualified(self) -> str:
        return f"{self.table}[{self.column}]" if self.table else f"[{self.column}]"


@dataclass
class TableRef(Node):
    """A bare table name."""

    name: str


@dataclass
class FunctionCall(Node):
    """``NAME(arg, ...)``."""

    name: str
    args: list[Node] = field(default_factory=list)

    def children(self) -> list[Node]:
        return list(self.args)


@dataclass
class BinaryOp(Node):
    """An infix operator."""

    op: str
    left: Node
    right: Node

    def children(self) -> list[Node]:
        return [self.left, self.right]


@dataclass
class UnaryOp(Node):
    """A prefix operator (``-``, ``NOT``, ``!``)."""

    op: str
    operand: Node

    def children(self) -> list[Node]:
        return [self.operand]


@dataclass
class VarBlock(Node):
    """``VAR a = x VAR b = y RETURN z``."""

    variables: list[tuple[str, Node]] = field(default_factory=list)
    body: Node | None = None

    def children(self) -> list[Node]:
        nodes: list[Node] = [value for _, value in self.variables]
        if self.body is not None:
            nodes.append(self.body)
        return nodes


@dataclass
class TableLiteral(Node):
    """``{1, 2, 3}``."""

    rows: list[list[Node]] = field(default_factory=list)

    def children(self) -> list[Node]:
        return [cell for row in self.rows for cell in row]


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


@dataclass
class Token:
    kind: str  # number | string | identifier | column | operator | punct | eof
    text: str
    offset: int


_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
_OPERATORS = (
    "<>",
    "<=",
    ">=",
    "&&",
    "||",
    "=",
    "<",
    ">",
    "+",
    "-",
    "*",
    "/",
    "^",
    "&",
    "!",
)


def _tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    length = len(source)
    while i < length:
        char = source[i]
        if char.isspace():
            i += 1
            continue
        if source.startswith("//", i):
            while i < length and source[i] != "\n":
                i += 1
            continue
        if source.startswith("/*", i):
            end = source.find("*/", i + 2)
            if end == -1:
                raise DaxParseError("unterminated block comment", i)
            i = end + 2
            continue
        if char == '"':
            end = i + 1
            while end < length:
                if source[end] == '"':
                    if end + 1 < length and source[end + 1] == '"':
                        end += 2
                        continue
                    break
                end += 1
            if end >= length:
                raise DaxParseError("unterminated string literal", i)
            # The stored value is unescaped; the printer escapes it
            # again on the way out.
            tokens.append(Token("string", source[i + 1 : end].replace('""', '"'), i))
            i = end + 1
            continue
        if char == "[":
            end = source.find("]", i + 1)
            if end == -1:
                raise DaxParseError("unterminated column reference", i)
            tokens.append(Token("column", source[i + 1 : end], i))
            i = end + 1
            continue
        if char.isdigit():
            match = _NUMBER_RE.match(source, i)
            assert match is not None
            tokens.append(Token("number", match.group(0), i))
            i = match.end()
            continue
        if char.isalpha() or char == "_":
            match = _IDENT_RE.match(source, i)
            assert match is not None
            tokens.append(Token("identifier", match.group(0), i))
            i = match.end()
            continue
        if char == "(" or char == ")" or char == ",":
            tokens.append(Token("punct", char, i))
            i += 1
            continue
        if char == "{" or char == "}":
            tokens.append(Token("punct", char, i))
            i += 1
            continue
        for op in _OPERATORS:
            if source.startswith(op, i):
                tokens.append(Token("operator", op, i))
                i += len(op)
                break
        else:
            raise DaxParseError(f"unexpected character {char!r}", i)
    tokens.append(Token("eof", "", length))
    return tokens


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

# Lowest precedence first. DAX spells its logical operators as words.
_BINARY_LEVELS: tuple[tuple[str, ...], ...] = (
    ("OR",),
    ("AND",),
    ("=", "<>", "<", ">", "<=", ">="),
    ("&",),
    ("+", "-"),
    ("*", "/"),
    ("^",),
)

_KEYWORDS = {"VAR", "RETURN"}


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    # -- token helpers --------------------------------------------------
    @property
    def _current(self) -> Token:
        return self._tokens[self._pos]

    def _advance(self) -> Token:
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def _at_keyword(self, word: str) -> bool:
        token = self._current
        return token.kind == "identifier" and token.text.upper() == word

    # -- grammar --------------------------------------------------------
    def parse(self) -> Node:
        node = self._parse_statement()
        if self._current.kind != "eof":
            raise DaxParseError(f"unexpected {self._current.text!r}", self._current.offset)
        return node

    def _parse_statement(self) -> Node:
        if self._at_keyword("VAR"):
            return self._parse_var_block()
        return self._parse_expression()

    def _parse_var_block(self) -> VarBlock:
        variables: list[tuple[str, Node]] = []
        while self._at_keyword("VAR"):
            self._advance()
            name_token = self._advance()
            if name_token.kind != "identifier":
                raise DaxParseError("expected a variable name after VAR", name_token.offset)
            if self._current.kind != "operator" or self._current.text != "=":
                raise DaxParseError("expected '=' after the variable name", self._current.offset)
            self._advance()
            variables.append((name_token.text, self._parse_expression()))
        if not self._at_keyword("RETURN"):
            raise DaxParseError("expected RETURN in a VAR block", self._current.offset)
        self._advance()
        body = self._parse_expression()
        return VarBlock(variables=variables, body=body)

    def _parse_expression(self, level: int = 0) -> Node:
        if level >= len(_BINARY_LEVELS):
            return self._parse_unary()
        operators = _BINARY_LEVELS[level]
        left = self._parse_expression(level + 1)
        while self._is_operator(operators):
            token = self._advance()
            op = token.text.upper() if token.kind == "identifier" else token.text
            right = self._parse_expression(level + 1)
            left = BinaryOp(op=op, left=left, right=right)
        return left

    def _is_operator(self, operators: tuple[str, ...]) -> bool:
        token = self._current
        if token.kind == "operator":
            return token.text in operators
        if token.kind == "identifier":
            return token.text.upper() in operators
        return False

    def _parse_unary(self) -> Node:
        if self._current.kind == "operator" and self._current.text in ("-", "!"):
            op = self._advance().text
            operand = self._parse_unary()
            return UnaryOp(op=op, operand=operand)
        if self._current.kind == "identifier" and self._current.text.upper() == "NOT":
            self._advance()
            return UnaryOp(op="NOT", operand=self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self) -> Node:
        token = self._current
        if token.kind == "number":
            self._advance()
            return Literal(value=float(token.text) if "." in token.text else int(token.text))
        if token.kind == "string":
            self._advance()
            return Literal(value=token.text, kind="string")
        if token.kind == "column":
            self._advance()
            return ColumnRef(column=token.text, table=None)
        if token.kind == "identifier":
            name = self._advance().text
            upper = name.upper()
            # ``Table[Column]`` — the reference is qualified by the
            # identifier in front of the bracket.
            if self._current.kind == "column":
                column = self._advance().text
                return ColumnRef(column=column, table=name)
            if (
                upper in ("TRUE", "FALSE")
                and self._current.kind == "punct"
                and self._tokens[self._pos + 1].text == ")"
            ):
                self._advance()  # '('
                self._advance()  # ')'
                return Literal(value=(upper == "TRUE"), kind="boolean")
            if self._current.kind == "punct" and self._current.text == "(":
                self._advance()
                args = self._parse_arguments()
                return FunctionCall(name=name, args=args)
            return TableRef(name=name)
        if token.kind == "punct" and token.text == "(":
            self._advance()
            node = self._parse_expression()
            self._expect(")")
            return node
        if token.kind == "punct" and token.text == "{":
            return self._parse_table_literal()
        raise DaxParseError(f"unexpected {token.text!r}", token.offset)

    def _parse_table_literal(self) -> TableLiteral:
        self._expect("{")
        rows: list[list[Node]] = []
        current_row: list[Node] = []
        while True:
            if self._current.kind == "punct" and self._current.text == "}":
                self._advance()
                break
            current_row.append(self._parse_expression())
            if self._current.kind == "punct" and self._current.text == ",":
                self._advance()
                continue
            if self._current.kind == "punct" and self._current.text == "}":
                self._advance()
                break
            raise DaxParseError("expected ',' or '}' in a table literal", self._current.offset)
        rows.append(current_row)
        return TableLiteral(rows=rows)

    def _parse_arguments(self) -> list[Node]:
        args: list[Node] = []
        if self._current.kind == "punct" and self._current.text == ")":
            self._advance()
            return args
        while True:
            args.append(self._parse_expression())
            if self._current.kind == "punct" and self._current.text == ",":
                self._advance()
                continue
            self._expect(")")
            return args

    def _expect(self, text: str) -> None:
        if self._current.kind != "punct" or self._current.text != text:
            raise DaxParseError(f"expected {text!r}", self._current.offset)
        self._advance()


def parse_dax(expression: str) -> Node:
    """Parse a DAX expression into a tree.

    Raises:
        DaxParseError: the expression uses something this parser does
            not understand.
    """
    if not expression or not expression.strip():
        raise DaxParseError("the expression is empty", 0)
    return _Parser(_tokenize(expression)).parse()


# ---------------------------------------------------------------------------
# Printer
# ---------------------------------------------------------------------------

_PRECEDENCE: dict[str, int] = {}
for _index, _level in enumerate(_BINARY_LEVELS):
    for _op in _level:
        _PRECEDENCE[_op] = _index
_PRECEDENCE["AND"] = 1
_PRECEDENCE["OR"] = 0


def _print(node: Node, parent_precedence: int = -1) -> str:
    if isinstance(node, Literal):
        if node.kind == "string":
            escaped = str(node.value).replace('"', '""')
            return f'"{escaped}"'
        if node.kind == "boolean":
            return "TRUE()" if node.value else "FALSE()"
        return _format_number(node.value)
    if isinstance(node, ColumnRef):
        return node.qualified
    if isinstance(node, TableRef):
        return node.name
    if isinstance(node, FunctionCall):
        args = ", ".join(_print(arg) for arg in node.args)
        return f"{node.name}({args})"
    if isinstance(node, UnaryOp):
        operand = node.operand
        # A unari dentro de otro unari necesita parentesis: sin ellos
        # "NOT NOT x" no es una expresion.
        text = f"({_print(operand)})" if isinstance(operand, UnaryOp) else _print(operand, 6)
        if node.op == "NOT":
            # ``NOT(x)`` reads better than ``NOT (x)``; a bare operand
            # still needs the space.
            return f"NOT{text}" if text.startswith("(") else f"NOT {text}"
        return f"{node.op}{text}"
    if isinstance(node, BinaryOp):
        precedence = _PRECEDENCE.get(node.op, 0)
        left = _print(node.left, precedence)
        right = _print(node.right, precedence + 1)
        text = f"{left} {node.op} {right}"
        if precedence < parent_precedence:
            return f"({text})"
        return text
    if isinstance(node, VarBlock):
        parts = [f"VAR {name} = {_print(value)}" for name, value in node.variables]
        body = _print(node.body) if node.body is not None else ""
        parts.append(f"RETURN {body}")
        return "\n".join(parts)
    if isinstance(node, TableLiteral):
        rows = []
        for row in node.rows:
            rows.append(", ".join(_print(cell) for cell in row))
        return "{" + ", ".join(rows) + "}"
    raise DaxParseError(f"cannot print {type(node).__name__}", 0)


def _format_number(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def print_dax(node: Node) -> str:
    """Render a tree back to DAX text."""
    return _print(node)
