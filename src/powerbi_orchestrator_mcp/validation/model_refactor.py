"""Find the logic that is written more than once in a semantic model.

This is the analysis behind the "abstraer" tool. Two measures that
compute the same thing in two different ways are a maintenance
liability: a change to the business rule has to be made in both, and
the day someone forgets is the day the two disagree.

The analysis works on the *shape* of an expression, not its text. A
literal and a column reference are replaced by placeholders, so two
measures that differ only in the year they filter on, or only in the
rate they apply, come out with the same shape — and the difference
between them is reported slot by slot.

Two kinds of duplication come out of it:

``identical``
    The expressions are the same. One measure can serve both, and the
    references to it have to be repointed.

``same_shape``
    The expressions differ only in their literals or their columns.
    This is the case a calculation group usually answers.

What this module does **not** do is apply anything. A Tabular measure
cannot take a parameter, so "one measure with an argument" is not
something that can be written down as a DAX expression; the tool
reports the duplication and the shape of the answer, and the caller
decides whether that is a calculation group, a helper table, or a
rewrite of the measures in place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from powerbi_orchestrator_mcp.validation.dax_ast import (
    BinaryOp,
    ColumnRef,
    DaxParseError,
    FunctionCall,
    Literal,
    Node,
    TableLiteral,
    TableRef,
    UnaryOp,
    VarBlock,
    parse_dax,
    print_dax,
)

__all__ = [
    "DuplicationGroup",
    "MeasureDuplication",
    "find_duplication",
]


@dataclass
class Slot:
    """One place where two otherwise-identical measures differ."""

    index: int
    kind: str  # literal | column
    values: dict[str, str] = field(default_factory=dict)
    """The value each measure has in this slot, by measure name."""

    @property
    def differs(self) -> bool:
        return len(set(self.values.values())) > 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "kind": self.kind,
            "values": self.values,
            "differs": self.differs,
        }


@dataclass
class DuplicationGroup:
    """A set of measures that share the same shape."""

    kind: str  # identical | same_shape
    measures: list[str]
    skeleton: str
    slots: list[Slot] = field(default_factory=list)
    expression: str | None = None
    """The shared expression, for the ``identical`` case."""

    @property
    def differing_slots(self) -> list[Slot]:
        return [slot for slot in self.slots if slot.differs]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "measures": self.measures,
            "measure_count": len(self.measures),
            "expression": self.expression,
            "skeleton": self.skeleton,
            "slots": [slot.to_dict() for slot in self.slots],
            "differing_slot_count": len(self.differing_slots),
        }


@dataclass
class MeasureDuplication:
    """Everything the analysis found."""

    groups: list[DuplicationGroup] = field(default_factory=list)
    measures_examined: int = 0
    measures_skipped: int = 0
    """Measures whose expression could not be parsed.

    They are counted rather than dropped, so a caller can tell the
    difference between "no duplication" and "we could not look".
    """
    skipped: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "groups": [group.to_dict() for group in self.groups],
            "group_count": len(self.groups),
            "measures_examined": self.measures_examined,
            "measures_skipped": self.measures_skipped,
            "skipped": self.skipped,
        }


@dataclass
class _Shape:
    """What one measure looks like."""

    name: str
    expression: str
    normalised: str
    skeleton: str
    slots: list[tuple[str, str]]


def _literal_text(node: Literal) -> str:
    if node.kind == "string":
        return f'"{node.value}"'
    if node.kind == "boolean":
        return "TRUE()" if node.value else "FALSE()"
    return str(node.value)


def _skeleton(node: Node) -> str:
    """Render the shape of an expression.

    A literal becomes ``<lit>`` and a column reference becomes
    ``<col>``; everything else keeps its shape. Two expressions with
    the same skeleton differ only in what was replaced.
    """
    if isinstance(node, Literal):
        return "<lit>"
    if isinstance(node, ColumnRef):
        return "<col>"
    if isinstance(node, TableRef):
        return node.name
    if isinstance(node, FunctionCall):
        args = ", ".join(_skeleton(arg) for arg in node.args)
        return f"{node.name}({args})"
    if isinstance(node, UnaryOp):
        operand = _skeleton(node.operand)
        if node.op == "NOT":
            return f"NOT{operand}" if operand.startswith("(") else f"NOT {operand}"
        return f"{node.op}{operand}"
    if isinstance(node, BinaryOp):
        left = _skeleton(node.left)
        right = _skeleton(node.right)
        return f"({left} {node.op} {right})"
    if isinstance(node, VarBlock):
        parts = [f"VAR {name} = {_skeleton(value)}" for name, value in node.variables]
        body = _skeleton(node.body) if node.body is not None else ""
        parts.append(f"RETURN {body}")
        return " ".join(parts)
    if isinstance(node, TableLiteral):
        rows = [", ".join(_skeleton(cell) for cell in row) for row in node.rows]
        return "{" + ", ".join(rows) + "}"
    return print_dax(node)


def _slot_values(node: Node) -> list[tuple[str, str]]:
    """The literal and column values of an expression, in order."""
    found: list[tuple[str, str]] = []

    def visit(current: Node) -> None:
        if isinstance(current, Literal):
            found.append(("literal", _literal_text(current)))
            return
        if isinstance(current, ColumnRef):
            found.append(("column", current.qualified))
            return
        if isinstance(current, FunctionCall):
            for arg in current.args:
                visit(arg)
            return
        if isinstance(current, BinaryOp):
            visit(current.left)
            visit(current.right)
            return
        if isinstance(current, UnaryOp):
            visit(current.operand)
            return
        if isinstance(current, VarBlock):
            for _, value in current.variables:
                visit(value)
            if current.body is not None:
                visit(current.body)
            return
        if isinstance(current, TableLiteral):
            for row in current.rows:
                for cell in row:
                    visit(cell)
            return

    visit(node)
    return found


def _normalise_whitespace(expression: str) -> str:
    return " ".join(expression.split())


def find_duplication(
    measures: dict[str, str],
    *,
    min_group_size: int = 2,
) -> MeasureDuplication:
    """Find the measures that compute the same thing.

    Args:
        measures: The measure name → DAX expression map.
        min_group_size: How many measures have to share a shape before
            it is worth reporting.

    Returns:
        The groups found, plus the measures that could not be parsed.
    """
    if min_group_size < 2:
        raise ValueError(f"min_group_size must be at least 2, got {min_group_size}")

    result = MeasureDuplication()
    shapes: list[_Shape] = []

    for name, expression in measures.items():
        result.measures_examined += 1
        try:
            tree = parse_dax(expression)
        except DaxParseError as exc:
            result.measures_skipped += 1
            result.skipped[name] = str(exc)
            continue
        shapes.append(
            _Shape(
                name=name,
                expression=expression,
                normalised=_normalise_whitespace(expression),
                skeleton=_skeleton(tree),
                slots=_slot_values(tree),
            )
        )

    by_expression: dict[str, list[_Shape]] = {}
    by_skeleton: dict[str, list[_Shape]] = {}
    for shape in shapes:
        by_expression.setdefault(shape.normalised, []).append(shape)
        by_skeleton.setdefault(shape.skeleton, []).append(shape)

    # The identical groups come first: they are the cheapest to fix.
    identical_skeletons: set[str] = set()
    for expression, group in sorted(by_expression.items()):
        if len(group) < min_group_size:
            continue
        identical_skeletons.add(group[0].skeleton)
        result.groups.append(
            DuplicationGroup(
                kind="identical",
                measures=sorted(shape.name for shape in group),
                skeleton=group[0].skeleton,
                expression=expression,
            )
        )

    # Then the ones that share a shape but not the text.
    for skeleton, group in sorted(by_skeleton.items()):
        if len(group) < min_group_size or skeleton in identical_skeletons:
            continue
        slots: list[Slot] = []
        width = max(len(shape.slots) for shape in group)
        for index in range(width):
            merged = Slot(index=index, kind="literal")
            for shape in group:
                if index < len(shape.slots):
                    kind, value = shape.slots[index]
                    merged.kind = kind
                    merged.values[shape.name] = value
            slots.append(merged)
        result.groups.append(
            DuplicationGroup(
                kind="same_shape",
                measures=sorted(shape.name for shape in group),
                skeleton=skeleton,
                slots=slots,
            )
        )

    return result
