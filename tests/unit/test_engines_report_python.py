"""Tests for engines.report_python — PythonReportEngine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.engines.base import (
    ConnectionHandle,
    PageLayout,
    ValidationResult,
    VisualSpec,
)
from powerbi_orchestrator_mcp.engines.errors import EngineValidationError
from powerbi_orchestrator_mcp.engines.report_python import (
    PythonReportEngine,
    _replace_in_obj,
)

# ---------------------------------------------------------------------------
# Fixture: minimal PBIP folder
# ---------------------------------------------------------------------------


@pytest.fixture()
def pbip(tmp_path: Path) -> Path:
    """Create a minimal PBIP folder structure for testing."""
    pbip_root = tmp_path / "sample.pbip"
    pbip_root.mkdir()

    # .pbip metadata file
    (pbip_root / "sample.pbip").write_text(
        json.dumps({"version": "1.0", "name": "sample"}),
        encoding="utf-8",
    )

    # Report folder
    report_dir = pbip_root / "sample.Report"
    report_dir.mkdir()

    (report_dir / "report.json").write_text(
        json.dumps({"name": "sample", "version": "1.0"}),
        encoding="utf-8",
    )

    # One initial page
    pages_dir = report_dir / "pages"
    page_dir = pages_dir / "Overview"
    page_dir.mkdir(parents=True)
    (page_dir / "page.json").write_text(
        json.dumps(
            {
                "$type": "page",
                "name": "Overview",
                "displayName": "Overview",
                "visualContainers": [
                    {
                        "$type": "visualContainer",
                        "id": "v1",
                        "visual": {
                            "$type": "card",
                            "query": {"queryRef": "Sales[Total Sales]"},
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    return pbip_root


@pytest.fixture()
def engine() -> PythonReportEngine:
    return PythonReportEngine()


# ---------------------------------------------------------------------------
# Properties + health
# ---------------------------------------------------------------------------


class TestHealth:
    async def test_health_check_always_available(self, engine: PythonReportEngine) -> None:
        status = await engine.health_check()
        assert status.available is True
        assert status.name == "python_report"

    def test_name(self, engine: PythonReportEngine) -> None:
        assert engine.name == "python_report"


# ---------------------------------------------------------------------------
# Connect
# ---------------------------------------------------------------------------


class TestConnect:
    async def test_connect_validates_pbip_layout(
        self, engine: PythonReportEngine, pbip: Path
    ) -> None:
        conn = await engine.connect(pbip)
        assert isinstance(conn, ConnectionHandle)
        assert conn.target_type == "pbip_folder"
        assert conn.target_ref == str(pbip)

    async def test_connect_raises_for_missing_pbip(
        self, engine: PythonReportEngine, tmp_path: Path
    ) -> None:
        with pytest.raises(EngineValidationError, match="no .Report directory"):
            await engine.connect(tmp_path / "nonexistent")

    async def test_connect_raises_for_missing_metadata(
        self, engine: PythonReportEngine, tmp_path: Path
    ) -> None:
        # Create only the Report dir but no .pbip metadata file.
        (tmp_path / "x.Report").mkdir()
        (tmp_path / "x.Report" / "report.json").write_text("{}")
        with pytest.raises(EngineValidationError, match="no .pbip metadata"):
            await engine.connect(tmp_path)

    async def test_disconnect_is_noop(
        self, engine: PythonReportEngine, pbip: Path
    ) -> None:
        conn = await engine.connect(pbip)
        await engine.disconnect(conn)  # does nothing


# ---------------------------------------------------------------------------
# Page CRUD
# ---------------------------------------------------------------------------


class TestAddPage:
    async def test_add_page_creates_files(
        self, engine: PythonReportEngine, pbip: Path
    ) -> None:
        conn = await engine.connect(pbip)
        result = await engine.add_page(conn, "Detail")
        assert result.success is True
        assert result.changed_files
        page_json = pbip / "sample.Report" / "pages" / "Detail" / "page.json"
        assert page_json.exists()
        data = json.loads(page_json.read_text())
        assert data["name"] == "Detail"

    async def test_add_page_duplicate_raises(
        self, engine: PythonReportEngine, pbip: Path
    ) -> None:
        conn = await engine.connect(pbip)
        # "Overview" already exists in the fixture.
        with pytest.raises(EngineValidationError, match="already exists"):
            await engine.add_page(conn, "Overview")

    async def test_add_page_with_layout(
        self, engine: PythonReportEngine, pbip: Path
    ) -> None:
        conn = await engine.connect(pbip)
        layout = PageLayout(width=1920, height=1080, mobile_first=False)
        await engine.add_page(conn, "Wide", layout=layout)
        page_json = pbip / "sample.Report" / "pages" / "Wide" / "page.json"
        data = json.loads(page_json.read_text())
        assert data["width"] == 1920
        assert data["height"] == 1080


class TestAddVisual:
    async def test_add_visual_appends_to_page(
        self, engine: PythonReportEngine, pbip: Path
    ) -> None:
        conn = await engine.connect(pbip)
        spec = VisualSpec(
            type="barChart",
            fields={"category": ["DimDate[Month]"], "values": ["[Total Sales]"]},
            position={"x": 0, "y": 0, "width": 400, "height": 300},
        )
        result = await engine.add_visual(conn, "Overview", spec)
        assert result.success is True

        page_json = pbip / "sample.Report" / "pages" / "Overview" / "page.json"
        data = json.loads(page_json.read_text())
        assert len(data["visualContainers"]) == 2
        assert data["visualContainers"][1]["visual"]["$type"] == "barChart"


class TestUpdateVisual:
    async def test_update_visual_mutates_field(
        self, engine: PythonReportEngine, pbip: Path
    ) -> None:
        conn = await engine.connect(pbip)
        result = await engine.update_visual(
            conn, "Overview", "v1", {"x": 100, "y": 200}
        )
        assert result.success is True
        page_json = pbip / "sample.Report" / "pages" / "Overview" / "page.json"
        data = json.loads(page_json.read_text())
        container = data["visualContainers"][0]
        assert container["x"] == 100
        assert container["y"] == 200

    async def test_update_visual_not_found_raises(
        self, engine: PythonReportEngine, pbip: Path
    ) -> None:
        conn = await engine.connect(pbip)
        with pytest.raises(EngineValidationError, match="not found"):
            await engine.update_visual(
                conn, "Overview", "v999", {"x": 0}
            )


# ---------------------------------------------------------------------------
# Propagate rename
# ---------------------------------------------------------------------------


class TestPropagateRename:
    async def test_propagate_renames_references_in_page(
        self, engine: PythonReportEngine, pbip: Path
    ) -> None:
        conn = await engine.connect(pbip)
        result = await engine.propagate_rename(
            conn,
            old_path="Total Sales",
            new_path="Revenue",
            scope="report_bindings",
        )
        assert result.success is True
        assert any("Overview" in c for c in result.changed_files)

        page_json = pbip / "sample.Report" / "pages" / "Overview" / "page.json"
        data = json.loads(page_json.read_text())
        # Original was "Sales[Total Sales]" — should now be "Sales[Revenue]".
        assert "Revenue" in json.dumps(data)
        assert "Total Sales" not in json.dumps(data)

    async def test_propagate_no_match_returns_empty(
        self, engine: PythonReportEngine, pbip: Path
    ) -> None:
        conn = await engine.connect(pbip)
        result = await engine.propagate_rename(
            conn, old_path="NonExistent", new_path="x", scope="report_bindings"
        )
        assert result.success is True
        assert result.changed_files == []

    async def test_propagate_invalid_scope_raises(
        self, engine: PythonReportEngine, pbip: Path
    ) -> None:
        conn = await engine.connect(pbip)
        with pytest.raises(EngineValidationError, match="unknown scope"):
            await engine.propagate_rename(
                conn, "a", "b", scope="nonsense"
            )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidate:
    async def test_validate_good_pbip_returns_valid(
        self, engine: PythonReportEngine, pbip: Path
    ) -> None:
        conn = await engine.connect(pbip)
        result = await engine.validate_pbir(conn)
        assert isinstance(result, ValidationResult)
        assert result.valid is True
        assert result.findings == []

    async def test_validate_missing_report_json(
        self, engine: PythonReportEngine, pbip: Path
    ) -> None:
        (pbip / "sample.Report" / "report.json").unlink()
        conn = await engine.connect(pbip)
        result = await engine.validate_pbir(conn)
        assert result.valid is False
        assert any(f["rule_id"] == "report_json_missing" for f in result.findings)

    async def test_validate_invalid_json(
        self, engine: PythonReportEngine, pbip: Path
    ) -> None:
        (pbip / "sample.Report" / "report.json").write_text("not valid json")
        conn = await engine.connect(pbip)
        result = await engine.validate_pbir(conn)
        assert result.valid is False
        assert any(f["rule_id"] == "report_json_invalid" for f in result.findings)

    async def test_validate_no_pages_warns(
        self, engine: PythonReportEngine, pbip: Path
    ) -> None:
        # Delete the Overview page.
        import shutil

        shutil.rmtree(pbip / "sample.Report" / "pages" / "Overview")
        conn = await engine.connect(pbip)
        result = await engine.validate_pbir(conn)
        # Still "valid" because no error-level findings.
        assert result.valid is True
        assert any(f["rule_id"] == "no_pages" for f in result.findings)


# ---------------------------------------------------------------------------
# Helper: _replace_in_obj
# ---------------------------------------------------------------------------


class TestReplaceInObj:
    def test_replace_in_string(self) -> None:
        assert _replace_in_obj("a b c", "b", "X") == "a X c"

    def test_replace_in_dict(self) -> None:
        result = _replace_in_obj({"k": "old value"}, "old", "new")
        assert result == {"k": "new value"}

    def test_replace_in_list(self) -> None:
        result = _replace_in_obj(["old", "old", "new"], "old", "X")
        assert result == ["X", "X", "new"]

    def test_replace_in_nested(self) -> None:
        result = _replace_in_obj(
            {"a": [{"b": "old"}]}, "old", "new"
        )
        assert result == {"a": [{"b": "new"}]}

    def test_no_match_returns_equivalent(self) -> None:
        data = {"a": "b"}
        result = _replace_in_obj(data, "x", "y")
        assert result == data  # equality; identity is not preserved

    def test_non_string_passthrough(self) -> None:
        assert _replace_in_obj(42, "x", "y") == 42
        assert _replace_in_obj(None, "x", "y") is None
