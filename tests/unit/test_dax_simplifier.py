"""Tests for the DAX simplifier.

Each rewrite has to be right in both directions: applied when it
holds, and *not* applied when it does not. The second half matters
more — a simplifier that rewrites something it should not produces a
measure that no longer computes what it used to.
"""

from __future__ import annotations

import pytest

from powerbi_orchestrator_mcp.validation.dax_simplifier import (
    DaxSimplifier,
    simplify_expression,
)


def _simplify(expression: str) -> str:
    return DaxSimplifier().simplify(expression).simplified


def _rules(expression: str) -> list[str]:
    return [rewrite.rule_id for rewrite in DaxSimplifier().simplify(expression).rewrites]


class TestBlankChecks:
    def test_a_blank_check_with_a_zero_fallback_is_coalesce(self) -> None:
        assert _simplify("IF(ISBLANK([Amount]), 0, [Amount])") == "COALESCE([Amount], 0)"

    def test_a_blank_in_gives_a_blank_out(self) -> None:
        assert _simplify("IF(ISBLANK([Amount]), BLANK(), [Amount])") == "[Amount]"

    def test_a_different_fallback_is_left_alone(self) -> None:
        """COALESCE only carries a zero fallback here.

        A blank check with another fallback is not the same thing, so
        the expression stays as it is.
        """
        original = "IF(ISBLANK([Amount]), -1, [Amount])"

        assert _simplify(original) == original

    def test_a_different_target_is_left_alone(self) -> None:
        original = "IF(ISBLANK([Amount]), 0, [Other])"

        assert _simplify(original) == original


class TestErrorChecks:
    def test_an_error_check_with_a_fallback_is_iferror(self) -> None:
        simplified = _simplify("IF(ISERROR(SUM(X[Amt])), 0, SUM(X[Amt]))")

        assert simplified == "IFERROR(SUM(X[Amt]), 0)"

    def test_it_evaluates_the_expression_once(self) -> None:
        """That is the point of the rewrite, not a side effect.

        The original evaluates ``SUM(X[Amt])`` twice; the rewritten one
        evaluates it once.
        """
        simplified = _simplify("IF(ISERROR(SUM(X[Amt])), 0, SUM(X[Amt]))")

        assert simplified.count("SUM(X[Amt])") == 1

    def test_an_error_check_without_the_same_expression_is_left_alone(self) -> None:
        original = "IF(ISERROR(SUM(X[Amt])), 0, SUM(X[Qty]))"

        assert _simplify(original) == original


class TestBooleanBranches:
    def test_an_if_over_two_booleans_is_the_condition(self) -> None:
        assert _simplify("IF([A] > 0, TRUE(), FALSE())") == "[A] > 0"

    def test_same_branches_collapse(self) -> None:
        assert _simplify("IF([A] > 0, [X], [X])") == "[X]"

    def test_different_branches_are_kept(self) -> None:
        original = "IF([A] > 0, [X], [Y])"

        assert _simplify(original) == original


class TestSwitchChains:
    def test_a_chain_of_tests_becomes_a_switch(self) -> None:
        simplified = _simplify('IF([Status] = "A", 1, IF([Status] = "B", 2, 0))')

        assert simplified == 'SWITCH(TRUE(), [Status] = "A", 1, [Status] = "B", 2, 0)'

    def test_a_three_step_chain(self) -> None:
        simplified = _simplify('IF([S] = "A", 1, IF([S] = "B", 2, IF([S] = "C", 3, 0)))')

        assert simplified.startswith("SWITCH(TRUE()")
        assert simplified.count("[S] =") == 3

    def test_tests_on_different_columns_are_left_alone(self) -> None:
        """A SWITCH over two columns would not mean the same thing."""
        original = 'IF([Status] = "A", 1, IF([Region] = "B", 2, 0))'

        assert _simplify(original) == original

    def test_a_comparison_against_another_column_is_left_alone(self) -> None:
        original = "IF([A] = [B], 1, IF([A] = [C], 2, 0))"

        assert _simplify(original) == original


