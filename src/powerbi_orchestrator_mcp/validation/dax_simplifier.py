"""Simplify a DAX measure into an equivalent, shorter expression.

This is the "simplificar" half of the model-refactoring tools. It reads
a DAX expression, finds the parts that are written the long way, and
rewrites them.

Every rewrite here is **structural**: it changes the shape of the
expression, not what it computes. The result says so explicitly,
because an expression that was never evaluated against data cannot
claim to be equivalent — only that the rewrite is one the reader can
check by eye.

The rewrites, and why each one holds:

``IF(ISBLANK(x), 0, x)`` → ``COALESCE(x, 0)``
    Both return 0 for a blank and the value otherwise.

``IF(ISERROR(e), fallback, e)`` → ``IFERROR(e, fallback)``
    Same result, and ``e`` is evaluated once instead of twice.

``IF(c, TRUE(), FALSE())`` → ``c``
    ``IF`` with two boolean branches returns the condition.

``IF(c, a, a)`` → ``a``
    Both branches are the same, so the condition does not matter.

``IF(ISBLANK(x), BLANK(), x)`` → ``x``
    A blank in, a blank out.

``IF(a = 1, x, IF(a = 2, y, z))`` → ``SWITCH(TRUE(), a = 1, x, a = 2, y, z)``
    A chain of tests on the same expression. ``SWITCH`` reads it as the
    list it is.

``x AND TRUE()`` → ``x``, ``x OR FALSE()`` → ``x``
    A tautology on either side.

``NOT(NOT(x))`` → ``x``
    Double negation.

``a + a`` → ``VAR _a = a RETURN _a + _a``
    A subexpression written twice is computed twice. Hoisting it into a
    variable computes it once. The occurrences must all sit in the same
    context, so anything inside a ``CALCULATE`` modifier is left alone.
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
    "DaxRewrite",
    "DaxSimplifier",
    "SimplifyResult",
]


@dataclass
class DaxRewrite:
    """One rewrite that was applied."""

    rule_id: str
    description: str
    before: str
    after: str


@dataclass
class SimplifyResult:
    """What the simplifier did."""

    original: str
    simplified: str
    rewrites: list[DaxRewrite] = field(default_factory=list)
    equivalence: str = "structural"
    """How the equivalence of the result was established.

    ``structural`` means the rewrite changes the shape and not the
    meaning, and that the expression was never evaluated against data.
    """

    @property
    def changed(self) -> bool:
        return self.original != self.simplified


def _same(left: Node, right: Node) -> bool:
    """True when two nodes print the same."""
    return print_dax(left) == print_dax(right)


def _is_call(node: Node, name: str) -> bool:
    return isinstance(node, FunctionCall) and node.name.upper() == name.upper()


def _is_true(node: Node) -> bool:
    return isinstance(node, Literal) and node.kind == "boolean" and node.value is True


def _is_false(node: Node) -> bool:
    return isinstance(node, Literal) and node.kind == "boolean" and node.value is False


def _is_blank(node: Node) -> bool:
    return isinstance(node, FunctionCall) and node.name.upper() == "BLANK"


class DaxSimplifier:
    """Rewrites a DAX expression into a simpler equivalent one."""

    #: A subexpression is only worth hoisting into a variable when it is
    #: this long or longer. Anything shorter is noise in the output.
    MIN_HOIST_LENGTH = 12

    #: How many times a subexpression has to appear before it is worth
    #: a variable.
    MIN_HOIST_OCCURRENCES = 2

    def simplify(self, expression: str) -> SimplifyResult:
        """Simplify ``expression``.

        Raises:
            DaxParseError: the expression is not something the parser
                understands. The caller is expected to leave it alone
                rather than rewrite something it could not read.
        """
        tree = parse_dax(expression)
        rewrites: list[DaxRewrite] = []

        # Rewrites are applied until nothing changes, so a rule that
        # exposes another one gets its chance too.
        for _ in range(10):
            before = print_dax(tree)
            tree, applied = self._rewrite_once(tree)
            rewrites.extend(applied)
            if print_dax(tree) == before:
                break

        simplified = print_dax(tree)
        return SimplifyResult(
            original=expression,
            simplified=simplified,
            rewrites=rewrites,
        )

    # -- the rewrite loop ------------------------------------------------
    def _rewrite_once(self, node: Node) -> tuple[Node, list[DaxRewrite]]:
        rewrites: list[DaxRewrite] = []
        node = self._rewrite_node(node, rewrites)
        node = self._hoist_repeated(node, rewrites)
        return node, rewrites

    def _rewrite_node(self, node: Node, rewrites: list[DaxRewrite]) -> Node:
        """Rewrite this node and, depth first, its children."""
        if isinstance(node, FunctionCall):
            # The chain rule looks at the whole else-branch, so it has
            # to run before the children are rewritten: rewriting the
            # inner IF into a SWITCH first would hide the chain.
            if node.name.upper() == "IF" and len(node.args) == 3:
                chained = self._rewrite_if_chain(node, rewrites)
                if chained is not None:
                    return chained
            node.args = [self._rewrite_node(arg, rewrites) for arg in node.args]
            return self._rewrite_call(node, rewrites)
        if isinstance(node, BinaryOp):
            node.left = self._rewrite_node(node.left, rewrites)
            node.right = self._rewrite_node(node.right, rewrites)
            return self._rewrite_binary(node, rewrites)
        if isinstance(node, UnaryOp):
            node.operand = self._rewrite_node(node.operand, rewrites)
            return self._rewrite_unary(node, rewrites)
        if isinstance(node, VarBlock):
            node.variables = [
                (name, self._rewrite_node(value, rewrites)) for name, value in node.variables
            ]
            if node.body is not None:
                node.body = self._rewrite_node(node.body, rewrites)
            return node
        if isinstance(node, TableLiteral):
            node.rows = [[self._rewrite_node(cell, rewrites) for cell in row] for row in node.rows]
            return node
        return node

    # -- individual rules ------------------------------------------------
    def _rewrite_call(self, node: FunctionCall, rewrites: list[DaxRewrite]) -> Node:
        name = node.name.upper()

        if name == "IF" and len(node.args) == 3:
            rewritten = self._rewrite_if(node, rewrites)
            if rewritten is not None:
                return rewritten

        if name == "COALESCE" and len(node.args) == 2:
            first, second = node.args
            if isinstance(second, FunctionCall) and _is_call(second, "BLANK"):
                rewrites.append(
                    DaxRewrite(
                        rule_id="DAX_COALESCE_BLANK",
                        description="COALESCE with a blank fallback always takes the first argument",
                        before=print_dax(node),
                        after=print_dax(first),
                    )
                )
                return first

        return node

    def _rewrite_if(self, node: FunctionCall, rewrites: list[DaxRewrite]) -> Node | None:
        condition, when_true, when_false = node.args

        # IF(c, a, a) -> a
        if _same(when_true, when_false):
            rewrites.append(
                DaxRewrite(
                    rule_id="DAX_IF_SAME_BRANCHES",
                    description="both branches are the same, so the condition does not matter",
                    before=print_dax(node),
                    after=print_dax(when_true),
                )
            )
            return when_true

        # IF(c, TRUE(), FALSE()) -> c
        if _is_true(when_true) and _is_false(when_false):
            rewrites.append(
                DaxRewrite(
                    rule_id="DAX_IF_BOOLEAN",
                    description="an IF over two booleans is the condition",
                    before=print_dax(node),
                    after=print_dax(condition),
                )
            )
            return condition

        # IF(ISBLANK(x), 0, x) -> COALESCE(x, 0)
        if isinstance(condition, FunctionCall) and _is_call(condition, "ISBLANK"):
            if len(condition.args) != 1:
                return self._rewrite_if_chain(node, rewrites)
            target = condition.args[0]
            if (
                _same(when_false, target)
                and isinstance(when_true, Literal)
                and when_true.kind == "number"
                and when_true.value == 0
            ):
                replacement = FunctionCall(name="COALESCE", args=[target, when_true])
                rewrites.append(
                    DaxRewrite(
                        rule_id="DAX_IF_ISBLANK_COALESCE",
                        description="a blank check with a zero fallback is COALESCE",
                        before=print_dax(node),
                        after=print_dax(replacement),
                    )
                )
                return replacement

        # IF(ISBLANK(x), BLANK(), x) -> x
        if isinstance(condition, FunctionCall) and _is_call(condition, "ISBLANK"):
            if len(condition.args) != 1:
                return self._rewrite_if_chain(node, rewrites)
            target = condition.args[0]
            if _is_blank(when_true) and _same(when_false, target):
                rewrites.append(
                    DaxRewrite(
                        rule_id="DAX_IF_ISBLANK_IDENTITY",
                        description="a blank in gives a blank out, so the check is a no-op",
                        before=print_dax(node),
                        after=print_dax(target),
                    )
                )
                return target

        # IF(ISERROR(e), fallback, e) -> IFERROR(e, fallback)
        if isinstance(condition, FunctionCall) and _is_call(condition, "ISERROR"):
            if len(condition.args) != 1:
                return self._rewrite_if_chain(node, rewrites)
            target = condition.args[0]
            if _same(when_false, target):
                replacement = FunctionCall(name="IFERROR", args=[target, when_true])
                rewrites.append(
                    DaxRewrite(
                        rule_id="DAX_IF_ISERROR_IFERROR",
                        description="an error check with a fallback is IFERROR, and it evaluates once",
                        before=print_dax(node),
                        after=print_dax(replacement),
                    )
                )
                return replacement

        return node

    def _rewrite_if_chain(self, node: FunctionCall, rewrites: list[DaxRewrite]) -> Node | None:
        """``IF(a = 1, x, IF(a = 2, y, z))`` → ``SWITCH(TRUE(), ...)``."""
        condition, when_true, when_false = node.args
        if not isinstance(when_false, FunctionCall) or when_false.name.upper() != "IF":
            return None
        if len(when_false.args) != 3:
            return None

        base = _switch_base(condition)
        if base is None:
            return None

        pairs: list[tuple[Node, Node]] = []
        current_condition: Node = condition
        current_true: Node = when_true
        current_false: Node = when_false
        while True:
            this_base = _switch_base(current_condition)
            if this_base is None or not _same(this_base, base):
                return None
            pairs.append((current_condition, current_true))
            if isinstance(current_false, FunctionCall) and current_false.name.upper() == "IF":
                if len(current_false.args) != 3:
                    return None
                next_base = _switch_base(current_false.args[0])
                if next_base is None or not _same(next_base, base):
                    return None
                current_condition = current_false.args[0]
                current_true = current_false.args[1]
                current_false = current_false.args[2]
                continue
            # The last value is the default.
            default = current_false
            break

        switch_args: list[Node] = [FunctionCall(name="TRUE", args=[])]
        for test, value in pairs:
            switch_args.append(test)
            switch_args.append(value)
        switch_args.append(default)
        replacement = FunctionCall(name="SWITCH", args=switch_args)
        rewrites.append(
            DaxRewrite(
                rule_id="DAX_IF_CHAIN_SWITCH",
                description=("a chain of tests on the same expression reads as a SWITCH"),
                before=print_dax(node),
                after=print_dax(replacement),
            )
        )
        return replacement

    def _rewrite_binary(self, node: BinaryOp, rewrites: list[DaxRewrite]) -> Node:
        op = node.op.upper()

        # x AND TRUE() -> x ; TRUE() AND x -> x
        if op == "AND":
            if _is_true(node.right):
                rewrites.append(
                    DaxRewrite(
                        rule_id="DAX_AND_TRUE",
                        description="conjunction with TRUE() is the other side",
                        before=print_dax(node),
                        after=print_dax(node.left),
                    )
                )
                return node.left
            if _is_true(node.left):
                rewrites.append(
                    DaxRewrite(
                        rule_id="DAX_AND_TRUE",
                        description="conjunction with TRUE() is the other side",
                        before=print_dax(node),
                        after=print_dax(node.right),
                    )
                )
                return node.right

        # x OR FALSE() -> x ; FALSE() OR x -> x
        if op == "OR":
            if _is_false(node.right):
                rewrites.append(
                    DaxRewrite(
                        rule_id="DAX_OR_FALSE",
                        description="disjunction with FALSE() is the other side",
                        before=print_dax(node),
                        after=print_dax(node.left),
                    )
                )
                return node.left
            if _is_false(node.left):
                rewrites.append(
                    DaxRewrite(
                        rule_id="DAX_OR_FALSE",
                        description="disjunction with FALSE() is the other side",
                        before=print_dax(node),
                        after=print_dax(node.right),
                    )
                )
                return node.right

        return node

    def _rewrite_unary(self, node: UnaryOp, rewrites: list[DaxRewrite]) -> Node:
        if node.op == "NOT" and isinstance(node.operand, UnaryOp) and node.operand.op == "NOT":
            rewrites.append(
                DaxRewrite(
                    rule_id="DAX_NOT_NOT",
                    description="a double negation is the original condition",
                    before=print_dax(node),
                    after=print_dax(node.operand.operand),
                )
            )
            return node.operand.operand
        return node

    # -- hoisting a repeated subexpression -------------------------------
    def _hoist_repeated(self, node: Node, rewrites: list[DaxRewrite]) -> Node:
        """Compute a subexpression that appears twice only once."""
        candidates = self._repeated_subexpressions(node)
        if not candidates:
            return node

        target = candidates[0]
        name = self._variable_name(target)
        # A variable is referenced by its bare name, not as a call.
        replacement = TableRef(name=name)

        body = node.body if isinstance(node, VarBlock) else node
        if body is None:
            return node
        replaced_body = _substitute(body, target, replacement)
        if replaced_body is None:
            return node

        before = print_dax(node)
        if isinstance(node, VarBlock):
            variables = [*node.variables, (name, target)]
            new_node: Node = VarBlock(variables=variables, body=replaced_body)
        else:
            new_node = VarBlock(variables=[(name, target)], body=replaced_body)

        rewrites.append(
            DaxRewrite(
                rule_id="DAX_REPEAT_TO_VAR",
                description=(
                    "the same expression appears more than once, so it is "
                    "computed once into a variable"
                ),
                before=before,
                after=print_dax(new_node),
            )
        )
        return new_node

    def _repeated_subexpressions(self, node: Node) -> list[Node]:
        """Subexpressions that appear more than once, and are worth a variable.

        Two rules keep this honest:

        - Only occurrences in the same context count. Anything inside a
          ``CALCULATE`` modifier is skipped: there the same text can
          mean different things depending on the filter context, and
          hoisting it would change the result.
        - A smaller expression contained in a bigger one that also
          repeats is not a candidate of its own. Hoisting the bigger
          one covers it.
        """
        counts: dict[str, int] = {}
        first_occurrence: dict[str, Node] = {}
        contained_in: dict[str, set[str]] = {}

        def visit(current: Node, in_modifier: bool, ancestors: tuple[str, ...]) -> None:
            text = print_dax(current)
            if not in_modifier and len(text) >= self.MIN_HOIST_LENGTH:
                counts[text] = counts.get(text, 0) + 1
                first_occurrence.setdefault(text, current)
                contained_in.setdefault(text, set()).update(ancestors)

            children: list[tuple[Node, bool]] = []
            if isinstance(current, FunctionCall):
                for index, arg in enumerate(current.args):
                    # A CALCULATE's first argument is the expression; the
                    # rest are modifiers and change the context.
                    modifier = in_modifier or (current.name.upper() == "CALCULATE" and index >= 1)
                    children.append((arg, modifier))
            elif isinstance(current, BinaryOp):
                children.append((current.left, in_modifier))
                children.append((current.right, in_modifier))
            elif isinstance(current, UnaryOp):
                children.append((current.operand, in_modifier))
            elif isinstance(current, VarBlock):
                for _, value in current.variables:
                    children.append((value, in_modifier))
                if current.body is not None:
                    children.append((current.body, in_modifier))
            elif isinstance(current, TableLiteral):
                for row in current.rows:
                    for cell in row:
                        children.append((cell, in_modifier))

            next_ancestors = (*ancestors, text)
            for child, modifier in children:
                visit(child, modifier, next_ancestors)

        visit(node, False, ())

        repeated: list[Node] = []
        for text, count in counts.items():
            if count < self.MIN_HOIST_OCCURRENCES:
                continue
            # Skip a candidate that sits inside another candidate that
            # repeats as often: the bigger one covers it.
            if any(counts.get(ancestor, 0) >= count for ancestor in contained_in[text]):
                continue
            repeated.append(first_occurrence[text])

        # Longest first: hoisting the biggest repetition first is the
        # one worth doing.
        repeated.sort(key=lambda item: len(print_dax(item)), reverse=True)
        return repeated

    def _variable_name(self, node: Node) -> str:
        """A readable name for the hoisted expression."""
        if isinstance(node, ColumnRef):
            base = node.column
        elif isinstance(node, FunctionCall):
            base = node.name.lower()
        else:
            base = "expr"
        cleaned = "".join(char if char.isalnum() or char == "_" else "_" for char in base)
        if not cleaned or cleaned[0].isdigit():
            cleaned = f"v_{cleaned}"
        return f"__{cleaned}"


def _switch_base(condition: Node) -> Node | None:
    """The expression a SWITCH chain would compare against.

    Only a comparison against a literal qualifies: ``[Status] = "A"``.
    A comparison against another column does not, because the two
    branches would have to agree on which column they mean.
    """
    if not isinstance(condition, BinaryOp):
        return None
    if condition.op not in ("=", "<>", "<", ">", "<=", ">="):
        return None
    left, right = condition.left, condition.right
    if isinstance(left, Literal) and isinstance(right, ColumnRef):
        return right
    if isinstance(right, Literal) and isinstance(left, ColumnRef):
        return left
    return None


def _substitute(node: Node, target: Node, replacement: Node) -> Node | None:
    """Replace every occurrence of ``target`` with ``replacement``.

    Returns ``None`` when the substitution could not be made safely,
    which happens when the occurrences sit inside a ``CALCULATE``
    modifier: there the same text can mean a different thing.
    """
    target_text = print_dax(target)

    def visit(current: Node, in_modifier: bool) -> Node | None:
        # The comparison comes first: a node that matches is replaced
        # whole, and its children are not visited. Comparing at the end
        # would never reach a function call, because the branch above
        # returns before getting there.
        if not in_modifier and print_dax(current) == target_text:
            return replacement
        if isinstance(current, FunctionCall):
            args: list[Node] = []
            for index, arg in enumerate(current.args):
                modifier = in_modifier or (current.name.upper() == "CALCULATE" and index >= 1)
                if modifier:
                    args.append(arg)
                    continue
                new_arg = visit(arg, in_modifier)
                if new_arg is None:
                    return None
                args.append(new_arg)
            return FunctionCall(name=current.name, args=args)
        if isinstance(current, BinaryOp):
            left = visit(current.left, in_modifier)
            right = visit(current.right, in_modifier)
            if left is None or right is None:
                return None
            return BinaryOp(op=current.op, left=left, right=right)
        if isinstance(current, UnaryOp):
            operand = visit(current.operand, in_modifier)
            if operand is None:
                return None
            return UnaryOp(op=current.op, operand=operand)
        if isinstance(current, VarBlock):
            variables = []
            for name, value in current.variables:
                new_value = visit(value, in_modifier)
                if new_value is None:
                    return None
                variables.append((name, new_value))
            body = None
            if current.body is not None:
                body = visit(current.body, in_modifier)
                if body is None:
                    return None
            return VarBlock(variables=variables, body=body)
        if isinstance(current, TableLiteral):
            rows = []
            for row in current.rows:
                cells = []
                for cell in row:
                    new_cell = visit(cell, in_modifier)
                    if new_cell is None:
                        return None
                    cells.append(new_cell)
                rows.append(cells)
            return TableLiteral(rows=rows)

        return current

    return visit(node, False)


def simplify_expression(expression: str) -> dict[str, Any]:
    """Simplify an expression and return a plain dict.

    The shape the MCP tools return.
    """
    try:
        result = DaxSimplifier().simplify(expression)
    except DaxParseError as exc:
        return {
            "success": False,
            "original_expression": expression,
            "simplified_expression": expression,
            "rewrites": [],
            "changed": False,
            "equivalence": "not_analysed",
            "error_message": (f"the expression could not be parsed, so it was left alone: {exc}"),
        }
    return {
        "success": True,
        "original_expression": result.original,
        "simplified_expression": result.simplified,
        "rewrites": [
            {
                "rule_id": rewrite.rule_id,
                "description": rewrite.description,
                "before": rewrite.before,
                "after": rewrite.after,
            }
            for rewrite in result.rewrites
        ],
        "changed": result.changed,
        "equivalence": result.equivalence,
        "error_message": None,
    }
