"""Tests for the DAX parser.

The parser is the foundation the model-refactoring tools stand on: if
it misreads a measure, every rewrite it proposes is wrong. These tests
pin the shapes that matter and, just as importantly, the ones it must
refuse rather than guess at.
"""

from __future__ import annotations

import pytest

from powerbi_orchestrator_mcp.validation.dax_ast import (
    BinaryOp,
    ColumnRef,
    DaxParseError,
    FunctionCall,
    Literal,
    TableRef,
    VarBlock,
    parse_dax,
    print_dax,
)


class TestLiterals:
    def test_an_integer(self) -> None:
        tree = parse_dax("42")
        assert isinstance(tree, Literal)
        assert tree.value == 42
        assert tree.kind == "number"

    def test_a_decimal(self) -> None:
        tree = parse_dax("3.5")
        assert isinstance(tree, Literal)
        assert tree.value == 3.5

    def test_a_string(self) -> None:
        tree = parse_dax('"hello"')
        assert isinstance(tree, Literal)
        assert tree.value == "hello"
        assert tree.kind == "string"

    def test_a_boolean(self) -> None:
        true = parse_dax("TRUE()")
        false = parse_dax("FALSE()")

        assert isinstance(true, Literal)
        assert isinstance(false, Literal)
        assert true.value is True
        assert false.value is False


class TestReferences:
    def test_a_bare_column(self) -> None:
        tree = parse_dax("[Amount]")
        assert isinstance(tree, ColumnRef)
        assert tree.column == "Amount"
        assert tree.table is None

    def test_a_qualified_column(self) -> None:
        tree = parse_dax("FactSales[Amount]")
        assert isinstance(tree, ColumnRef)
        assert tree.column == "Amount"
        assert tree.table == "FactSales"

    def test_a_table_reference(self) -> None:
        tree = parse_dax("FactSales")
        assert isinstance(tree, TableRef)
        assert tree.name == "FactSales"


class TestCalls:
    def test_a_call_with_arguments(self) -> None:
        tree = parse_dax("SUM(FactSales[Amount])")
        assert isinstance(tree, FunctionCall)
        assert tree.name == "SUM"
        assert len(tree.args) == 1

    def test_a_call_with_no_arguments(self) -> None:
        tree = parse_dax("NOW()")
        assert isinstance(tree, FunctionCall)
        assert tree.args == []

    def test_a_call_with_several_arguments(self) -> None:
        tree = parse_dax("DIVIDE([A], [B], 0)")
        assert isinstance(tree, FunctionCall)
        assert len(tree.args) == 3

    def test_nested_calls(self) -> None:
        tree = parse_dax("ROUND(SUM(FactSales[Amount]), 2)")
        assert isinstance(tree, FunctionCall)
        assert isinstance(tree.args[0], FunctionCall)


class TestOperators:
    def test_arithmetic(self) -> None:
        tree = parse_dax("[A] + [B] * 2")
        assert isinstance(tree, BinaryOp)
        assert tree.op == "+"
        assert isinstance(tree.right, BinaryOp)
        assert tree.right.op == "*"

    def test_comparison(self) -> None:
        tree = parse_dax("[A] >= 10")
        assert isinstance(tree, BinaryOp)
        assert tree.op == ">="

    def test_and_binds_tighter_than_or(self) -> None:
        tree = parse_dax("[A] = 1 OR [B] = 2 AND [C] = 3")
        assert isinstance(tree, BinaryOp)
        assert tree.op == "OR"
        assert isinstance(tree.right, BinaryOp)
        assert tree.right.op == "AND"

    def test_not_is_a_unary_operator(self) -> None:
        tree = parse_dax("NOT([A] > 1)")
        assert tree.__class__.__name__ == "UnaryOp"


class TestVarBlocks:
    def test_a_var_block(self) -> None:
        tree = parse_dax("VAR Total = SUM(X[Amt]) RETURN Total * 2")
        assert isinstance(tree, VarBlock)
        assert tree.variables[0][0] == "Total"
        assert tree.body is not None

    def test_several_variables(self) -> None:
        tree = parse_dax("VAR a = 1 VAR b = 2 RETURN a + b")
        assert isinstance(tree, VarBlock)
        assert [name for name, _ in tree.variables] == ["a", "b"]


class TestWhatItRefuses:
    """It must refuse what it does not understand, not guess."""

    def test_an_unterminated_string(self) -> None:
        with pytest.raises(DaxParseError):
            parse_dax('"unterminated')

    def test_an_unterminated_column(self) -> None:
        with pytest.raises(DaxParseError):
            parse_dax("[Amount")

    def test_an_empty_expression(self) -> None:
        with pytest.raises(DaxParseError):
            parse_dax("   ")

    def test_trailing_garbage(self) -> None:
        with pytest.raises(DaxParseError):
            parse_dax("SUM(X[Amt]) )")

    def test_a_var_block_without_return(self) -> None:
        with pytest.raises(DaxParseError):
            parse_dax("VAR a = 1")

    def test_the_error_carries_the_offset(self) -> None:
        with pytest.raises(DaxParseError) as exc:
            parse_dax("SUM(X[Amt]) )")

        assert exc.value.offset == 12


class TestPrinting:
    @pytest.mark.parametrize(
        "expression",
        [
            "SUM(FactSales[Amount])",
            "IF([A] > 1, 2, 3)",
            "DIVIDE([A], [B], 0)",
            "FactSales[Amount] * 2",
            "COALESCE([A], 0)",
            "SUMX(FactSales, FactSales[Amount] * [Rate])",
            "{1, 2, 3}",
            "TRUE()",
        ],
    )
    def test_it_round_trips(self, expression: str) -> None:
        assert print_dax(parse_dax(expression)) == expression

    def test_a_var_block_round_trips(self) -> None:
        expression = "VAR Total = SUM(X[Amt])\nRETURN Total * 2"
        assert print_dax(parse_dax(expression)) == expression

    def test_redundant_parentheses_are_dropped(self) -> None:
        """The printed form has to be equivalent, not identical.

        ``AND`` binds tighter than ``OR``, so the parentheses around it
        carry no meaning and the printed form does not need them.
        """
        printed = print_dax(parse_dax("[A] = 1 OR ([B] = 2 AND [C] = 3)"))

        assert printed == "[A] = 1 OR [B] = 2 AND [C] = 3"

    def test_a_string_is_escaped(self) -> None:
        printed = print_dax(parse_dax('"say ""hi"""'))

        assert printed == '"say ""hi"""'


class TestComments:
    def test_a_line_comment_is_discarded(self) -> None:
        assert print_dax(parse_dax("SUM(X[Amt]) // total")) == "SUM(X[Amt])"

    def test_a_block_comment_is_discarded(self) -> None:
        assert print_dax(parse_dax("SUM(X[Amt]) /* total */")) == "SUM(X[Amt])"

    def test_a_comment_inside_a_call(self) -> None:
        printed = print_dax(parse_dax("SUM( // the amount\n X[Amt])"))

        assert printed == "SUM(X[Amt])"
