"""Tests for the validation fixes that were still open after v1.19.0.

- The accessibility auditor read the alt text from the wrong place in
  the PBIR tree, so the alt text written by Power BI Desktop was
  invisible and every visual was reported as having none.
- The visual regression comparison reported "unchanged" for images it
  could not decode, which hid a real regression.
- A page name was used as a directory without being checked, so an
  empty name wrote a page outside the expected layout and a name with
  a separator wrote outside the report directory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.engines.base import ConnectionHandle
from powerbi_orchestrator_mcp.engines.errors import EngineValidationError
from powerbi_orchestrator_mcp.engines.report_python import PythonReportEngine
from powerbi_orchestrator_mcp.validation.accessibility.wcag_auditor import WcagAuditor


def _pbip(tmp_path: Path) -> Path:
    pbip = tmp_path / "sample.pbip"
    pbip.mkdir()
    (pbip / "sample.pbip").write_text("{}", encoding="utf-8")
    report = pbip / "sample.Report"
    report.mkdir()
    (report / "report.json").write_text("{}", encoding="utf-8")
    return pbip


class TestAltTextExtraction:
    def test_the_pbir_shape_is_read(self) -> None:
        """PBIR nests the value under ``properties``."""
        container: dict[str, object] = {
            "visual": {
                "objects": {
                    "altText": [{"properties": {"expr": {"Literal": {"Value": "Sales by region"}}}}]
                }
            }
        }
        assert WcagAuditor._extract_alt_text(container) == "Sales by region"

    def test_the_flat_shape_is_read_too(self) -> None:
        container = {"visual": {"objects": {"altText": [{"expr": {"Literal": {"Value": "Flat"}}}]}}}
        assert WcagAuditor._extract_alt_text(container) == "Flat"

    def test_no_alt_text_returns_none(self) -> None:
        container: dict[str, object] = {"visual": {"objects": {}}}
        assert WcagAuditor._extract_alt_text(container) is None

    def test_a_visual_with_alt_text_is_not_reported_as_missing(self, tmp_path: Path) -> None:
        """The end-to-end path: alt text in a real page file."""
        pbip = _pbip(tmp_path)
        page_dir = pbip / "sample.Report" / "pages" / "Overview"
        page_dir.mkdir(parents=True)
        (page_dir / "page.json").write_text(
            json.dumps(
                {
                    "width": 1280,
                    "height": 720,
                    "visualContainers": [
                        {
                            "id": "v1",
                            "visual": {
                                "$type": "card",
                                "objects": {
                                    "altText": [
                                        {
                                            "properties": {
                                                "expr": {"Literal": {"Value": "Sales by region"}}
                                            }
                                        }
                                    ]
                                },
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = WcagAuditor().audit_pbip(pbip)

        assert not [f for f in result.findings if f.rule_id == "WCAG_1_1_1_NON_TEXT_CONTENT"]


class TestPageNameValidation:
    @pytest.mark.parametrize(
        "name",
        ["", "   ", "..", ".", "../evil", "a/b", "a\\b", ".hidden"],
    )
    def test_a_bad_page_name_is_refused(self, tmp_path: Path, name: str) -> None:
        pbip = _pbip(tmp_path)
        conn = ConnectionHandle(
            engine="python_report",
            target_type="pbip_folder",
            target_ref=str(pbip),
            session_token="x",
        )

        with pytest.raises(EngineValidationError):
            asyncio_run(PythonReportEngine().add_page(conn, name))

    def test_a_good_page_name_writes_the_page(self, tmp_path: Path) -> None:
        pbip = _pbip(tmp_path)
        conn = ConnectionHandle(
            engine="python_report",
            target_type="pbip_folder",
            target_ref=str(pbip),
            session_token="x",
        )

        result = asyncio_run(PythonReportEngine().add_page(conn, "Overview"))

        assert result.success is True
        page = pbip / "sample.Report" / "pages" / "Overview" / "page.json"
        assert page.exists()
        assert json.loads(page.read_text())["name"] == "Overview"


def asyncio_run(coro):  # type: ignore[no-untyped-def]
    import asyncio

    return asyncio.run(coro)
