"""Tests for the Sprint 10 first slice — optimize_report_performance."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.tools.optimize_report_performance import (
    optimize_report_performance,
)


@pytest.fixture()
def pbip_with_pages(tmp_path: Path) -> Path:
    """Minimal PBIP with a .Report/pages/Overview/page.json."""
    pbip = tmp_path / "demo.pbip"
    pbip.mkdir()
    (pbip / "demo.pbip").write_text("{}")
    (pbip / "demo.Dataset").mkdir()
    report_dir = pbip / "demo.Report"
    report_dir.mkdir()
    page_dir = report_dir / "pages" / "Overview"
    page_dir.mkdir(parents=True)
    return pbip


def _write_page(pbip: Path, page_name: str, visual_containers: list[dict]) -> None:
    page_dir = pbip / "demo.Report" / "pages" / page_name
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "page.json").write_text(
        json.dumps(
            {
                "width": 1280,
                "height": 720,
                "visualContainers": visual_containers,
            }
        )
    )


def _visual(vid: str, vtype: str, **extra: object) -> dict:
    v: dict = {"id": vid, "visual": {"$type": vtype}}
    v.update(extra)
    return v


class TestOptimizeReportPerformance:
    def test_missing_pbip_returns_warning(self, tmp_path: Path) -> None:
        result = optimize_report_performance(pbip_path=str(tmp_path / "missing"))
        assert result.performance_score == 0.0
        assert any("does not exist" in w for w in result.warnings)

    def test_pbip_without_report_dir_returns_warning(
        self, tmp_path: Path
    ) -> None:
        pbip = tmp_path / "demo.pbip"
        pbip.mkdir()
        (pbip / "demo.pbip").write_text("{}")
        (pbip / "demo.Dataset").mkdir()
        result = optimize_report_performance(pbip_path=str(pbip))
        assert result.performance_score == 100.0
        assert any("no .Report" in w for w in result.warnings)

    def test_empty_pages_returns_clean_score(self, pbip_with_pages: Path) -> None:
        # No pages/ content.
        result = optimize_report_performance(pbip_path=str(pbip_with_pages))
        assert result.performance_score == 100.0
        assert result.pages_analyzed == 0
        assert result.meets_target is True

    def test_single_page_no_issues(self, pbip_with_pages: Path) -> None:
        _write_page(
            pbip_with_pages,
            "Overview",
            [_visual("v1", "card")],
        )
        result = optimize_report_performance(pbip_path=str(pbip_with_pages))
        assert result.pages_analyzed == 1
        assert result.visuals_analyzed == 1
        assert result.hotspots == []
        assert result.performance_score == 100.0

    def test_pie_chart_emits_hotspot(self, pbip_with_pages: Path) -> None:
        _write_page(
            pbip_with_pages,
            "Overview",
            [_visual("v1", "card"), _visual("v2", "pieChart")],
        )
        result = optimize_report_performance(pbip_path=str(pbip_with_pages))
        assert any(
            h.visual_id == "v2" and "pie" in h.reasons[0].lower()
            for h in result.hotspots
        )
        # Score should be reduced by the hotspot.
        assert result.performance_score < 100.0

    def test_density_over_five_creates_hotspot(
        self, pbip_with_pages: Path
    ) -> None:
        visuals = [_visual(f"v{i}", "card") for i in range(8)]
        _write_page(pbip_with_pages, "Overview", visuals)
        result = optimize_report_performance(pbip_path=str(pbip_with_pages))
        assert any(
            "density" in h.reasons[0].lower() for h in result.hotspots
        )

    def test_custom_visual_emits_hotspot(
        self, pbip_with_pages: Path
    ) -> None:
        _write_page(
            pbip_with_pages,
            "Overview",
            [_visual("v1", "customAwesomeVisual")],
        )
        result = optimize_report_performance(pbip_path=str(pbip_with_pages))
        assert any(
            "custom visual" in " ".join(h.reasons).lower()
            for h in result.hotspots
        )

    def test_conditional_formatting_emits_hotspot(
        self, pbip_with_pages: Path
    ) -> None:
        _write_page(
            pbip_with_pages,
            "Overview",
            [
                _visual(
                    "v1",
                    "barChart",
                    conditionalFormatting=[{"x": 1}, {"x": 2}],
                )
            ],
        )
        result = optimize_report_performance(pbip_path=str(pbip_with_pages))
        assert any(
            "conditional-formatting" in " ".join(h.reasons).lower()
            for h in result.hotspots
        )

    def test_meets_target_flag(self, pbip_with_pages: Path) -> None:
        # One page with many visuals → exceeds target.
        visuals = [_visual(f"v{i}", "pieChart") for i in range(15)]
        _write_page(pbip_with_pages, "Overview", visuals)
        result = optimize_report_performance(
            pbip_path=str(pbip_with_pages), target_load_ms=100
        )
        assert result.meets_target is False

    def test_multiple_pages_aggregated(
        self, pbip_with_pages: Path
    ) -> None:
        _write_page(pbip_with_pages, "Overview", [_visual("v1", "card")])
        _write_page(pbip_with_pages, "Detail", [_visual("v2", "lineChart")])
        result = optimize_report_performance(pbip_path=str(pbip_with_pages))
        assert result.pages_analyzed == 2
        assert result.visuals_analyzed == 2

    def test_wide_page_with_density_penalty(
        self, pbip_with_pages: Path
    ) -> None:
        visuals = [_visual(f"v{i}", "card") for i in range(7)]
        page_dir = pbip_with_pages / "demo.Report" / "pages" / "Wide"
        page_dir.mkdir(parents=True)
        (page_dir / "page.json").write_text(
            json.dumps(
                {
                    "width": 1920,
                    "height": 1080,
                    "visualContainers": visuals,
                }
            )
        )
        result = optimize_report_performance(pbip_path=str(pbip_with_pages))
        assert any(
            "wide page" in " ".join(h.reasons).lower() for h in result.hotspots
        )

    def test_invalid_json_page_emits_warning(
        self, pbip_with_pages: Path
    ) -> None:
        page_dir = pbip_with_pages / "demo.Report" / "pages" / "Broken"
        page_dir.mkdir(parents=True)
        (page_dir / "page.json").write_text("{not valid json")
        result = optimize_report_performance(pbip_path=str(pbip_with_pages))
        # Non-zero load because the failed page counts its baseline (500ms).
        assert any(
            "failed to parse" in " ".join(h.reasons).lower()
            for h in result.hotspots
        )

    def test_hotspot_cost_classification(self) -> None:
        # Pure unit test of _classify_cost: imported indirectly via tool.
        from powerbi_orchestrator_mcp.tools.optimize_report_performance import (
            _classify_cost,
        )

        assert _classify_cost(5) == "low"
        assert _classify_cost(30) == "medium"
        assert _classify_cost(80) == "high"
        assert _classify_cost(1000) == "high"

    def test_score_bounded_zero_to_hundred(
        self, pbip_with_pages: Path
    ) -> None:
        # Heaviest possible: 30 pie visuals.
        visuals = [_visual(f"v{i}", "pieChart") for i in range(30)]
        _write_page(pbip_with_pages, "Overview", visuals)
        result = optimize_report_performance(pbip_path=str(pbip_with_pages))
        assert 0.0 <= result.performance_score <= 100.0
