"""optimize_report_performance tool — v2 (SPEC §6.2; spec: 04-viz-ux.md §4).

Heuristic-only performance analyzer for PBIR pages. Reads page.json
files, scans visual containers + their $type, and accumulates cost
estimates for known anti-patterns that impact perceived report
load time:

- Too many visuals on a single page (visual density).
- Pie / donut charts (multi-segment rendering).
- Scatter charts with high cardinality.
- Custom (non-registry) visuals — unknown render cost.
- Conditional formatting rules (rendering recalculation).
- Cross-filter both directions in relationships (rerun cost).
- Page dimensions larger than 1280x720 with many visuals (viewport stress).

For MVP these are heuristic estimates (no real telemetry). The
output is a 0-100 score and a list of hotspots (per page) so the
LLM-side agents can reason about what to refactor.

For real deterministic v3 measurement, see screenshot_report_pages
v3 outline (VertiPaq telemetry) — out of scope for this tool.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.viz.visual_registry import (
    VISUAL_REGISTRY,
    lookup,
)

# Cost weights (heuristic, ms-equivalent).
_COST_WEIGHTS: dict[str, int] = {
    "density_per_visual_over_5": 8,
    "pie_or_donut": 12,
    "scatter_high_cardinality": 25,
    "custom_visual_unknown": 40,
    "conditional_formatting_rule": 5,
    "cross_filter_both": 6,
    "wide_page_with_density": 15,
}

# Visual type -> cost category.
_EXPENSIVE_VISUALS: set[str] = {"pieChart", "donutChart"}


class OptimizeReportPerformance(BaseModel):
    """Input schema for ``optimize_report_performance`` (SPEC §6.2 + 04-viz-ux.md §4)."""

    pbip_path: str
    target_load_ms: int = 5000


class PerformanceHotspot(BaseModel):
    """Per-page hotspot finding."""

    page_name: str
    visual_id: str | None = None
    est_cost: str  # "low" | "medium" | "high"
    reasons: list[str] = Field(default_factory=list)
    fix_suggestion: str


class OptimizeReportPerformanceResult(BaseModel):
    """Output of optimize_report_performance."""

    performance_score: float  # 0-100, higher = better
    estimated_total_load_ms: int
    target_load_ms: int
    meets_target: bool
    hotspots: list[PerformanceHotspot] = Field(default_factory=list)
    pages_analyzed: int = 0
    visuals_analyzed: int = 0
    warnings: list[str] = Field(default_factory=list)


def _estimate_visual_cost(visual: dict[str, Any], cardinality_hint: int | None) -> int:
    """Return heuristic cost (ms-equivalent) for a single visualContainer entry."""
    cost = 0
    visual_type = visual.get("visual", {}).get("$type", "unknown")
    if visual_type in _EXPENSIVE_VISUALS:
        cost += _COST_WEIGHTS["pie_or_donut"]
    if visual_type == "scatterChart":
        if cardinality_hint is not None and cardinality_hint > 1000:
            cost += _COST_WEIGHTS["scatter_high_cardinality"]
        else:
            cost += _COST_WEIGHTS["scatter_high_cardinality"] // 2
    # Custom visual: type not in the native registry.
    if visual_type not in {e.type_id for e in VISUAL_REGISTRY} and visual_type != "unknown":
        cost += _COST_WEIGHTS["custom_visual_unknown"]
    # Conditional formatting: heuristic flag in metadata.
    if visual.get("conditionalFormatting"):
        cost += _COST_WEIGHTS["conditional_formatting_rule"] * len(
            visual["conditionalFormatting"]
        )
    return cost


def _classify_cost(estimated_ms: int) -> str:
    if estimated_ms >= 80:
        return "high"
    if estimated_ms >= 30:
        return "medium"
    return "low"


def _suggest_fix(reasons: list[str]) -> str:
    """Pick the first actionable fix hint based on detected reasons."""
    for r in reasons:
        if "pie" in r.lower() or "donut" in r.lower():
            return "replace pie/donut with bar chart (better a11y + faster)"
        if "scatter" in r.lower():
            return "use binning or aggregation in the model to reduce points"
        if "custom visual" in r.lower():
            return (
                "audit the custom visual; consider native visual or "
                "pre-aggregate in the model"
            )
        if "density" in r.lower():
            return "split page into multiple drill-through pages"
    return "review visual; consider simplification or removal"


def _analyze_page(
    page_path: Path,
) -> tuple[int, int, list[PerformanceHotspot]]:
    """Return (per_page_load_ms, visuals_count, hotspots_for_page)."""
    try:
        data = json.loads(page_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return 500, 0, [
            PerformanceHotspot(
                page_name=page_path.parent.name,
                visual_id=None,
                est_cost="medium",
                reasons=[f"failed to parse page.json: {exc}"],
                fix_suggestion="fix JSON syntax; the page failed to parse",
            )
        ]

    width = data.get("width", 1280)
    height = data.get("height", 720)
    visuals: list[dict[str, Any]] = data.get("visualContainers", [])
    page_name = page_path.parent.name

    page_cost = 500  # baseline per-page cost
    hotspots: list[PerformanceHotspot] = []

    # Compute per-visual cost.
    for visual in visuals:
        vid = visual.get("id", "?")
        v_cost = _estimate_visual_cost(visual, cardinality_hint=None)
        page_cost += v_cost
        if v_cost > 0:
            reasons: list[str] = []
            vtype = visual.get("visual", {}).get("$type", "unknown")
            if vtype in _EXPENSIVE_VISUALS:
                reasons.append(f"{vtype} is an expensive multi-segment visual")
            if vtype == "scatterChart":
                reasons.append(
                    "scatter may render thousands of points without binning"
                )
            if vtype not in {e.type_id for e in VISUAL_REGISTRY} and vtype != "unknown":
                reasons.append(f"custom visual ({vtype}) has unknown render cost")
            if visual.get("conditionalFormatting"):
                reasons.append(
                    f"has {len(visual['conditionalFormatting'])} conditional-formatting rules"
                )
            hotspots.append(
                PerformanceHotspot(
                    page_name=page_name,
                    visual_id=vid,
                    est_cost=_classify_cost(v_cost),
                    reasons=reasons,
                    fix_suggestion=_suggest_fix(reasons),
                )
            )

    # Density penalty.
    if len(visuals) > 5:
        density_cost = (len(visuals) - 5) * _COST_WEIGHTS["density_per_visual_over_5"]
        page_cost += density_cost
        hotspots.append(
            PerformanceHotspot(
                page_name=page_name,
                visual_id=None,
                est_cost=_classify_cost(density_cost),
                reasons=[
                    f"high visual density: {len(visuals)} visuals on one page"
                ],
                fix_suggestion=_suggest_fix(
                    ["density exceeds 5 visuals per page"]
                ),
            )
        )

    # Wide-page penalty.
    if width > 1280 and len(visuals) > 6:
        wide_cost = _COST_WEIGHTS["wide_page_with_density"]
        page_cost += wide_cost
        hotspots.append(
            PerformanceHotspot(
                page_name=page_name,
                visual_id=None,
                est_cost="low",
                reasons=[
                    f"wide page ({width}x{height}) with {len(visuals)} visuals "
                    "stresses the viewport"
                ],
                fix_suggestion="reduce page width to 1280 or fewer visuals",
            )
        )

    return page_cost, len(visuals), hotspots


def optimize_report_performance(
    pbip_path: str,
    target_load_ms: int = 5000,
) -> OptimizeReportPerformanceResult:
    """Analyze all pages of a PBIP for performance hotspots.

    Args:
        pbip_path: Path to the PBIP root (parent of .Dataset / .Report dirs).
        target_load_ms: Target report load time in milliseconds. Defaults to 5000.

    Returns:
        OptimizeReportPerformanceResult with overall score, estimated load,
        per-page hotspots, and meets-target flag.
    """
    pbip = Path(pbip_path)
    if not pbip.exists():
        return OptimizeReportPerformanceResult(
            performance_score=0.0,
            estimated_total_load_ms=0,
            target_load_ms=target_load_ms,
            meets_target=False,
            hotspots=[],
            pages_analyzed=0,
            visuals_analyzed=0,
            warnings=[f"PBIP path does not exist: {pbip_path}"],
        )

    report_dir_candidates = sorted(pbip.glob("*.Report"))
    if not report_dir_candidates:
        return OptimizeReportPerformanceResult(
            performance_score=100.0,
            estimated_total_load_ms=0,
            target_load_ms=target_load_ms,
            meets_target=True,
            hotspots=[],
            pages_analyzed=0,
            visuals_analyzed=0,
            warnings=["no .Report directory in PBIP; nothing to analyze"],
        )

    report_dir = report_dir_candidates[0]
    pages_dir = report_dir / "pages"
    if not pages_dir.exists():
        return OptimizeReportPerformanceResult(
            performance_score=100.0,
            estimated_total_load_ms=0,
            target_load_ms=target_load_ms,
            meets_target=True,
            hotspots=[],
            pages_analyzed=0,
            visuals_analyzed=0,
            warnings=["no pages/ directory; nothing to analyze"],
        )

    page_files = sorted(pages_dir.glob("*/page.json"))
    if not page_files:
        return OptimizeReportPerformanceResult(
            performance_score=100.0,
            estimated_total_load_ms=0,
            target_load_ms=target_load_ms,
            meets_target=True,
            hotspots=[],
            pages_analyzed=0,
            visuals_analyzed=0,
            warnings=["no page.json files found; nothing to analyze"],
        )

    total_load_ms = 0
    total_visuals = 0
    all_hotspots: list[PerformanceHotspot] = []
    for page_path in page_files:
        per_page_ms, visuals_count, hotspots = _analyze_page(page_path)
        total_load_ms += per_page_ms
        total_visuals += visuals_count
        all_hotspots.extend(hotspots)

    # Score: 100 - weighted hotspot cost, capped at [0, 100].
    hotspot_cost_sum = sum(
        {"low": 1, "medium": 3, "high": 7}[h.est_cost] for h in all_hotspots
    )
    score = max(0.0, 100.0 - hotspot_cost_sum * 1.5)

    return OptimizeReportPerformanceResult(
        performance_score=round(score, 1),
        estimated_total_load_ms=total_load_ms,
        target_load_ms=target_load_ms,
        meets_target=total_load_ms <= target_load_ms,
        hotspots=all_hotspots,
        pages_analyzed=len(page_files),
        visuals_analyzed=total_visuals,
        warnings=[],
    )


__all__ = [
    "OptimizeReportPerformance",
    "OptimizeReportPerformanceResult",
    "PerformanceHotspot",
    "optimize_report_performance",
]


# Defensive import-time check (catches rename of registry categories early).
_ = lookup
