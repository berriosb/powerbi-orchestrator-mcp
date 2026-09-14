"""Tests for tools.audit_report_ux_and_storytelling (Sprint 10 v2).

The tool was 33% covered; these tests cover all 5 heuristics
(hierarchy / density / narrative / mobile / cohesion) + the pie-slice
check + the top-level orchestration paths (missing pbip, empty pages,
filter by page_name, audience inference + override, strictness).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.tools.audit_report_ux_and_storytelling import (
    AuditReportUxAndStorytellingResult,
    UxFinding,
    _check_cohesion,
    _check_density,
    _check_hierarchy,
    _check_mobile,
    _check_narrative,
    _check_pie_categories,
    _infer_audience,
    _load_pages,
    _strictness_threshold,
    audit_report_ux_and_storytelling,
)


def _write_page(report_dir: Path, page_name: str, page_data: dict) -> None:
    page_dir = report_dir / "pages" / page_name
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "page.json").write_text(
        json.dumps(page_data), encoding="utf-8"
    )


def _make_pbip(tmp_path: Path) -> Path:
    pbip = tmp_path / "report.pbip"
    pbip.mkdir()
    (pbip / "report.Report").mkdir()
    return pbip


# ---------------------------------------------------------------------------
# Helper-level tests
# ---------------------------------------------------------------------------


class TestInferAudience:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Executive Summary", "executive"),
            ("KPI Cockpit", "executive"),
            ("Overview", "executive"),
            ("Headline", "executive"),
            ("Sales Analysis", "analyst"),
            ("Deep Drill", "analyst"),
            ("Breakdown", "analyst"),
            ("Ops Monitor", "operational"),
            ("Alert Triage", "operational"),
            ("RandomName", "executive"),  # default
        ],
    )
    def test_patterns(self, name: str, expected: str) -> None:
        assert _infer_audience(name) == expected


class TestStrictnessThreshold:
    @pytest.mark.parametrize(
        ("strictness", "base", "expected"),
        [
            ("lenient", 8, 12),
            ("standard", 8, 8),
            ("strict", 8, 4),
            ("strict", 1, 1),  # max(1, base//2) clamp
            ("unknown-strictness", 10, 10),  # default to base
        ],
    )
    def test_thresholds(self, strictness: str, base: int, expected: int) -> None:
        assert _strictness_threshold(strictness, base) == expected


# ---------------------------------------------------------------------------
# Per-heuristic tests
# ---------------------------------------------------------------------------


def _ctx(visuals: list[dict], *, audience: str = "executive") -> object:
    from powerbi_orchestrator_mcp.tools.audit_report_ux_and_storytelling import (
        PageContext,
    )

    return PageContext(
        page_name="Test",
        audience=audience,
        width=1280,
        height=720,
        visuals=visuals,
        strictness="standard",
    )


class TestHierarchy:
    def test_empty_visuals_score_100(self) -> None:
        findings: list[UxFinding] = []
        assert _check_hierarchy(_ctx([]), findings) == 100.0
        assert findings == []

    def test_non_executive_returns_90_with_no_finding(self) -> None:
        findings: list[UxFinding] = []
        # analyst: hierarchy matters less; no finding
        assert _check_hierarchy(_ctx([{"visual": {"$type": "card"}}], audience="analyst"), findings) == 90.0
        assert findings == []

    def test_kpi_top_left_scores_100(self) -> None:
        findings: list[UxFinding] = []
        visuals = [{"visual": {"$type": "card"}, "x": 100, "y": 80}]
        assert _check_hierarchy(_ctx(visuals), findings) == 100.0
        assert findings == []

    def test_no_kpi_top_left_emits_warning(self) -> None:
        findings: list[UxFinding] = []
        visuals = [
            {"visual": {"$type": "barChart"}, "x": 600, "y": 400},
        ]
        score = _check_hierarchy(_ctx(visuals), findings)
        assert score == 50.0
        assert len(findings) == 1
        assert findings[0].category == "hierarchy"
        assert findings[0].severity == "warning"
        assert findings[0].auto_fixable is False


class TestDensity:
    def test_within_limit_scores_100(self) -> None:
        findings: list[UxFinding] = []
        visuals = [{"visual": {"$type": "card"}} for _ in range(6)]
        assert _check_density(_ctx(visuals), findings) == 100.0
        assert findings == []

    def test_over_limit_emits_info(self) -> None:
        findings: list[UxFinding] = []
        # executive max = 8; 10 visuals → over by 2 → info
        visuals = [
            {"visual": {"$type": "card"}, "x": i * 100, "y": 0}
            for i in range(10)
        ]
        score = _check_density(_ctx(visuals), findings)
        assert score < 100.0
        assert len(findings) == 1
        assert findings[0].severity == "info"
        assert findings[0].category == "density"

    def test_over_by_more_than_3_emits_warning(self) -> None:
        findings: list[UxFinding] = []
        visuals = [
            {"visual": {"$type": "card"}, "x": i * 100, "y": 0}
            for i in range(13)
        ]
        _check_density(_ctx(visuals), findings)
        assert findings[0].severity == "warning"

    def test_clustered_over_threshold_emits_error(self) -> None:
        findings: list[UxFinding] = []
        # 12 visuals all at x=0 → spread=0 → error severity
        visuals = [
            {"visual": {"$type": "card"}, "x": 0, "y": 0} for _ in range(12)
        ]
        _check_density(_ctx(visuals), findings)
        assert findings[0].severity == "error"

    def test_analyst_allows_more(self) -> None:
        findings: list[UxFinding] = []
        visuals = [{"visual": {"$type": "card"}} for _ in range(10)]
        # analyst max_visuals=12; 10 visuals → no finding
        assert _check_density(_ctx(visuals, audience="analyst"), findings) == 100.0
        assert findings == []


class TestNarrative:
    def test_few_visuals_score_100(self) -> None:
        findings: list[UxFinding] = []
        visuals = [{"visual": {"$type": "card"}}]
        assert _check_narrative(_ctx(visuals), findings) == 100.0
        assert findings == []

    def test_kpi_above_table_scores_100(self) -> None:
        findings: list[UxFinding] = []
        visuals = [
            {"visual": {"$type": "card"}, "y": 100},
            {"visual": {"$type": "tableEx"}, "y": 500},
        ]
        assert _check_narrative(_ctx(visuals), findings) == 100.0
        assert findings == []

    def test_table_above_kpi_emits_warning(self) -> None:
        findings: list[UxFinding] = []
        visuals = [
            {"visual": {"$type": "tableEx"}, "y": 100},
            {"visual": {"$type": "card"}, "y": 500},
        ]
        score = _check_narrative(_ctx(visuals), findings)
        assert score == 60.0
        assert len(findings) == 1
        assert findings[0].category == "narrative"
        assert "broken narrative" in findings[0].message.lower()

    def test_no_mixed_kpi_and_table_returns_100(self) -> None:
        findings: list[UxFinding] = []
        visuals = [
            {"visual": {"$type": "barChart"}},
            {"visual": {"$type": "lineChart"}},
        ]
        assert _check_narrative(_ctx(visuals), findings) == 100.0


class TestMobile:
    def test_executive_under_limit_scores_100(self) -> None:
        findings: list[UxFinding] = []
        visuals = [{"visual": {"$type": "card"}} for _ in range(3)]
        assert _check_mobile(_ctx(visuals), findings) == 100.0
        assert findings == []

    def test_executive_over_mobile_threshold_emits_warning(self) -> None:
        findings: list[UxFinding] = []
        visuals = [{"visual": {"$type": "card"}} for _ in range(6)]
        score = _check_mobile(_ctx(visuals), findings)
        assert score == 70.0
        assert len(findings) == 1
        assert findings[0].category == "mobile"
        assert findings[0].auto_fixable is True

    def test_analyst_not_flagged_for_mobile(self) -> None:
        findings: list[UxFinding] = []
        visuals = [{"visual": {"$type": "card"}} for _ in range(10)]
        assert _check_mobile(_ctx(visuals, audience="analyst"), findings) == 100.0


class TestCohesion:
    def test_empty_visuals_score_100(self) -> None:
        findings: list[UxFinding] = []
        assert _check_cohesion(_ctx([]), "executive", findings) == 100.0
        assert findings == []

    def test_unknown_visual_emits_info_finding(self) -> None:
        findings: list[UxFinding] = []
        visuals = [{"visual": {"$type": "unknown"}}]
        score = _check_cohesion(_ctx(visuals), "executive", findings)
        assert score == 100.0
        assert any(f.category == "cohesion" for f in findings)
        assert findings[0].severity == "info"

    def test_too_many_styles_for_executive_emits_warning(self) -> None:
        findings: list[UxFinding] = []
        # 6 different styles > 5 → warning for executive
        visuals = [
            {"visual": {"$type": "card"}},
            {"visual": {"$type": "lineChart"}},
            {"visual": {"$type": "barChart"}},
            {"visual": {"$type": "pieChart"}},
            {"visual": {"$type": "scatterChart"}},
            {"visual": {"$type": "tableEx"}},
        ]
        score = _check_cohesion(_ctx(visuals), "executive", findings)
        assert score == 70.0
        assert any(f.severity == "warning" for f in findings)

    def test_analyst_allows_more_styles(self) -> None:
        findings: list[UxFinding] = []
        visuals = [
            {"visual": {"$type": "card"}},
            {"visual": {"$type": "lineChart"}},
            {"visual": {"$type": "barChart"}},
            {"visual": {"$type": "pieChart"}},
            {"visual": {"$type": "scatterChart"}},
            {"visual": {"$type": "tableEx"}},
        ]
        assert _check_cohesion(_ctx(visuals), "analyst", findings) == 100.0


class TestPieCategories:
    def test_no_pie_no_finding(self) -> None:
        findings: list[UxFinding] = []
        visuals = [{"visual": {"$type": "barChart"}}]
        _check_pie_categories(_ctx(visuals), findings)
        assert findings == []

    def test_pie_within_7_slices_no_finding(self) -> None:
        findings: list[UxFinding] = []
        visuals = [
            {
                "id": "p1",
                "visual": {
                    "$type": "pieChart",
                    "projections": {
                        "Category": ["A", "B", "C", "D", "E", "F"],
                        "Values": ["Sales"],
                    },
                },
            }
        ]
        _check_pie_categories(_ctx(visuals), findings)
        assert findings == []

    def test_pie_with_too_many_slices_emits_warning(self) -> None:
        findings: list[UxFinding] = []
        # 12 categories projected → category_count = 12 > 7
        visuals = [
            {
                "id": "p1",
                "visual": {
                    "$type": "pieChart",
                    "projections": {
                        "Category": ["A"] * 11,
                        "Values": ["Sales"],
                    },
                },
            }
        ]
        _check_pie_categories(_ctx(visuals), findings)
        assert len(findings) == 1
        assert findings[0].category == "pie_size"
        assert findings[0].auto_fixable is True

    def test_donut_also_audited(self) -> None:
        findings: list[UxFinding] = []
        visuals = [
            {
                "id": "d1",
                "visual": {
                    "$type": "donutChart",
                    "projections": {"Category": ["A"] * 10},
                },
            }
        ]
        _check_pie_categories(_ctx(visuals), findings)
        assert len(findings) == 1


# ---------------------------------------------------------------------------
# Page loading + main entry
# ---------------------------------------------------------------------------


class TestLoadPages:
    def test_no_pbip_dir_returns_empty(self, tmp_path: Path) -> None:
        empty_pbip = tmp_path / "nope.pbip"
        empty_pbip.mkdir()
        assert _load_pages(empty_pbip, None) == []

    def test_no_pages_dir_returns_empty(self, tmp_path: Path) -> None:
        pbip = tmp_path / "r.pbip"
        pbip.mkdir()
        (pbip / "r.Report").mkdir()
        assert _load_pages(pbip, None) == []

    def test_corrupt_page_skipped_silently(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        report_dir = pbip / "report.Report"
        (report_dir / "pages" / "Good").mkdir(parents=True)
        (report_dir / "pages" / "Good" / "page.json").write_text(
            json.dumps({"width": 1280, "height": 720, "visualContainers": []}),
            encoding="utf-8",
        )
        (report_dir / "pages" / "Bad").mkdir(parents=True)
        (report_dir / "pages" / "Bad" / "page.json").write_text(
            "{not json", encoding="utf-8"
        )
        pages = _load_pages(pbip, None)
        assert len(pages) == 1
        assert pages[0].page_name == "Good"

    def test_filter_by_page_name(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        for name in ("A", "B", "C"):
            _write_page(
                pbip / "report.Report",
                name,
                {"width": 1280, "height": 720, "visualContainers": []},
            )
        pages = _load_pages(pbip, "B")
        assert len(pages) == 1
        assert pages[0].page_name == "B"


class TestAuditReportUxAndStorytelling:
    def test_missing_pbip_warns(self, tmp_path: Path) -> None:
        result = audit_report_ux_and_storytelling(
            pbip_path=str(tmp_path / "missing")
        )
        assert result.overall_score == 0.0
        assert any("does not exist" in w for w in result.warnings)
        assert result.pages_analyzed == 0

    def test_no_pages_warns(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        result = audit_report_ux_and_storytelling(pbip_path=str(pbip))
        assert result.overall_score == 100.0
        assert any("no page.json" in w for w in result.warnings)

    def test_clean_page_scores_high(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        _write_page(
            pbip / "report.Report",
            "ExecutiveSummary",
            {
                "width": 1280,
                "height": 720,
                "visualContainers": [
                    {"id": "kpi", "visual": {"$type": "card"}, "x": 100, "y": 80},
                    {"id": "trend", "visual": {"$type": "lineChart"}, "x": 500, "y": 80},
                    {"id": "bar", "visual": {"$type": "barChart"}, "x": 100, "y": 400},
                ],
            },
        )
        result = audit_report_ux_and_storytelling(pbip_path=str(pbip))
        assert result.audience_inferred == "executive"
        assert result.pages_analyzed == 1
        assert result.overall_score >= 80.0
        assert result.meets_target is True

    def test_audience_override(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        _write_page(
            pbip / "report.Report",
            "RandomPage",
            {
                "width": 1280,
                "height": 720,
                "visualContainers": [
                    {"id": "v1", "visual": {"$type": "card"}, "x": 100, "y": 80},
                ],
            },
        )
        result = audit_report_ux_and_storytelling(
            pbip_path=str(pbip), audience_assumed="analyst"
        )
        assert result.audience_inferred == "analyst"

    def test_strictness_accepted(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        _write_page(
            pbip / "report.Report",
            "Overview",
            {
                "width": 1280,
                "height": 720,
                "visualContainers": [],
            },
        )
        for strictness in ("lenient", "standard", "strict"):
            result = audit_report_ux_and_storytelling(
                pbip_path=str(pbip), strictness=strictness
            )
            assert isinstance(
                result, AuditReportUxAndStorytellingResult
            )

    def test_filter_to_single_page(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        _write_page(
            pbip / "report.Report",
            "Page1",
            {
                "width": 1280,
                "height": 720,
                "visualContainers": [
                    {"id": "v1", "visual": {"$type": "card"}, "x": 100, "y": 80}
                ],
            },
        )
        _write_page(
            pbip / "report.Report",
            "Page2",
            {
                "width": 1280,
                "height": 720,
                "visualContainers": [],
            },
        )
        result = audit_report_ux_and_storytelling(
            pbip_path=str(pbip), page_name="Page2"
        )
        assert result.pages_analyzed == 1

    def test_narrative_score_separate_from_overall(self, tmp_path: Path) -> None:
        """narrative_score must equal the category score for narrative,
        even when the overall aggregates all categories."""
        pbip = _make_pbip(tmp_path)
        _write_page(
            pbip / "report.Report",
            "ExecutiveSummary",
            {
                "width": 1280,
                "height": 720,
                "visualContainers": [
                    {"id": "table", "visual": {"$type": "tableEx"}, "y": 50},
                    {"id": "kpi", "visual": {"$type": "card"}, "y": 500},
                ],
            },
        )
        result = audit_report_ux_and_storytelling(pbip_path=str(pbip))
        # narrative = 60 because table is above kpi
        assert result.category_scores["narrative"] == 60.0
        assert result.narrative_score == 60.0

    def test_low_score_fails_meets_target(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        # Massive density violation + no hierarchy + broken narrative
        visuals: list[dict[str, object]] = []
        for i in range(15):
            visuals.append(
                {
                    "id": f"v{i}",
                    "visual": {"$type": "tableEx"},
                    "x": 0,
                    "y": i * 10,
                }
            )
        visuals.append(
            {"id": "kpi", "visual": {"$type": "card"}, "y": 1000}
        )
        _write_page(
            pbip / "report.Report",
            "ExecutiveSummary",
            {"width": 1280, "height": 720, "visualContainers": visuals},
        )
        result = audit_report_ux_and_storytelling(pbip_path=str(pbip))
        assert result.overall_score < 70.0
        assert result.meets_target is False
        assert len(result.findings) >= 3
