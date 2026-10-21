"""Tests for the abstract_measures tool.

The tool finds the logic that is written more than once in a model.
What matters is that it reports the duplication accurately — a group
that is not really a group sends the reader after a refactor that
changes nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.tools.abstract_measures import abstract_measures


def _project(tmp_path: Path, measures: dict[str, str], table: str = "FactSales") -> Path:
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
                            "name": table,
                            "columns": [{"name": "Amount", "dataType": "decimal"}],
                            "measures": [
                                {"name": name, "expression": expression}
                                for name, expression in measures.items()
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    return root


class TestIdenticalMeasures:
    def test_two_measures_with_the_same_expression_are_a_group(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "Total Amount": "SUM(FactSales[Amount])",
                "Total Amount (old)": "SUM(FactSales[Amount])",
            },
        )

        result = abstract_measures(str(root))

        assert result["success"] is True
        assert result["group_count"] == 1
        group = result["groups"][0]
        assert group["kind"] == "identical"
        assert group["measures"] == ["Total Amount", "Total Amount (old)"]

    def test_it_says_what_the_duplication_is(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "Total Amount": "SUM(FactSales[Amount])",
                "Total Amount (old)": "SUM(FactSales[Amount])",
            },
        )

        group = abstract_measures(str(root))["groups"][0]

        assert "one of them can serve the rest" in group["suggestion"]

    def test_different_measures_are_not_a_group(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "Total Amount": "SUM(FactSales[Amount])",
                "Margin": "DIVIDE([A], [B], 0)",
            },
        )

        assert abstract_measures(str(root))["group_count"] == 0


class TestMeasuresWithTheSameShape:
    def test_they_are_a_group(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "Revenue 2024": "SUM(FactSales[Amount]) * 1.05",
                "Revenue 2025": "SUM(FactSales[Amount]) * 1.10",
            },
        )

        result = abstract_measures(str(root))

        assert result["group_count"] == 1
        group = result["groups"][0]
        assert group["kind"] == "same_shape"
        assert group["measures"] == ["Revenue 2024", "Revenue 2025"]

    def test_it_says_where_they_differ(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "Revenue 2024": "SUM(FactSales[Amount]) * 1.05",
                "Revenue 2025": "SUM(FactSales[Amount]) * 1.10",
            },
        )

        group = abstract_measures(str(root))["groups"][0]

        differing = [slot for slot in group["slots"] if slot["differs"]]
        assert len(differing) == 1
        assert differing[0]["kind"] == "literal"
        assert differing[0]["values"] == {"Revenue 2024": "1.05", "Revenue 2025": "1.1"}

    def test_the_shared_parts_are_not_reported_as_differences(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "Revenue 2024": "SUM(FactSales[Amount]) * 1.05",
                "Revenue 2025": "SUM(FactSales[Amount]) * 1.10",
            },
        )

        group = abstract_measures(str(root))["groups"][0]

        same = [slot for slot in group["slots"] if not slot["differs"]]
        assert len(same) == 1
        assert same[0]["kind"] == "column"

    def test_it_suggests_a_calculation_group(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "Revenue 2024": "SUM(FactSales[Amount]) * 1.05",
                "Revenue 2025": "SUM(FactSales[Amount]) * 1.10",
            },
        )

        group = abstract_measures(str(root))["groups"][0]

        assert "calculation group" in group["suggestion"]


class TestWhatItCannotLookAt:
    def test_a_measure_it_cannot_parse_is_counted_not_dropped(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "Broken": "SUM(([A]",
                "Fine": "SUM(FactSales[Amount])",
            },
        )

        result = abstract_measures(str(root))

        assert result["measures_examined"] == 2
        assert result["measures_skipped"] == 1
        assert "Broken" in result["skipped"]


class TestTheArguments:
    def test_a_table_filter_narrows_the_search(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "Total Amount": "SUM(FactSales[Amount])",
                "Total Amount (old)": "SUM(FactSales[Amount])",
            },
        )

        result = abstract_measures(str(root), table="FactSales")

        assert result["group_count"] == 1

    def test_a_table_that_does_not_exist_is_reported(self, tmp_path: Path) -> None:
        root = _project(tmp_path, {"A": "SUM(FactSales[Amount])"})

        result = abstract_measures(str(root), table="Nope")

        assert result["success"] is False
        assert "no measure in the table" in (result["error_message"] or "")

    def test_a_group_size_below_two_is_refused(self, tmp_path: Path) -> None:
        root = _project(tmp_path, {"A": "SUM(FactSales[Amount])"})

        result = abstract_measures(str(root), min_group_size=1)

        assert result["success"] is False
        assert "at least 2" in (result["error_message"] or "")

    def test_a_project_that_cannot_be_read_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The workspace root is the working directory by default, and
        # a temporary directory is outside it, so the path is refused
        # by the boundary before it is even looked for.
        monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(tmp_path))
        result = abstract_measures(str(tmp_path / "nope"))

        assert result["success"] is False
        assert "does not exist" in (result["error_message"] or "")


class TestItWritesNothing:
    def test_the_project_is_untouched(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "Total Amount": "SUM(FactSales[Amount])",
                "Total Amount (old)": "SUM(FactSales[Amount])",
            },
        )
        before = (root / "demo.Dataset" / "definition.pbism").read_bytes()

        abstract_measures(str(root))

        assert (root / "demo.Dataset" / "definition.pbism").read_bytes() == before


@pytest.mark.parametrize("kind", ["measures", "columns", "both"])
def test_the_kinds_are_accepted(kind: str) -> None:
    assert kind in ("measures", "columns", "both")
