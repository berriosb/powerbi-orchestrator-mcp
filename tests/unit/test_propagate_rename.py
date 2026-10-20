"""Tests for the report-side rename.

PBIR holds a column reference as a structured object, not as the text
``Table[Column]``::

    {"Column": {"Expression": {"SourceRef": {"Entity": "FactSales"}},
                 "Property": "TotalAmount"}}

The rename used to be a literal text replacement, so it found nothing
in a real report and the step reported success while not a single
binding had changed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from powerbi_orchestrator_mcp.engines.base import ConnectionHandle
from powerbi_orchestrator_mcp.engines.report_python import (
    PythonReportEngine,
    _rename_references,
    _split_path,
)


def _pbip(tmp_path: Path) -> Path:
    pbip = tmp_path / "sample.pbip"
    pbip.mkdir()
    (pbip / "sample.pbip").write_text("{}", encoding="utf-8")
    report = pbip / "sample.Report"
    report.mkdir()
    (report / "report.json").write_text("{}", encoding="utf-8")
    return pbip


def _container() -> dict[str, Any]:
    return {
        "id": "v1",
        "visual": {
            "$type": "card",
            "projections": [
                {
                    "field": {
                        "Column": {
                            "Expression": {"SourceRef": {"Entity": "FactSales"}},
                            "Property": "TotalAmount",
                        }
                    }
                }
            ],
        },
        "queryRef": "FactSales.TotalAmount",
    }


def _conn(pbip: Path) -> ConnectionHandle:
    return ConnectionHandle(
        engine="python_report",
        target_type="pbip_folder",
        target_ref=str(pbip),
        session_token="x",
    )


class TestSplitPath:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("FactSales[TotalAmount]", ("FactSales", "TotalAmount")),
            ("FactSales.TotalAmount", ("FactSales", "TotalAmount")),
            ("TotalAmount", ("", "TotalAmount")),
        ],
    )
    def test_the_reference_forms(self, path: str, expected: tuple[str, str]) -> None:
        assert _split_path(path) == expected


class TestRenameStructuredReferences:
    def test_a_structured_reference_is_renamed(self) -> None:
        tree = {
            "field": {
                "Column": {
                    "Expression": {"SourceRef": {"Entity": "FactSales"}},
                    "Property": "TotalAmount",
                }
            }
        }

        out, count = _rename_references(tree, "FactSales", "TotalAmount", "FactSales", "Revenue")

        assert count == 1
        ref = out["field"]["Column"]
        assert ref["Property"] == "Revenue"
        assert ref["Expression"]["SourceRef"]["Entity"] == "FactSales"

    def test_a_reference_to_another_column_is_untouched(self) -> None:
        tree = {
            "field": {
                "Column": {
                    "Expression": {"SourceRef": {"Entity": "FactSales"}},
                    "Property": "OtherAmount",
                }
            }
        }

        out, count = _rename_references(tree, "FactSales", "TotalAmount", "FactSales", "Revenue")

        assert count == 0
        assert out["field"]["Column"]["Property"] == "OtherAmount"

    def test_a_reference_to_another_table_is_untouched(self) -> None:
        tree = {
            "field": {
                "Column": {
                    "Expression": {"SourceRef": {"Entity": "DimDate"}},
                    "Property": "TotalAmount",
                }
            }
        }

        out, count = _rename_references(tree, "FactSales", "TotalAmount", "FactSales", "Revenue")

        assert count == 0
        assert out["field"]["Column"]["Expression"]["SourceRef"]["Entity"] == "DimDate"

    def test_the_original_tree_is_not_modified(self) -> None:
        """A caller compares the result with the original to see a change."""
        tree = {
            "field": {
                "Column": {
                    "Expression": {"SourceRef": {"Entity": "FactSales"}},
                    "Property": "TotalAmount",
                }
            }
        }

        _rename_references(tree, "FactSales", "TotalAmount", "FactSales", "Revenue")

        assert tree["field"]["Column"]["Property"] == "TotalAmount"


class TestPropagateRename:
    def test_a_real_page_is_rewritten(self, tmp_path: Path) -> None:
        pbip = _pbip(tmp_path)
        page_dir = pbip / "sample.Report" / "pages" / "Overview"
        page_dir.mkdir(parents=True)
        (page_dir / "page.json").write_text(
            json.dumps({"width": 1280, "height": 720, "visualContainers": [_container()]}),
            encoding="utf-8",
        )

        result = asyncio.run(
            PythonReportEngine().propagate_rename(
                _conn(pbip),
                "FactSales[TotalAmount]",
                "FactSales[Revenue]",
                "report_bindings",
            )
        )

        assert result.changed_files == [
            # The path is relative to the PBIP root; the separator is
            # the platform's.
            str(Path("sample.Report") / "pages" / "Overview" / "page.json")
        ]
        assert result.references_renamed == 1

        saved = json.loads((page_dir / "page.json").read_text())
        container = saved["visualContainers"][0]
        ref = container["visual"]["projections"][0]["field"]["Column"]
        assert ref["Property"] == "Revenue"
        assert ref["Expression"]["SourceRef"]["Entity"] == "FactSales"
        # The dotted queryRef is rewritten too.
        assert container["queryRef"] == "FactSales.Revenue"

    def test_a_report_that_does_not_reference_the_column_reports_nothing(
        self, tmp_path: Path
    ) -> None:
        """No reference and no file changed is a different outcome."""
        pbip = _pbip(tmp_path)
        page_dir = pbip / "sample.Report" / "pages" / "Overview"
        page_dir.mkdir(parents=True)
        container = _container()
        container["visual"]["projections"][0]["field"]["Column"]["Property"] = "SomethingElse"
        container["queryRef"] = "FactSales.SomethingElse"
        (page_dir / "page.json").write_text(
            json.dumps({"width": 1280, "height": 720, "visualContainers": [container]}),
            encoding="utf-8",
        )

        result = asyncio.run(
            PythonReportEngine().propagate_rename(
                _conn(pbip),
                "FactSales[TotalAmount]",
                "FactSales[Revenue]",
                "report_bindings",
            )
        )

        assert result.changed_files == []
        assert result.references_renamed == 0