class TestBooleanOperators:
    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            ("[A] AND TRUE()", "[A]"),
            ("TRUE() AND [A]", "[A]"),
            ("[A] OR FALSE()", "[A]"),
            ("FALSE() OR [A]", "[A]"),
            ("NOT(NOT([A] > 1))", "[A] > 1"),
        ],
    )
    def test_the_tautologies(self, expression: str, expected: str) -> None:
        assert _simplify(expression) == expected

    def test_the_meaningful_operators_are_kept(self) -> None:
        original = "[A] AND [B]"

        assert _simplify(original) == original


class TestHoisting:
    def test_a_repeated_expression_becomes_a_variable(self) -> None:
        simplified = _simplify("SUM(FactSales[Amount]) + SUM(FactSales[Amount])")

        assert simplified.startswith("VAR ")
        assert "RETURN" in simplified
        assert simplified.count("SUM(FactSales[Amount])") == 1

    def test_the_variable_is_used_twice(self) -> None:
        simplified = _simplify("SUM(FactSales[Amount]) + SUM(FactSales[Amount])")

        body = simplified.split("RETURN", 1)[1]
        name = simplified.split("VAR ", 1)[1].split(" =", 1)[0]
        assert body.count(name) == 2

    def test_a_short_expression_is_not_hoisted(self) -> None:
        """A bare column is cheap to repeat; a variable would be noise."""
        original = "[Amount] + [Amount]"

        assert _simplify(original) == original

    def test_an_expression_written_once_is_not_hoisted(self) -> None:
        original = "SUM(FactSales[Amount]) + SUM(FactSales[Other])"

        assert _simplify(original) == original

    def test_occurrences_in_a_calculate_modifier_are_left_alone(self) -> None:
        """The same text in a filter context means something else.

        Hoisting it would compute the sum in the outer context, which
        is not what the original does.
        """
        original = "SUM(X[Amt]) + CALCULATE(SUM(X[Amt]), T[F] = 1)"

        assert _simplify(original) == original

    def test_an_existing_var_block_keeps_its_variables(self) -> None:
        simplified = _simplify(
            "VAR Base = SUM(X[Amt]) RETURN SUM(FactSales[Amount]) + SUM(FactSales[Amount])"
        )

        assert "VAR Base = SUM(X[Amt])" in simplified


class TestTheResult:
    def test_it_reports_whether_it_changed_anything(self) -> None:
        result = DaxSimplifier().simplify("DIVIDE([A], [B], 0)")

        assert result.changed is False
        assert result.rewrites == []

    def test_the_equivalence_is_structural(self) -> None:
        """The expression was never evaluated, so it cannot claim more."""
        result = DaxSimplifier().simplify("IF(ISBLANK([A]), 0, [A])")

        assert result.equivalence == "structural"

    def test_each_rewrite_says_what_it_did(self) -> None:
        result = DaxSimplifier().simplify("IF(ISBLANK([A]), 0, [A])")

        assert len(result.rewrites) == 1
        rewrite = result.rewrites[0]
        assert rewrite.rule_id
        assert rewrite.description
        assert rewrite.before != rewrite.after

    def test_it_is_stable(self) -> None:
        """Simplifying twice gives the same answer.

        A rewrite that exposes a new rewrite is fine; one that never
        settles is not.
        """
        expression = "IF(ISBLANK([A]), 0, [A])"

        once = _simplify(expression)
        twice = _simplify(once)

        assert once == twice

    def test_it_leaves_a_correct_expression_alone(self) -> None:
        original = "DIVIDE(SUM(FactSales[Amount]), SUM(FactSales[Quantity]), 0)"

        assert _simplify(original) == original


class TestTheToolShape:
    def test_it_returns_a_plain_dict(self) -> None:
        result = simplify_expression("IF(ISBLANK([A]), 0, [A])")

        assert result["success"] is True
        assert result["changed"] is True
        assert result["simplified_expression"] == "COALESCE([A], 0)"
        assert result["equivalence"] == "structural"
        assert result["error_message"] is None

    def test_an_unparseable_expression_is_left_alone_and_says_so(self) -> None:
        """It must not guess at something it could not read."""
        result = simplify_expression("SUM(([A]")

        assert result["success"] is False
        assert result["changed"] is False
        assert result["simplified_expression"] == "SUM(([A]"
        assert result["equivalence"] == "not_analysed"
        assert "could not be parsed" in (result["error_message"] or "")
