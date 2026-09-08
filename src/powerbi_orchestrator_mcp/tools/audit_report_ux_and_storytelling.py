"""audit_report_ux_and_storytelling tool — v2 (SPEC §6.2; spec: specs/tools/audit-report-ux-and-storytelling.md).

Heuristic qualitative auditor for PBIR pages. Complements
``audit_model_and_report`` (BPA + WCAG + DAX lint) with subjective UX
checks that aren't capturable by static analysis:

- Hierarchy: is the KPI in the top-left for executive audiences?
- Density: too many visuals crowded together?
- Narrative: do KPIs come before drill-downs?
- Mobile-readiness: would the layout survive a phone screen?
- Cohesion: mixed visual styles (pie + line + table) on same page?

The output is an overall 0-100 score with category breakdown and a list
of findings, each marked auto-fixable (with suggestion text) or not.

For MVP these are heuristic-only. The deterministic v3 variant with
rendering telemetry is part of the v3 screenshot_report_pages outline.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_KPI_VISUAL_TYPES: set[str] = {"card", "kpi"}
_PRIMARY_VISUAL_TYPES: set[str] = {"card", "kpi", "lineChart", "barChart"}
_NARRATIVE_TOP: set[str] = {"card", "kpi"}
_NARRATIVE_DETAIL: set[str] = {"tableEx", "matrix"}
_MAX_VISUALS_FOR_EXECUTIVE = 8
_MAX_VISUALS_FOR_MOBILE = 4
_DENSITY_GAP_THRESHOLD = 8

# Audience inference from page name patterns.
_AUDIENCE_PATTERNS: dict[str, re.Pattern[str]] = {
    "executive": re.compile(r"\b(exec|summary|overview|cockpit|kpi|headline)\b", re.I),
    "analyst": re.compile(r"\b(analysis|deep|drill|detail|breakdown)\b", re.I),
    "operational": re.compile(r"\b(ops|monitor|alert|triage)\b", re.I),
}

# Anti-pattern reasons list used by density / cohesion heuristics.
_VISUAL_STYLE_BUCKETS: dict[str, set[str]] = {
    "kpi": {"card", "kpi"},
    "trend": {"lineChart"},
    "comparison": {"barChart"},
    "composition": {"pieChart", "donutChart", "treemap", "stackedBarChart"},
    "distribution": {"scatterChart"},
    "tabular": {"tableEx", "matrix"},
}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AuditReportUxAndStorytelling(BaseModel):
    """Input schema."""

    pbip_path: str
    page_name: str | None = None  # default: audit all pages
    audience_assumed: str | None = None  # default: infer from page_name
    strictness: str = "standard"  # lenient | standard | strict


class UxFinding(BaseModel):
    """One actionable UX issue detected in the report."""

    page_name: str
    category: str  # hierarchy | density | narrative | mobile | cohesion | pie_size
    severity: str  # info | warning | error
    location: str  # "visual v1", "page header", etc.
    message: str
    suggestion: str
    auto_fixable: bool


class AuditReportUxAndStorytellingResult(BaseModel):
    """Output schema."""

    overall_score: float  # 0-100, higher = better
    category_scores: dict[str, float] = Field(default_factory=dict)
    narrative_score: float = 100.0  # sub-score of storytelling (0-100)
    meets_target: bool = False
    findings: list[UxFinding] = Field(default_factory=list)
    pages_analyzed: int = 0
    audience_inferred: str = "executive"
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class PageContext:
    """State carried between heuristics for one page."""

    page_name: str
    audience: str
    width: int
    height: int
    visuals: list[dict[str, Any]]
    strictness: str

    @property
    def max_visuals(self) -> int:
        return {
            "executive": _MAX_VISUALS_FOR_EXECUTIVE,
            "analyst": 12,
            "operational": 15,
        }.get(self.audience, _MAX_VISUALS_FOR_EXECUTIVE)


def _infer_audience(page_name: str) -> str:
    for audience, pattern in _AUDIENCE_PATTERNS.items():
        if pattern.search(page_name):
            return audience
    return "executive"


def _load_pages(pbip: Path, filter_page: str | None) -> list[PageContext]:
    """Read PBIR pages and return PageContexts."""
    report_dir_candidates = sorted(pbip.glob("*.Report"))
    if not report_dir_candidates:
        return []
    pages_dir = report_dir_candidates[0] / "pages"
    if not pages_dir.exists():
        return []

    out: list[PageContext] = []
    for page_path in sorted(pages_dir.glob("*/page.json")):
        page_name = page_path.parent.name
        if filter_page and page_name != filter_page:
            continue
        try:
            data = json.loads(page_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            PageContext(
                page_name=page_name,
                audience="executive",  # overwritten by caller
                width=int(data.get("width", 1280)),
                height=int(data.get("height", 720)),
                visuals=list(data.get("visualContainers", [])),
                strictness="standard",
            )
        )
    return out


def _strictness_threshold(strictness: str, base: int) -> int:
    """Adjust thresholds by strictness."""
    return {"lenient": int(base * 1.5), "standard": base, "strict": max(1, base // 2)}.get(
        strictness, base
    )


def _check_hierarchy(ctx: PageContext, findings: list[UxFinding]) -> float:
    """Score 0-100 for visual hierarchy."""
    if not ctx.visuals:
        return 100.0
    if ctx.audience != "executive":
        # Less critical for analysts (drill-downs are expected).
        return 90.0

    # Look for KPI/card in the top-left quadrant.
    has_kpi_top_left = False
    for v in ctx.visuals:
        vtype = v.get("visual", {}).get("$type", "")
        x = float(v.get("x", 0))
        y = float(v.get("y", 0))
        if vtype in _KPI_VISUAL_TYPES and x < 320 and y < 200:
            has_kpi_top_left = True
            break

    if has_kpi_top_left:
        return 100.0

    findings.append(
        UxFinding(
            page_name=ctx.page_name,
            category="hierarchy",
            severity="warning",
            location="page header",
            message="No KPI/card visual in top-left quadrant; executives expect it there",
            suggestion=(
                "Move the primary KPI/card visual to (x<=320, y<=200); "
                "executive audiences scan top-left first"
            ),
            auto_fixable=False,
        )
    )
    return 50.0


def _check_density(ctx: PageContext, findings: list[UxFinding]) -> float:
    """Score 0-100 for visual density."""
    if len(ctx.visuals) <= ctx.max_visuals:
        return 100.0

    over = len(ctx.visuals) - ctx.max_visuals
    # Also flag if there's no gap (all visuals clustered).
    xs = sorted(float(v.get("x", 0)) for v in ctx.visuals)
    spread = (xs[-1] - xs[0]) if xs else 0

    if over > _DENSITY_GAP_THRESHOLD or (spread == 0 and len(ctx.visuals) > 4):
        severity = "error"
    elif over > 3:
        severity = "warning"
    else:
        severity = "info"

    findings.append(
        UxFinding(
            page_name=ctx.page_name,
            category="density",
            severity=severity,
            location=f"{len(ctx.visuals)} visuals",
            message=(
                f"Page has {len(ctx.visuals)} visuals (max for {ctx.audience}: "
                f"{ctx.max_visuals}); consider splitting"
            ),
            suggestion=(
                "Split into multiple drill-through pages, or move secondary "
                "details to a tooltip"
            ),
            auto_fixable=False,
        )
    )
    # Score penalised proportionally.
    penalty = min(60, over * 8)
    return max(0.0, 100.0 - penalty)


def _check_narrative(ctx: PageContext, findings: list[UxFinding]) -> float:
    """Score 0-100 for narrative ordering (KPI/trend before detail tables)."""
    if len(ctx.visuals) < 2:
        return 100.0
    # Find the y-coordinate (vertical order in PBIR; smaller y = higher) of
    # the first KPI vs the first table.
    first_kpi_y: float | None = None
    first_table_y: float | None = None
    for v in ctx.visuals:
        vtype = v.get("visual", {}).get("$type", "")
        y = float(v.get("y", 0))
        if vtype in _NARRATIVE_TOP and first_kpi_y is None:
            first_kpi_y = y
        if vtype in _NARRATIVE_DETAIL and first_table_y is None:
            first_table_y = y

    if first_table_y is None or first_kpi_y is None:
        # No mixed KPI + detail on this page; nothing to audit.
        return 100.0
    if first_kpi_y < first_table_y:
        return 100.0

    findings.append(
        UxFinding(
            page_name=ctx.page_name,
            category="narrative",
            severity="warning",
            location="page layout",
            message=(
                f"Detail table (y={int(first_table_y)}) appears above KPI "
                f"(y={int(first_kpi_y)}); broken narrative flow"
            ),
            suggestion=(
                "Move KPIs/cards to the top of the page; tables/matrices "
                "go below summaries"
            ),
            auto_fixable=False,
        )
    )
    return 60.0


def _check_mobile(ctx: PageContext, findings: list[UxFinding]) -> float:
    """Score 0-100 for mobile-readiness."""
    # mobileLayout is an optional PBIR property; we treat its presence as
    # the heuristic indicator.
    # (We don't actually load mobileLayout here; we infer from visual count
    #  + audience.)
    score = 100.0
    if ctx.audience == "executive" and len(ctx.visuals) > _MAX_VISUALS_FOR_MOBILE:
        findings.append(
            UxFinding(
                page_name=ctx.page_name,
                category="mobile",
                severity="warning",
                location=f"{len(ctx.visuals)} visuals",
                message=(
                    f"Page has {len(ctx.visuals)} visuals; mobile rendering "
                    "will be cramped (>4 stacked)"
                ),
                suggestion=(
                    "Reduce to <=4 visuals OR enable mobileLayout with proper gaps"
                ),
                auto_fixable=True,
            )
        )
        score = 70.0
    return score


def _check_cohesion(
    ctx: PageContext, audience: str, findings: list[UxFinding]
) -> float:
    """Score 0-100 for visual-style cohesion."""
    vtypes = [
        v.get("visual", {}).get("$type", "unknown") for v in ctx.visuals
    ]
    unknown_count = sum(1 for v in vtypes if v == "unknown")
    style_count = sum(
        1 for v in vtypes if any(v in s for s in _VISUAL_STYLE_BUCKETS.values())
    )
    if not vtypes:
        return 100.0
    if unknown_count > 0:
        findings.append(
            UxFinding(
                page_name=ctx.page_name,
                category="cohesion",
                severity="info",
                location=f"{unknown_count} visuals",
                message=(
                    f"{unknown_count} visual(s) use an unknown $type; "
                    "cohesion check incomplete"
                ),
                suggestion=(
                    "Use native visuals from the viz/visual_registry catalog "
                    "where possible"
                ),
                auto_fixable=False,
            )
        )
    if style_count > 5 and audience == "executive":
        findings.append(
            UxFinding(
                page_name=ctx.page_name,
                category="cohesion",
                severity="warning",
                location="page layout",
                message=(
                    f"{style_count} different visual styles on one page "
                    "(executive audience prefers <=4)"
                ),
                suggestion="Consolidate to a smaller set of visual styles",
                auto_fixable=False,
            )
        )
        return 70.0
    return 100.0


def _check_pie_categories(ctx: PageContext, findings: list[UxFinding]) -> None:
    """Detect pie charts with >7 slices (heuristic: looks at field count)."""
    # Heuristic: if a visual is pieChart/donutChart AND has many field
    # projections, flag it. Without semantic eval, this is a coarse signal.
    for v in ctx.visuals:
        vtype = v.get("visual", {}).get("$type", "")
        if vtype not in {"pieChart", "donutChart"}:
            continue
        projections = (
            v.get("visual", {}).get("projections", {}) or {}
        )
        category_count = sum(
            len(p) if isinstance(p, list) else 1
            for p in projections.values()
        )
        if category_count > 7:
            findings.append(
                UxFinding(
                    page_name=ctx.page_name,
                    category="pie_size",
                    severity="warning",
                    location=v.get("id", "?"),
                    message=(
                        f"{vtype} has {category_count} slices; >7 is hard to read"
                    ),
                    suggestion="Use a bar chart or filter to top-N categories",
                    auto_fixable=True,
                )
            )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def audit_report_ux_and_storytelling(
    pbip_path: str,
    page_name: str | None = None,
    audience_assumed: str | None = None,
    strictness: str = "standard",
) -> AuditReportUxAndStorytellingResult:
    """Run qualitative heuristics on all (or one) page(s) of a PBIP."""
    pbip = Path(pbip_path)
    if not pbip.exists():
        return AuditReportUxAndStorytellingResult(
            overall_score=0.0,
            meets_target=False,
            findings=[],
            pages_analyzed=0,
            warnings=[f"PBIP path does not exist: {pbip_path}"],
        )

    contexts = _load_pages(pbip, page_name)
    if not contexts:
        return AuditReportUxAndStorytellingResult(
            overall_score=100.0,
            meets_target=True,
            findings=[],
            pages_analyzed=0,
            warnings=["no page.json files found; nothing to audit"],
        )

    # Determine audience for the whole report (either explicit or inferred
    # from the first page name; consistent within a report).
    first_name = contexts[0].page_name
    inferred_audience = audience_assumed or _infer_audience(first_name)
    for c in contexts:
        c.audience = audience_assumed or _infer_audience(c.page_name) or inferred_audience
        c.strictness = strictness

    all_findings: list[UxFinding] = []
    category_sums: dict[str, list[float]] = {
        "hierarchy": [],
        "density": [],
        "narrative": [],
        "mobile": [],
        "cohesion": [],
    }

    for ctx in contexts:
        category_sums["hierarchy"].append(_check_hierarchy(ctx, all_findings))
        category_sums["density"].append(_check_density(ctx, all_findings))
        category_sums["narrative"].append(_check_narrative(ctx, all_findings))
        category_sums["mobile"].append(_check_mobile(ctx, all_findings))
        category_sums["cohesion"].append(
            _check_cohesion(ctx, ctx.audience, all_findings)
        )
        _check_pie_categories(ctx, all_findings)

    def avg(xs: list[float]) -> float:
        return round(sum(xs) / len(xs), 1) if xs else 100.0

    category_scores = {k: avg(v) for k, v in category_sums.items()}
    # Narrative score derived from category_scores["narrative"].
    narrative_score = category_scores["narrative"]
    overall = round(
        sum(category_scores.values()) / len(category_scores), 1
    )

    meets_target = overall >= 70.0

    return AuditReportUxAndStorytellingResult(
        overall_score=overall,
        category_scores=category_scores,
        narrative_score=narrative_score,
        meets_target=meets_target,
        findings=all_findings,
        pages_analyzed=len(contexts),
        audience_inferred=inferred_audience,
        warnings=[],
    )


__all__ = [
    "AuditReportUxAndStorytelling",
    "AuditReportUxAndStorytellingResult",
    "UxFinding",
    "audit_report_ux_and_storytelling",
]
