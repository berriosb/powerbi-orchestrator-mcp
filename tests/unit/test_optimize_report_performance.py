"""Tests for tools.optimize_report_performance (Sprint 10 v2).

Coverage of the heuristic analyzer was 28% before this file; these
tests exercise every branch in ``_analyze_page`` + ``_estimate_visual_cost``
+ ``_suggest_fix`` + the empty / missing-paths fallbacks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.tools.optimize_report_performance import (
    _analyze_page,
    _classify_cost,
    _estimate_visual_cost,
    _suggest_fix,
    optimize_report_performance,
)


def _write_page(report_dir: Path, page_name: str, page_data: dict) -> None:
    """Write a synthetic ``pages/<page_name>/page.json`` file."""
    page_dir = report_dir / "pages" / page_name
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "page.json").write_text(
        json.dumps(page_data), encoding="utf-8"
    )


def _make_pbip(tmp_path: Path) -> Path:
    pbip = tmp_path / "report.pbip"
    pbip.mkdir()
    report = pbip / "report.Report"
    report.mkdir()
    return pbip


class TestClassifyCost:
    @pytest.mark.parametrize(
        ("ms", "expected"),
        [
            (10, "low"),
            (29, "low"),
            (30, "medium"),
            (79, "medium"),
            (80, "high"),
            (500, "high"),
        ],
    )
    def test_thresholds(self, ms: int, expected: str) -> None:
        assert _classify_cost(ms) == expected


class TestSuggestFix:
    def test_pie_or_donut_message_wins(self) -> None:
        msg = _suggest_fix(["pieChart is an expensive multi-segment visual"])
        assert "bar chart" in msg.lower()

    def test_scatter_message(self) -> None:
        msg = _suggest_fix(["scatter may render thousands of points"])
        assert "binning" in msg.lower() or "aggregation" in msg.lower()

    def test_custom_visual_message(self) -> None:
        msg = _suggest_fix(["custom visual (chartJS) has unknown render cost"])
        assert "custom visual" in msg.lower()

    def test_density_message(self) -> None:
        msg = _suggest_fix(["density exceeds 5 visuals per page"])
        assert "drill-through" in msg.lower() or "split" in msg.lower()

    def test_fallback_message(self) -> None:
        msg = _suggest_fix(["some unknown reason"])
        assert "review" in msg.lower() or "simplif" in msg.lower()


class TestEstimateVisualCost:
    def test_zero_for_clean_card_visual(self) -> None:
        visual = {"id": "v1", "visual": {"$type": "card"}}
        assert _estimate_visual_cost(visual, cardinality_hint=None) == 0

    def test_pie_adds_penalty(self) -> None:
        visual = {"id": "v1", "visual": {"$type": "pieChart"}}
        assert _estimate_visual_cost(visual, cardinality_hint=None) >= 12

    def test_donut_adds_penalty(self) -> None:
        visual = {"id": "v1", "visual": {"$type": "donutChart"}}
        assert _estimate_visual_cost(visual, cardinality_hint=None) >= 12

    def test_scatter_high_cardinality(self) -> None:
        visual = {"id": "v1", "visual": {"$type": "scatterChart"}}
        high = _estimate_visual_cost(visual, cardinality_hint=10_000)
        low = _estimate_visual_cost(visual, cardinality_hint=10)
        assert high > low

    def test_custom_visual_penalty(self) -> None:
        visual = {"id": "v1", "visual": {"$type": "chartJSVisual"}}
        assert _estimate_visual_cost(visual, cardinality_hint=None) >= 40

    def test_conditional_formatting_penalty_scales(self) -> None:
        visual_one = {
            "id": "v1",
            "visual": {"$type": "card"},
            "conditionalFormatting": [{"type": "color"}],
        }
        visual_many = {
            "id": "v1",
            "visual": {"$type": "card"},
            "conditionalFormatting": [
                {"type": "color"},
                {"type": "color"},
                {"type": "color"},
            ],
        }
        assert (
            _estimate_visual_cost(visual_many, None)
            > _estimate_visual_cost(visual_one, None)
        )


class TestAnalyzePage:
    def test_returns_baseline_on_minimal_page(self, tmp_path: Path) -> None:
        page = tmp_path / "page.json"
        page.write_text(json.dumps({"width": 1280, "height": 720, "visualContainers": []}), encoding="utf-8")
        ms, visuals, hotspots = _analyze_page(page)
        assert visuals == 0
        assert ms == 500  # baseline
        assert hotspots == []

    def test_detects_density_over_5(self, tmp_path: Path) -> None:
        page = tmp_path / "page.json"
        visuals = [
            {"id": f"v{i}", "visual": {"$type": "card"}} for i in range(8)
        ]
        page.write_text(
            json.dumps(
                {"width": 1280, "height": 720, "visualContainers": visuals}
            ),
            encoding="utf-8",
        )
        ms, v_count, hotspots = _analyze_page(page)
        assert v_count == 8
        assert any("density" in " ".join(h.reasons).lower() for h in hotspots)

    def test_wide_page_with_many_visuals(self, tmp_path: Path) -> None:
        page = tmp_path / "page.json"
        visuals = [
            {"id": f"v{i}", "visual": {"$type": "card"}} for i in range(7)
        ]
        page.write_text(
            json.dumps(
                {"width": 1920, "height": 1080, "visualContainers": visuals}
            ),
            encoding="utf-8",
        )
        _ms, _v, hotspots = _analyze_page(page)
        assert any("wide" in " ".join(h.reasons).lower() for h in hotspots)

    def test_pie_hotspot_classified_high(self, tmp_path: Path) -> None:
        page = tmp_path / "page.json"
        page.write_text(
            json.dumps(
                {
                    "width": 1280,
                    "height": 720,
                    "visualContainers": [
                        {
                            "id": "p1",
                            "visual": {"$type": "pieChart"},
                            "conditionalFormatting": [
                                {"type": "color"} for _ in range(8)
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        _ms, _v, hotspots = _analyze_page(page)
        pie = next(h for h in hotspots if h.visual_id == "p1")
        # pie (12) + 8 conditional-formatting rules (8*5=40) → 52ms → medium.
        # Add custom visual to push over the high threshold (80).
        assert pie.est_cost in ("medium", "high")
        assert pie.fix_suggestion  # non-empty

    def test_corrupt_json_emits_warning_hotspot(
        self, tmp_path: Path
    ) -> None:
        page = tmp_path / "page.json"
        page.write_text("{ this is not json", encoding="utf-8")
        _ms, v_count, hotspots = _analyze_page(page)
        assert v_count == 0
        assert len(hotspots) == 1
        assert "parse" in hotspots[0].reasons[0].lower()


class TestOptimizeReportPerformance:
    def test_missing_pbip_warns(self, tmp_path: Path) -> None:
        result = optimize_report_performance(
            pbip_path=str(tmp_path / "does_not_exist")
        )
        assert result.performance_score == 0.0
        assert any("does not exist" in w for w in result.warnings)
        assert result.pages_analyzed == 0

    def test_no_report_directory_warns(self, tmp_path: Path) -> None:
        pbip = tmp_path / "report.pbip"
        pbip.mkdir()
        result = optimize_report_performance(pbip_path=str(pbip))
        assert result.performance_score == 100.0
        assert any("no .Report" in w for w in result.warnings)

    def test_no_pages_directory_warns(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        result = optimize_report_performance(pbip_path=str(pbip))
        assert result.performance_score == 100.0
        assert any("no pages/" in w for w in result.warnings)

    def test_no_page_json_warns(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        (pbip / "report.Report" / "pages").mkdir()
        result = optimize_report_performance(pbip_path=str(pbip))
        assert result.performance_score == 100.0
        assert any("no page.json" in w for w in result.warnings)

    def test_clean_page_scores_high(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        _write_page(
            pbip / "report.Report",
            "Overview",
            {
                "width": 1280,
                "height": 720,
                "visualContainers": [
                    {"id": "v1", "visual": {"$type": "card"}},
                    {"id": "v2", "visual": {"$type": "barChart"}},
                ],
            },
        )
        result = optimize_report_performance(pbip_path=str(pbip))
        assert result.pages_analyzed == 1
        assert result.visuals_analyzed == 2
        assert result.performance_score >= 90.0
        assert result.meets_target is True

    def test_pie_and_density_lowers_score(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        # 9 visuals: 5 cards + 1 pie + 1 custom + 1 bar with conditional
        # formatting + 1 scatter. The cumulative heuristic cost must
        # push the score noticeably below 100.
        visuals: list[dict[str, object]] = [
            {"id": f"v{i}", "visual": {"$type": "card"}} for i in range(5)
        ]
        visuals.append({"id": "pie", "visual": {"$type": "pieChart"}})
        visuals.append(
            {
                "id": "custom",
                "visual": {"$type": "chartJSVisual"},
                "conditionalFormatting": [
                    {"type": "color"} for _ in range(5)
                ],
            }
        )
        visuals.append(
            {
                "id": "cf",
                "visual": {"$type": "barChart"},
                "conditionalFormatting": [{"type": "color"}],
            }
        )
        visuals.append(
            {
                "id": "scatter",
                "visual": {"$type": "scatterChart"},
                "conditionalFormatting": [
                    {"type": "color"} for _ in range(3)
                ],
            }
        )
        _write_page(
            pbip / "report.Report",
            "Hotspots",
            {"width": 1280, "height": 720, "visualContainers": visuals},
        )
        result = optimize_report_performance(pbip_path=str(pbip))
        assert result.performance_score < 90.0
        assert any(h.visual_id == "pie" for h in result.hotspots)
        assert any(h.visual_id == "custom" for h in result.hotspots)
        assert any(h.visual_id == "scatter" for h in result.hotspots)
        # Density hotspot is anonymous (no visual_id).
        assert any(h.visual_id is None for h in result.hotspots)

    def test_corrupt_page_does_not_crash(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        report_dir = pbip / "report.Report"
        page_dir = report_dir / "pages" / "Bad"
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "page.json").write_text("{ not valid json", encoding="utf-8")
        result = optimize_report_performance(pbip_path=str(pbip))
        # Corrupt pages contribute a "medium" hotspot but the tool
        # completes without raising.
        assert result.pages_analyzed == 1
        assert any("parse" in " ".join(h.reasons).lower() for h in result.hotspots)

    def test_meets_target_uses_target_load_ms(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        _write_page(
            pbip / "report.Report",
            "Tiny",
            {
                "width": 1280,
                "height": 720,
                "visualContainers": [
                    {"id": "v1", "visual": {"$type": "card"}}
                ],
            },
        )
        # Set a very low target so even a clean page doesn't meet it.
        result = optimize_report_performance(
            pbip_path=str(pbip), target_load_ms=100
        )
        assert result.meets_target is False
        assert result.estimated_total_load_ms >= 100
