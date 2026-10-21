"""Tests for the simplify_dax_measure tool.

The tool is the user-facing half of the simplifier: it has to pick the
right expression, report what it did, and refuse the inputs that make
no sense.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.tools.simplify_dax_measure import simplify_dax_measure


@pytest.fixture()
def pbip(tmp_path: Path) -> Path:
    """A PBIP project with three measures, one of them simplifiable."""
    root = tmp_path / "demo.pbip"
    root.mkdir()
    (root / "demo.pbip").write_text("{}", encoding="utf-8")
    report = root / "demo.Report"
    report.mkdir()
    (report / "report.json").write_text("{}", encoding="utf-8")

    dataset = root / "demo.Dataset"
    dataset.mkdir()
    (dataset / "definition.pbism").write_text(
        json.dumps(
            {
                "model": {
                    "tables": [
                        {
                            "name": "FactSales",
                            "columns": [{"name": "Amount", "dataType": "decimal"}],
                            "measures": [
                                {
                                    "name": "Total Amount",
                                    "expression": "SUM(FactSales[Amount])",
                                },
                                {
                                    "name": "Safe Amount",
                                    "expression": "IF(ISBLANK([Amount]), 0, [Amount])",
                                },
                                {
                                    "name": "Margin",
                                    "expression": "IF(ISBLANK([Amount]), 0, [Amount]) * 2",
                                },
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    return root


class TestAnExpression:
    def test_it_simplifies(self) -> None:
        result = simplify_dax_measure(expression="IF(ISBLANK([A]), 0, [A])")

        assert result["success"] is True
        assert result["mode"] == "expression"
        assert result["simplified_expression"] == "COALESCE([A], 0)"
        assert result["changed"] is True

    def test_it_lists_the_rewrites(self) -> None:
        result = simplify_dax_measure(expression="IF(ISBLANK([A]), 0, [A])")

        assert len(result["rewrites"]) == 1
        assert result["rewrites"][0]["rule_id"] == "DAX_IF_ISBLANK_COALESCE"

    def test_it_leaves_a_correct_expression_alone(self) -> None:
        result = simplify_dax_measure(expression="DIVIDE([A], [B], 0)")

        assert result["changed"] is False
        assert result["simplified_expression"] == "DIVIDE([A], [B], 0)"
        assert result["rewrites"] == []

    def test_it_reports_the_linter_verdict_before_and_after(self) -> None:
        result = simplify_dax_measure(expression="IF(ISBLANK([A]), 0, [A])")

        assert "lint_before" in result
        assert "lint_after" in result

    def test_the_linter_can_be_left_out(self) -> None:
        result = simplify_dax_measure(
            expression="IF(ISBLANK([A]), 0, [A])",
            apply_lint=False,
        )

        assert "lint_before" not in result

    def test_the_equivalence_is_structural(self) -> None:
        result = simplify_dax_measure(expression="IF(ISBLANK([A]), 0, [A])")

        assert result["equivalence"] == "structural"

    def test_an_unparseable_expression_is_left_alone(self) -> None:
        result = simplify_dax_measure(expression="SUM(([A]")

        assert result["success"] is False
        assert result["changed"] is False
        assert result["simplified_expression"] == "SUM(([A]"
        assert result["equivalence"] == "not_analysed"


class TestAMeasure:
    def test_it_reads_the_measure(self, pbip: Path) -> None:
        result = simplify_dax_measure(pbip_path=str(pbip), measure="Safe Amount")

        assert result["success"] is True
        assert result["mode"] == "measure"
        assert result["measure"] == "Safe Amount"
        assert result["table"] == "FactSales"
        assert result["simplified_expression"] == "COALESCE([Amount], 0)"

    def test_it_says_which_measures_exist_when_the_name_is_wrong(self, pbip: Path) -> None:
        result = simplify_dax_measure(pbip_path=str(pbip), measure="Nope")

        assert result["success"] is False
        assert "no measure named" in (result["error_message"] or "")
        assert "Safe Amount" in result["available_measures"]

    def test_a_project_that_cannot_be_read_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The workspace root is the working directory by default, and
        # a temporary directory is outside it, so the path is refused
        # by the boundary before it is even looked for.
        monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(tmp_path))
        result = simplify_dax_measure(pbip_path=str(tmp_path / "nope"), measure="X")

        assert result["success"] is False
        assert "does not exist" in (result["error_message"] or "")


class TestEveryMeasure:
    def test_it_reports_which_measures_can_be_written_shorter(self, pbip: Path) -> None:
        result = simplify_dax_measure(pbip_path=str(pbip), all_measures=True)

        assert result["success"] is True
        assert result["mode"] == "project"
        assert result["measures_examined"] == 3
        assert result["measures_simplifiable"] == 2

    def test_each_measure_comes_back_with_its_own_verdict(self, pbip: Path) -> None:
        result = simplify_dax_measure(pbip_path=str(pbip), all_measures=True)

        by_name = {entry["measure"]: entry for entry in result["measures"]}
        assert by_name["Total Amount"]["changed"] is False
        assert by_name["Safe Amount"]["changed"] is True
        assert by_name["Margin"]["simplified_expression"] == "COALESCE([Amount], 0) * 2"


class TestTheArguments:
    def test_both_an_expression_and_a_project_is_refused(self, pbip: Path) -> None:
        result = simplify_dax_measure(expression="[A]", pbip_path=str(pbip), measure="X")

        assert result["success"] is False
        assert "not both" in (result["error_message"] or "")

    def test_nothing_at_all_is_refused(self) -> None:
        result = simplify_dax_measure()

        assert result["success"] is False
        assert "give an expression" in (result["error_message"] or "")

    def test_a_project_without_a_measure_is_refused(self, pbip: Path) -> None:
        result = simplify_dax_measure(pbip_path=str(pbip))

        assert result["success"] is False
        assert "all_measures" in (result["error_message"] or "")


class TestItWritesNothing:
    def test_the_project_is_untouched(self, pbip: Path) -> None:
        before = (pbip / "demo.Dataset" / "definition.pbism").read_bytes()

        simplify_dax_measure(pbip_path=str(pbip), all_measures=True)

        after = (pbip / "demo.Dataset" / "definition.pbism").read_bytes()
        assert before == after
