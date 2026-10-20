"""Tests for the DAX linter rules.

The linter is the tool a data analyst runs every day, so a rule that
fires on correct DAX trains people to ignore it, and a rule that does
not fire on a real anti-pattern is worse than no rule at all.

These tests pin both directions: the anti-patterns are caught, and the
correct DAX is left alone.
"""

from __future__ import annotations

from powerbi_orchestrator_mcp.validation.dax_linter import DaxLinter


def _rules(expression: str) -> set[str]:
    return {finding.rule_id for finding in DaxLinter().lint(expression)}


class TestSummarizeForAggregation:
    def test_a_summarize_with_an_aggregation_is_flagged(self) -> None:
        assert "BP_SUMMARIZE_FOR_AGG" in _rules(
            'SUMMARIZE(FactSales, "Region", DimRegion[Region], SUM(FactSales[Amount]))'
        )

    def test_an_aggregation_outside_summarize_is_not_flagged(self) -> None:
        """The aggregation has to be inside the SUMMARIZE.

        The alternation used to sit outside the parenthesis group, so
        any AVERAGE( or COUNT( in the expression fired the rule.
        """
        assert "BP_SUMMARIZE_FOR_AGG" not in _rules("AVERAGE([Sales Amount])")
        assert "BP_SUMMARIZE_FOR_AGG" not in _rules("COUNTROWS(FactSales)")
        assert "BP_SUMMARIZE_FOR_AGG" not in _rules("SUM(FactSales[Amount])")

    def test_summarizecolumns_is_not_flagged(self) -> None:
        assert "BP_SUMMARIZE_FOR_AGG" not in _rules(
            'SUMMARIZECOLUMNS(DimRegion[Region], FILTER(FactSales, FactSales[Amount] > 0), "Total", SUM(FactSales[Amount]))'
        )


class TestDivideVsSlash:
    def test_a_division_in_the_middle_is_flagged(self) -> None:
        """The rule used to be anchored to the end of the line."""
        assert "BP_DIVIDE_VS_SLASH" in _rules("IF([X] > 0, [N] / [D], 0)")

    def test_a_division_at_the_end_is_flagged(self) -> None:
        assert "BP_DIVIDE_VS_SLASH" in _rules("SUM([X]) / SUM([Y])")

    def test_a_division_between_measures_is_flagged(self) -> None:
        assert "BP_DIVIDE_VS_SLASH" in _rules("[Total] / [Units]")

    def test_divide_is_not_flagged(self) -> None:
        assert "BP_DIVIDE_VS_SLASH" not in _rules("DIVIDE([N], [D], 0)")

    def test_a_slash_in_a_comment_is_not_a_division(self) -> None:
        assert "BP_DIVIDE_VS_SLASH" not in _rules("// divide by two\n[N]")

    def test_a_slash_in_a_block_comment_is_not_a_division(self) -> None:
        assert "BP_DIVIDE_VS_SLASH" not in _rules("/* a / b\nc */")

    def test_a_division_after_a_block_comment_is_still_flagged(self) -> None:
        rules = _rules("/* a / b\nc */\n[N] / [D]")
        assert "BP_DIVIDE_VS_SLASH" in rules
        # Exactly one division: the one that is really there.
        divisions = [
            f
            for f in DaxLinter().lint("/* a / b\nc */\n[N] / [D]")
            if f.rule_id == "BP_DIVIDE_VS_SLASH"
        ]
        assert len(divisions) == 1

    def test_a_division_after_a_comment_is_still_flagged(self) -> None:
        rules = _rules("// a / b\n[N] / [D]")
        assert "BP_DIVIDE_VS_SLASH" in rules


class TestFilterIsfiltered:
    def test_a_filter_on_a_whole_table_is_flagged(self) -> None:
        assert "BP_FILTER_ISFILTERED" in _rules(
            "CALCULATE([Total], FILTER(FactSales, FactSales[Amount] > 0))"
        )

    def test_a_filter_on_isfiltered_is_not_flagged(self) -> None:
        """The rule only applies when the filter is not on ISFILTERED."""
        assert "BP_FILTER_ISFILTERED" not in _rules(
            "CALCULATE([Total], FILTER(FactSales, ISFILTERED(FactSales[ProductKey])))"
        )


class TestIferrorMisuse:
    def test_iferror_around_a_non_failing_function_is_flagged(self) -> None:
        """IFERROR around SUM is the anti-pattern the rule is about."""
        assert "BP_IFERROR_MISUSE" in _rules("IFERROR(SUM([X]), 0)")


class TestLineNumbers:
    def test_the_line_number_points_at_the_match(self) -> None:
        findings = DaxLinter().lint("VAR x = 1\n[N] / [D]")
        assert findings
        assert findings[0].line_number == 2
        assert "/" in (findings[0].matched_text or "")
