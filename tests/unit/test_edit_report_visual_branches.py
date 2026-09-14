"""Sprint 15 follow-up: cover the missing branches in edit_report_visual.

The existing tests in test_tools_v11.py cover the happy paths
(altText, position, type, fields, multiple changes). These cover the
remaining branches: no .Report dir, missing page, invalid page.json,
invalid *_json inputs, format change, is_hidden change, atomic write
failure, and "no changes provided" edge.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.tools.edit_report_visual import (
    edit_report_visual,
)


def _make_pbip(tmp_path: Path) -> Path:
    pbip = tmp_path / "test.pbip"
    pbip.mkdir()
    (pbip / "test.pbip").write_text("{}")
    report_dir = pbip / "test.Report"
    report_dir.mkdir()
    page_dir = report_dir / "pages" / "Overview"
    page_dir.mkdir(parents=True)
    (page_dir / "page.json").write_text(
        json.dumps(
            {
                "name": "Overview",
                "visualContainers": [
                    {
                        "id": "v1",
                        "x": 0,
                        "y": 0,
                        "width": 300,
                        "height": 200,
                        "altText": "Original alt",
                        "visual": {
                            "$type": "card",
                            "projections": {"Values": [{"queryRef": "[X]"}]},
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return pbip


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


class TestFailurePaths:
    def test_no_report_dir(self, tmp_path: Path) -> None:
        pbip = tmp_path / "empty.pbip"
        pbip.mkdir()
        result = edit_report_visual(
            pbip_path=str(pbip), page_name="X", visual_id="v1"
        )
        assert result["success"] is False
        assert "no .Report" in result["error_message"]

    def test_missing_page(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        result = edit_report_visual(
            pbip_path=str(pbip),
            page_name="Nonexistent",
            visual_id="v1",
            alt_text="x",
        )
        assert result["success"] is False
        assert "does not exist" in result["error_message"]

    def test_invalid_page_json(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        page_json = (
            pbip / "test.Report" / "pages" / "Overview" / "page.json"
        )
        page_json.write_text("{ not json", encoding="utf-8")
        result = edit_report_visual(
            pbip_path=str(pbip),
            page_name="Overview",
            visual_id="v1",
            alt_text="x",
        )
        assert result["success"] is False
        assert "page.json is not valid JSON" in result["error_message"]

    def test_invalid_format_json(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        result = edit_report_visual(
            pbip_path=str(pbip),
            page_name="Overview",
            visual_id="v1",
            format_json="not-valid",
        )
        assert result["success"] is False
        assert "format_json" in result["error_message"]

    def test_invalid_position_json(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        result = edit_report_visual(
            pbip_path=str(pbip),
            page_name="Overview",
            visual_id="v1",
            position_json="not-valid",
        )
        assert result["success"] is False
        assert "position_json" in result["error_message"]


# ---------------------------------------------------------------------------
# Change branches (less common)
# ---------------------------------------------------------------------------


class TestChangeBranches:
    def test_format_change_writes_objects(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        result = edit_report_visual(
            pbip_path=str(pbip),
            page_name="Overview",
            visual_id="v1",
            format_json='[{"type": "color", "fill": {"solid": {"color": "#000"}}}]',
        )
        assert result["success"] is True
        assert "format" in result["changes_applied"]
        data = json.loads(
            (
                    pbip
                    / "test.Report"
                    / "pages"
                    / "Overview"
                    / "page.json"
                ).read_text(encoding="utf-8")
        )
        assert "objects" in data["visualContainers"][0]["visual"]

    def test_is_hidden_true(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        result = edit_report_visual(
            pbip_path=str(pbip),
            page_name="Overview",
            visual_id="v1",
            is_hidden=True,
        )
        assert result["success"] is True
        assert "isHidden" in result["changes_applied"]
        data = json.loads(
            (
                pbip
                / "test.Report"
                / "pages"
                / "Overview"
                / "page.json"
            ).read_text(encoding="utf-8")
        )
        assert data["visualContainers"][0]["isHidden"] is True

    def test_is_hidden_false(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        result = edit_report_visual(
            pbip_path=str(pbip),
            page_name="Overview",
            visual_id="v1",
            is_hidden=False,
        )
        assert result["success"] is True
        data = json.loads(
            (
                pbip
                / "test.Report"
                / "pages"
                / "Overview"
                / "page.json"
            ).read_text(encoding="utf-8")
        )
        assert data["visualContainers"][0]["isHidden"] is False

    def test_type_change_sets_visual_subtype(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        result = edit_report_visual(
            pbip_path=str(pbip),
            page_name="Overview",
            visual_id="v1",
            type="barChart",
        )
        assert result["success"] is True
        assert "type" in result["changes_applied"]
        data = json.loads(
            (
                pbip
                / "test.Report"
                / "pages"
                / "Overview"
                / "page.json"
            ).read_text(encoding="utf-8")
        )
        assert (
            data["visualContainers"][0]["visual"]["$type"] == "barChart"
        )


# ---------------------------------------------------------------------------
# Atomic write failure (mock os.replace to raise)
# ---------------------------------------------------------------------------


class TestAtomicWriteFailure:
    def test_replace_failure_cleaned_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os as _os

        pbip = _make_pbip(tmp_path)
        # Force os.replace to raise.
        def raise_replace(*_a: object, **_kw: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(_os, "replace", raise_replace)
        result = edit_report_visual(
            pbip_path=str(pbip),
            page_name="Overview",
            visual_id="v1",
            alt_text="new alt",
        )
        assert result["success"] is False
        assert "atomic write failed" in result["error_message"]
        assert result["changes_applied"] == ["altText"]
