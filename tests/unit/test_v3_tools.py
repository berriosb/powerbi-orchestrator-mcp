"""Tests for Sprint 10 tools — optimize_report_performance + audit_report_ux_and_storytelling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.tools.audit_report_ux_and_storytelling import (
    audit_report_ux_and_storytelling,
)
from powerbi_orchestrator_mcp.tools.optimize_report_performance import (
    optimize_report_performance,
)
from powerbi_orchestrator_mcp.tools.screenshot_report_pages import (
    screenshot_report_pages,
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


# ---------------------------------------------------------------------------
# audit_report_ux_and_storytelling
# ---------------------------------------------------------------------------


def _write_ux_page(pbip: Path, page_name: str, visuals: list[dict], **kw) -> None:
    """Write a page.json with width/height + visuals (x/y required for layout)."""
    page_dir = pbip / "demo.Report" / "pages" / page_name
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "page.json").write_text(
        json.dumps(
            {
                "width": kw.get("width", 1280),
                "height": kw.get("height", 720),
                "visualContainers": visuals,
            }
        )
    )


def _ux_visual(vid: str, vtype: str, x: int = 0, y: int = 0, **extra) -> dict:
    v: dict = {
        "id": vid,
        "x": x,
        "y": y,
        "visual": {"$type": vtype, "projections": {}},
    }
    if extra:
        v.update(extra)
    return v


class TestAuditUxAndStorytelling:
    def test_missing_pbip_returns_warning(self, tmp_path: Path) -> None:
        result = audit_report_ux_and_storytelling(pbip_path=str(tmp_path / "x"))
        assert result.overall_score == 0.0
        assert any("does not exist" in w for w in result.warnings)

    def test_no_pages_clean_score(self, pbip_with_pages: Path) -> None:
        result = audit_report_ux_and_storytelling(pbip_path=str(pbip_with_pages))
        assert result.overall_score == 100.0
        assert result.pages_analyzed == 0

    def test_kpi_top_left_executive_no_hierarchy_finding(
        self, pbip_with_pages: Path
    ) -> None:
        _write_ux_page(
            pbip_with_pages,
            "Executive Summary",
            [_ux_visual("v1", "card", x=10, y=10), _ux_visual("v2", "lineChart", x=400, y=10)],
        )
        result = audit_report_ux_and_storytelling(pbip_path=str(pbip_with_pages))
        hierarchy_findings = [
            f for f in result.findings if f.category == "hierarchy"
        ]
        assert hierarchy_findings == []
        assert result.category_scores["hierarchy"] == 100.0

    def test_no_kpi_top_left_emits_hierarchy_finding(
        self, pbip_with_pages: Path
    ) -> None:
        _write_ux_page(
            pbip_with_pages,
            "Executive Summary",
            [_ux_visual("v1", "lineChart", x=400, y=10)],
        )
        result = audit_report_ux_and_storytelling(pbip_path=str(pbip_with_pages))
        assert any(
            f.category == "hierarchy" for f in result.findings
        )
        assert result.category_scores["hierarchy"] < 100.0

    def test_density_over_threshold(
        self, pbip_with_pages: Path
    ) -> None:
        visuals = [_ux_visual(f"v{i}", "card", x=i * 30, y=0) for i in range(12)]
        _write_ux_page(pbip_with_pages, "Executive Summary", visuals)
        result = audit_report_ux_and_storytelling(pbip_path=str(pbip_with_pages))
        assert any(
            f.category == "density" for f in result.findings
        )
        assert result.category_scores["density"] < 100.0

    def test_narrative_ordering_broken(
        self, pbip_with_pages: Path
    ) -> None:
        # Table above KPI = broken narrative.
        _write_ux_page(
            pbip_with_pages,
            "Executive Summary",
            [
                _ux_visual("v1", "tableEx", x=0, y=0),
                _ux_visual("v2", "card", x=400, y=400),
            ],
        )
        result = audit_report_ux_and_storytelling(pbip_path=str(pbip_with_pages))
        assert any(
            f.category == "narrative" for f in result.findings
        )
        assert result.category_scores["narrative"] < 100.0

    def test_mobile_finding_for_executive_with_many_visuals(
        self, pbip_with_pages: Path
    ) -> None:
        visuals = [_ux_visual(f"v{i}", "card", x=i * 20, y=0) for i in range(6)]
        _write_ux_page(pbip_with_pages, "Executive Summary", visuals)
        result = audit_report_ux_and_storytelling(pbip_path=str(pbip_with_pages))
        assert any(f.category == "mobile" for f in result.findings)
        auto_fixable = [f for f in result.findings if f.auto_fixable]
        assert any(f.category == "mobile" for f in auto_fixable)

    def test_pie_with_too_many_slices(
        self, pbip_with_pages: Path
    ) -> None:
        # 9 projections on a pie chart.
        page_dir = pbip_with_pages / "demo.Report" / "pages" / "Exec"
        page_dir.mkdir(parents=True)
        (page_dir / "page.json").write_text(
            json.dumps(
                {
                    "width": 1280,
                    "height": 720,
                    "visualContainers": [
                        {
                            "id": "v1",
                            "x": 0,
                            "y": 0,
                            "visual": {
                                "$type": "pieChart",
                                "projections": {
                                    "Category": [f"cat{i}" for i in range(9)],
                                },
                            },
                        }
                    ],
                }
            )
        )
        result = audit_report_ux_and_storytelling(pbip_path=str(pbip_with_pages))
        assert any(
            f.category == "pie_size" and "pie" in f.message.lower()
            for f in result.findings
        )

    def test_score_reproducible(self, pbip_with_pages: Path) -> None:
        visuals = [_ux_visual(f"v{i}", "lineChart", x=i * 30, y=0) for i in range(5)]
        _write_ux_page(pbip_with_pages, "Executive Summary", visuals)
        a = audit_report_ux_and_storytelling(pbip_path=str(pbip_with_pages))
        b = audit_report_ux_and_storytelling(pbip_path=str(pbip_with_pages))
        assert a.overall_score == b.overall_score
        assert a.findings == b.findings

    def test_meets_target_threshold(self, pbip_with_pages: Path) -> None:
        _write_ux_page(
            pbip_with_pages,
            "Executive Summary",
            [_ux_visual("v1", "card", x=10, y=10)],
        )
        result = audit_report_ux_and_storytelling(pbip_path=str(pbip_with_pages))
        # Single KPI in top-left → all categories score 100 → meets_target True.
        assert result.meets_target is True
        assert result.overall_score == 100.0

    def test_audience_inferred_from_page_name(
        self, pbip_with_pages: Path
    ) -> None:
        _write_ux_page(
            pbip_with_pages,
            "Drill-Down Analysis",
            [_ux_visual("v1", "lineChart", x=0, y=0)],
        )
        result = audit_report_ux_and_storytelling(pbip_path=str(pbip_with_pages))
        assert result.audience_inferred == "analyst"

    def test_explicit_audience_overrides_inference(
        self, pbip_with_pages: Path
    ) -> None:
        _write_ux_page(
            pbip_with_pages,
            "Anything",
            [_ux_visual("v1", "lineChart", x=400, y=10)],
        )
        result = audit_report_ux_and_storytelling(
            pbip_path=str(pbip_with_pages),
            audience_assumed="analyst",
        )
        # Analyst audience → hierarchy check is lenient (returns 90).
        assert result.category_scores["hierarchy"] == 90.0

    def test_filter_page_only_audits_that_page(
        self, pbip_with_pages: Path
    ) -> None:
        _write_ux_page(
            pbip_with_pages,
            "Overview",
            [_ux_visual("v1", "card", x=10, y=10)],
        )
        _write_ux_page(
            pbip_with_pages,
            "Detail",
            [_ux_visual("v2", "lineChart", x=400, y=10)],
        )
        result = audit_report_ux_and_storytelling(
            pbip_path=str(pbip_with_pages),
            page_name="Overview",
        )
        assert result.pages_analyzed == 1
        assert all(f.page_name == "Overview" for f in result.findings)

    def test_narrative_score_subscore(self, pbip_with_pages: Path) -> None:
        # KPI first → narrative score high.
        _write_ux_page(
            pbip_with_pages,
            "Exec",
            [
                _ux_visual("v1", "card", x=0, y=0),
                _ux_visual("v2", "tableEx", x=0, y=400),
            ],
        )
        result = audit_report_ux_and_storytelling(pbip_path=str(pbip_with_pages))
        assert result.narrative_score == 100.0

    def test_strict_strictness_lowers_density_threshold(
        self, pbip_with_pages: Path
    ) -> None:
        visuals = [_ux_visual(f"v{i}", "card", x=i * 30, y=0) for i in range(6)]
        _write_ux_page(pbip_with_pages, "Exec", visuals)
        a = audit_report_ux_and_storytelling(
            pbip_path=str(pbip_with_pages), strictness="strict"
        )
        b = audit_report_ux_and_storytelling(
            pbip_path=str(pbip_with_pages), strictness="lenient"
        )
        # Strict → density finding present; lenient → below the threshold.
        a_density = sum(1 for f in a.findings if f.category == "density")
        b_density = sum(1 for f in b.findings if f.category == "density")
        assert a_density >= b_density


# ---------------------------------------------------------------------------
# screenshot_report_pages
# ---------------------------------------------------------------------------


class TestScreenshotReportPages:
    def test_missing_pbip_returns_warning(self, tmp_path: Path) -> None:
        result = screenshot_report_pages(pbip_path=str(tmp_path / "missing"))
        assert any("does not exist" in w for w in result.warnings)

    def test_no_pages_returns_warning(self, pbip_with_pages: Path) -> None:
        # No page.json files → empty bundle list.
        result = screenshot_report_pages(pbip_path=str(pbip_with_pages))
        assert result.pages_attempted == 0
        assert any("no pages" in w.lower() for w in result.warnings)

    def test_single_page_writes_svg_and_manifest(
        self, pbip_with_pages: Path, tmp_path: Path
    ) -> None:
        _write_ux_page(
            pbip_with_pages,
            "Overview",
            [_ux_visual("v1", "card", x=0, y=0)],
        )
        out = tmp_path / "out"
        result = screenshot_report_pages(
            pbip_path=str(pbip_with_pages),
            output_dir=str(out),
        )
        assert result.pages_succeeded == 1
        assert (out / "Overview.svg").exists()
        assert (out / "Overview.manifest.json").exists()
        assert result.rendering_warnings  # at least the PNG fallback warning

    def test_svg_contains_visual_label(self, pbip_with_pages: Path, tmp_path: Path) -> None:
        _write_ux_page(
            pbip_with_pages,
            "Overview",
            [_ux_visual("v1", "card", x=10, y=10)],
        )
        out = tmp_path / "out"
        screenshot_report_pages(
            pbip_path=str(pbip_with_pages),
            output_dir=str(out),
        )
        svg = (out / "Overview.svg").read_text(encoding="utf-8")
        assert "v1" in svg
        assert "card" in svg

    def test_filter_pages_only_captures_listed(
        self, pbip_with_pages: Path, tmp_path: Path
    ) -> None:
        _write_ux_page(pbip_with_pages, "Overview", [_ux_visual("v1", "card", x=0, y=0)])
        _write_ux_page(pbip_with_pages, "Detail", [_ux_visual("v2", "lineChart", x=0, y=0)])
        out = tmp_path / "out"
        result = screenshot_report_pages(
            pbip_path=str(pbip_with_pages),
            pages=["Overview"],
            output_dir=str(out),
        )
        assert result.pages_attempted == 1
        assert result.pages_succeeded == 1

    def test_resolution_preset_changes_viewport(
        self, pbip_with_pages: Path, tmp_path: Path
    ) -> None:
        _write_ux_page(pbip_with_pages, "Overview", [_ux_visual("v1", "card", x=0, y=0)])
        out_mobile = tmp_path / "out_m"
        out_desktop = tmp_path / "out_d"
        screenshot_report_pages(
            pbip_path=str(pbip_with_pages),
            output_dir=str(out_mobile),
            resolution="mobile",
        )
        screenshot_report_pages(
            pbip_path=str(pbip_with_pages),
            output_dir=str(out_desktop),
            resolution="desktop",
        )
        m_data = json.loads((out_mobile / "Overview.manifest.json").read_text())
        d_data = json.loads((out_desktop / "Overview.manifest.json").read_text())
        assert m_data["viewport"]["width"] == 375
        assert d_data["viewport"]["width"] == 1280

    def test_baseline_comparison_threshold(
        self, pbip_with_pages: Path, tmp_path: Path
    ) -> None:
        # Baseline says 1 visual; current page has 3 → exceeds 5% threshold.
        _write_ux_page(
            pbip_with_pages,
            "Overview",
            [
                _ux_visual("v1", "card", x=0, y=0),
                _ux_visual("v2", "card", x=0, y=0),
                _ux_visual("v3", "card", x=0, y=0),
            ],
        )
        baseline = tmp_path / "baseline"
        baseline.mkdir()
        (baseline / "Overview.manifest.json").write_text(
            json.dumps({"visuals_count": 1})
        )
        out = tmp_path / "out"
        result = screenshot_report_pages(
            pbip_path=str(pbip_with_pages),
            output_dir=str(out),
            baseline_dir=str(baseline),
        )
        assert len(result.comparison) == 1
        diff = result.comparison[0]
        assert diff.exceeds_threshold is True
        assert diff.pixel_diff_pct is not None and diff.pixel_diff_pct > 5.0

    def test_manifest_is_deterministic(
        self, pbip_with_pages: Path, tmp_path: Path
    ) -> None:
        # Re-running with the same input produces byte-identical manifests.
        _write_ux_page(pbip_with_pages, "Overview", [_ux_visual("v1", "card", x=0, y=0)])
        out1 = tmp_path / "out1"
        out2 = tmp_path / "out2"
        screenshot_report_pages(pbip_path=str(pbip_with_pages), output_dir=str(out1))
        screenshot_report_pages(pbip_path=str(pbip_with_pages), output_dir=str(out2))
        m1 = (out1 / "Overview.manifest.json").read_bytes()
        m2 = (out2 / "Overview.manifest.json").read_bytes()
        assert m1 == m2

    def test_output_dir_auto_created(
        self, pbip_with_pages: Path, tmp_path: Path
    ) -> None:
        _write_ux_page(pbip_with_pages, "Overview", [_ux_visual("v1", "card", x=0, y=0)])
        deep = tmp_path / "deep" / "nested" / "out"
        result = screenshot_report_pages(
            pbip_path=str(pbip_with_pages),
            output_dir=str(deep),
        )
        assert deep.exists()
        assert result.pages_succeeded == 1
