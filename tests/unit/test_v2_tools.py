"""Tests for the 3 v2 tools (Sprint 9) + viz foundation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from powerbi_orchestrator_mcp.tools.design_report_page_from_requirements import (
    design_report_page_from_requirements,
)
from powerbi_orchestrator_mcp.tools.refactor_to_calculation_groups import (
    TIME_VARIANTS,
    refactor_to_calculation_groups,
)
from powerbi_orchestrator_mcp.tools.select_visuals_for_kpis import (
    select_visuals_for_kpis,
)
from powerbi_orchestrator_mcp.viz.visual_registry import (
    VISUAL_REGISTRY,
    entries_matching,
    lookup,
)
from powerbi_orchestrator_mcp.viz.visual_suggester import (
    SuggesterInput,
    suggest,
    suggest_many,
)

# ---------------------------------------------------------------------------
# viz/visual_registry
# ---------------------------------------------------------------------------


class TestVisualRegistry:
    def test_registry_has_eight_visual_types(self) -> None:
        # MVP scope: 8 visual types per spec §2.1.
        assert len(VISUAL_REGISTRY) == 8

    def test_lookup_known(self) -> None:
        entry = lookup("card")
        assert entry is not None
        assert entry.display_name == "Card"

    def test_lookup_unknown(self) -> None:
        assert lookup("unknownVisual") is None

    def test_entries_matching_by_semantic_type(self) -> None:
        kpi_entries = entries_matching(semantic_type="single_value")
        # Both "card" and "kpi" support single_value.
        assert {e.type_id for e in kpi_entries} == {"card", "kpi"}

    def test_entries_matching_trend(self) -> None:
        trend_entries = entries_matching(semantic_type="trend")
        assert any(e.type_id == "lineChart" for e in trend_entries)

    def test_entries_matching_with_cardinality(self) -> None:
        # Pie chart has cardinality_range (2, 4). 5 should not match.
        comp_5 = entries_matching(semantic_type="composition", cardinality=5)
        pie_entries = [e for e in comp_5 if e.type_id == "pieChart"]
        assert pie_entries == []
        # 4 should match.
        comp_4 = entries_matching(semantic_type="composition", cardinality=4)
        assert any(e.type_id == "pieChart" for e in comp_4)

    def test_color_safe_flag(self) -> None:
        # Pie and donut are NOT colorblind-safe by default (per registry).
        assert lookup("pieChart").color_safe_default is False
        assert lookup("donutChart").color_safe_default is False
        # Others are safe.
        assert lookup("card").color_safe_default is True
        assert lookup("barChart").color_safe_default is True


# ---------------------------------------------------------------------------
# viz/visual_suggester
# ---------------------------------------------------------------------------


class TestVisualSuggesterSingle:
    def test_suggests_card_for_single_value_kpi(self) -> None:
        inp = SuggesterInput(
            name="Total Sales",
            semantic_type="single_value",
            fields=["[Sales]"],
            audience="executive",
        )
        sugs = suggest(inp)
        assert len(sugs) >= 1
        # Card or KPI should be top for executive + single_value.
        assert sugs[0].type in {"card", "kpi"}

    def test_suggests_line_chart_for_trend_kpi(self) -> None:
        inp = SuggesterInput(
            name="Revenue over time",
            semantic_type="trend",
            fields=["[Date]", "[Revenue]"],
            has_time=True,
            audience="executive",
        )
        sugs = suggest(inp)
        assert sugs[0].type == "lineChart"

    def test_penalizes_pie_for_high_cardinality(self) -> None:
        inp_pie_ok = SuggesterInput(
            name="Categories",
            semantic_type="composition",
            fields=["[Category]"],
            cardinality=3,
            audience="executive",
        )
        inp_pie_bad = SuggesterInput(
            name="Categories",
            semantic_type="composition",
            fields=["[Category]"],
            cardinality=10,
            audience="executive",
        )
        sugs_ok = suggest(inp_pie_ok)
        sugs_bad = suggest(inp_pie_bad)
        # Pie should appear in top-3 for cardinality=3 but be penalized for 10.
        types_ok = {s.type for s in sugs_ok[:3]}
        types_bad = {s.type for s in sugs_bad[:3]}
        assert "pieChart" in types_ok
        assert "pieChart" not in types_bad

    def test_penalizes_pie_with_time(self) -> None:
        inp = SuggesterInput(
            name="X over time",
            semantic_type="trend",
            fields=["[Date]", "[X]"],
            has_time=True,
            audience="executive",
        )
        sugs = suggest(inp)
        # Pie shouldn't be in top-3 for trend + time.
        types = {s.type for s in sugs[:3]}
        assert "pieChart" not in types
        assert "donutChart" not in types

    def test_audience_preference(self) -> None:
        inp = SuggesterInput(
            name="Total",
            semantic_type="single_value",
            fields=["[X]"],
            audience="analyst",
        )
        # Analyst gets "card" or "kpi" but maybe other things.
        sugs = suggest(inp)
        # Just verify it returned at least 1 suggestion.
        assert len(sugs) >= 1

    def test_suggest_many(self) -> None:
        results = suggest_many([
            SuggesterInput(name="KPI 1", semantic_type="single_value", fields=["[A]"]),
            SuggesterInput(name="KPI 2", semantic_type="trend", fields=["[Date]"], has_time=True),
        ])
        assert "KPI 1" in results
        assert "KPI 2" in results


# ---------------------------------------------------------------------------
# tools/refactor_to_calculation_groups
# ---------------------------------------------------------------------------


class _InspectorForRefactor:
    def __init__(self, measures: list[dict[str, Any]]) -> None:
        self._measures = measures

    def list_measures(self) -> list[dict[str, Any]]:
        return self._measures


def _ok_writer(**kwargs: Any) -> dict[str, Any]:
    return {"changed_files": ["model.tmdl"], "error_message": None}


class TestRefactorCalcGroups:
    def test_detects_time_variant_suffixes(self) -> None:
        # Smoke-test the variant list is what we expect.
        assert "YTD" in TIME_VARIANTS
        assert "QTD" in TIME_VARIANTS
        assert "MTD" in TIME_VARIANTS
        assert "YOY" in TIME_VARIANTS

    def test_groups_three_time_variants(self) -> None:
        measures = [
            {"name": "Total Sales YTD", "expression": "TOTALYTD([A])"},
            {"name": "Total Sales QTD", "expression": "TOTALQTD([A])"},
            {"name": "Total Sales MTD", "expression": "TOTALMTD([A])"},
        ]
        result = refactor_to_calculation_groups(
            target="x", min_candidates=3, inspector=_InspectorForRefactor(measures)
        )
        assert len(result.groups_created) == 1
        plan = result.groups_created[0]
        assert plan.template_measure == "Total Sales"
        item_names = {item["name"] for item in plan.items}
        assert item_names == {"YTD", "QTD", "MTD"}

    def test_does_not_group_below_threshold(self) -> None:
        measures = [
            {"name": "Total Sales YTD", "expression": "TOTALYTD([A])"},
            {"name": "Total Sales QTD", "expression": "TOTALQTD([A])"},
        ]
        result = refactor_to_calculation_groups(
            target="x", min_candidates=3, inspector=_InspectorForRefactor(measures)
        )
        # Below threshold → no groups.
        assert result.groups_created == []
        assert any("≥3" in w for w in result.warnings)

    def test_dry_run_does_not_persist(self) -> None:
        measures = [
            {"name": "Total Sales YTD", "expression": "TOTALYTD([A])"},
            {"name": "Total Sales QTD", "expression": "TOTALQTD([A])"},
            {"name": "Total Sales MTD", "expression": "TOTALMTD([A])"},
        ]
        result = refactor_to_calculation_groups(
            target="x", auto_apply=False, inspector=_InspectorForRefactor(measures)
        )
        assert result.dry_run is True
        assert result.changed_files == []

    def test_measures_remapped(self) -> None:
        measures = [
            {"name": "Total Sales YTD", "expression": "TOTALYTD([A])"},
            {"name": "Total Sales QTD", "expression": "TOTALQTD([A])"},
            {"name": "Total Sales MTD", "expression": "TOTALMTD([A])"},
        ]
        result = refactor_to_calculation_groups(
            target="x", inspector=_InspectorForRefactor(measures)
        )
        assert "Total Sales YTD" in result.measures_remapped
        assert "Total Sales QTD" in result.measures_remapped
        assert "Total Sales MTD" in result.measures_remapped

    def test_no_inspector_returns_warning(self) -> None:
        result = refactor_to_calculation_groups(target="x", inspector=None)
        assert any("inspector" in w for w in result.warnings)

    def test_no_time_variant_measures_are_ignored(self) -> None:
        measures = [
            {"name": "Plain Total", "expression": "SUM([A])"},
            {"name": "Plain Count", "expression": "COUNT([A])"},
            {"name": "Plain Avg", "expression": "AVERAGE([A])"},
        ]
        result = refactor_to_calculation_groups(
            target="x", inspector=_InspectorForRefactor(measures)
        )
        assert result.groups_created == []


# ---------------------------------------------------------------------------
# tools/select_visuals_for_kpis
# ---------------------------------------------------------------------------


class TestSelectVisualsForKpis:
    def test_suggests_for_each_kpi(self) -> None:
        kpis_json = json.dumps([
            {"name": "Total Sales", "semantic_type": "single_value", "fields": ["[Sales]"]},
            {"name": "Trend", "semantic_type": "trend", "fields": ["[Date]"], "has_time": True},
        ])
        result = select_visuals_for_kpis(kpis_json=kpis_json)
        assert len(result.recommendations) == 2
        assert result.coverage_pct == 100.0

    def test_invalid_json_returns_warning(self) -> None:
        result = select_visuals_for_kpis(kpis_json="not-json")
        assert result.recommendations == []
        assert any("parse" in w.lower() for w in result.warnings)

    def test_empty_list(self) -> None:
        result = select_visuals_for_kpis(kpis_json="[]")
        assert result.recommendations == []
        assert result.coverage_pct == 100.0

    def test_no_suggestion_for_unknown_semantic_type(self) -> None:
        # An exotic semantic_type still returns *some* fallback.
        kpis_json = json.dumps([
            {"name": "X", "semantic_type": "unknown_type", "fields": ["[A]"]},
        ])
        result = select_visuals_for_kpis(kpis_json=kpis_json)
        assert len(result.recommendations) >= 1


# ---------------------------------------------------------------------------
# tools/design_report_page_from_requirements
# ---------------------------------------------------------------------------


@pytest.fixture()
def pbip_with_dataset_dir(tmp_path: Path) -> Path:
    """Minimal PBIP with a .Dataset dir."""
    pbip = tmp_path / "demo.pbip"
    pbip.mkdir()
    (pbip / "demo.pbip").write_text("{}")
    (pbip / "demo.Dataset").mkdir()
    (pbip / "demo.Dataset" / "definition.tmdl").write_text("")
    return pbip


class TestDesignReportPage:
    def test_missing_pbip_returns_warning(self, tmp_path: Path) -> None:
        result = design_report_page_from_requirements(
            pbip_path=str(tmp_path / "missing"),
            brief="KPI Total Sales",
        )
        assert result.visual_count == 0
        assert any("does not exist" in w for w in result.warnings)

    def test_brief_with_kpis_creates_visuals(self, pbip_with_dataset_dir: Path) -> None:
        result = design_report_page_from_requirements(
            pbip_path=str(pbip_with_dataset_dir),
            brief="KPI Total Sales, MoM% trend, Top 10 products by Region",
        )
        assert result.visual_count >= 2
        # Page file written.
        page_path = (
            pbip_with_dataset_dir
            / "demo.Report"
            / "pages"
            / "Overview"
            / "page.json"
        )
        assert page_path.exists()
        assert "page.json" in result.files_changed[0]

    def test_brief_without_kpis_returns_warning(
        self, pbip_with_dataset_dir: Path
    ) -> None:
        result = design_report_page_from_requirements(
            pbip_path=str(pbip_with_dataset_dir),
            brief="Some text without KPIs",
        )
        assert result.visual_count == 0
        assert any("no kpis" in w.lower() for w in result.warnings)

    def test_existing_page_is_overwritten_atomically(
        self, pbip_with_dataset_dir: Path
    ) -> None:
        # Pre-create a page file.
        report_dir = pbip_with_dataset_dir / "demo.Report"
        page_dir = report_dir / "pages" / "Overview"
        page_dir.mkdir(parents=True)
        existing = page_dir / "page.json"
        existing.write_text('{"existing": true}')

        result = design_report_page_from_requirements(
            pbip_path=str(pbip_with_dataset_dir),
            brief="KPI Total Sales",
        )
        assert result.visual_count >= 1
        # File was replaced atomically (no temp files left behind).
        assert existing.exists()
        temps = list(page_dir.glob(".page.*.tmp"))
        assert temps == []

    def test_executive_audience_enables_mobile_layout(
        self, pbip_with_dataset_dir: Path
    ) -> None:
        design_report_page_from_requirements(
            pbip_path=str(pbip_with_dataset_dir),
            brief="KPI Total Sales",
            audience="executive",
        )
        page_path = (
            pbip_with_dataset_dir
            / "demo.Report"
            / "pages"
            / "Overview"
            / "page.json"
        )
        data = json.loads(page_path.read_text(encoding="utf-8"))
        assert data.get("mobileLayout") is not None

    def test_rationale_lists_detected_kpis(
        self, pbip_with_dataset_dir: Path
    ) -> None:
        result = design_report_page_from_requirements(
            pbip_path=str(pbip_with_dataset_dir),
            brief="Top 10 products by Region, Revenue trend over time",
        )
        assert "Top" in result.rationale or "products" in result.rationale
        assert "Revenue" in result.rationale or "time" in result.rationale
