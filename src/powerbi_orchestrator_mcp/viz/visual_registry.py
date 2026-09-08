"""Visual Registry — catalog of Power BI visual types (Capa 5).

Implements [`specs/04-viz-ux.md`](../specs/04-viz-ux.md) §2.1.

For MVP we ship a minimum viable catalog: the 8 most common Power BI
visual types with their data shapes, required roles, and color-safety
characteristics. This is what `select_visuals_for_kpis` and
`design_report_page_from_requirements` consume.

Design notes:
- VisualSpec is imported from `engines/base.py` (already Pydantic).
- The registry is a class-level constant; no DB lookup (unlike
  Microsoft's VisualRegistry which scans installed custom visuals).
- Categories mirror SQLBI's classification.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VisualTypeEntry:
    """One visual type in the catalog.

    Attributes:
        type_id: PBI type identifier (e.g. "card", "barChart").
        display_name: Human-readable.
        category: One of CATEGORIES.
        semantic_types: KPI semantic types this visual supports.
        cardinality_range: (min, max) cardinality it handles well.
        supports_time: Whether the visual natively plots time on an axis.
        color_safe_default: Whether the default theme colors are
            colorblind-safe (False for pie / donut where default red/green
            contrast fails WCAG).
        priority_default: Default rank within category (lower = higher).
        sqlbi_reference: SQLBI blog post or doc URL.
        rationale: 1-line description of when to use this visual.
    """

    type_id: str
    display_name: str
    category: str
    semantic_types: tuple[str, ...]
    cardinality_range: tuple[int, int]
    supports_time: bool
    color_safe_default: bool
    priority_default: int
    sqlbi_reference: str | None
    rationale: str


# Categories mirror SQLBI's classification.
CATEGORIES = (
    "kpi",
    "trend",
    "comparison",
    "composition",
    "distribution",
    "tabular",
    "geo",
    "decomposition",
)

# 8 most common native visuals per spec §2.1 minimum viable catalog.
VISUAL_REGISTRY: tuple[VisualTypeEntry, ...] = (
    VisualTypeEntry(
        type_id="card",
        display_name="Card",
        category="kpi",
        semantic_types=("single_value",),
        cardinality_range=(1, 1),
        supports_time=False,
        color_safe_default=True,
        priority_default=1,
        sqlbi_reference="https://www.sqlbi.com/articles/cards/",
        rationale="Single value with optional trend indicator; ideal for executive dashboards.",
    ),
    VisualTypeEntry(
        type_id="kpi",
        display_name="KPI",
        category="kpi",
        semantic_types=("single_value",),
        cardinality_range=(1, 1),
        supports_time=True,
        color_safe_default=True,
        priority_default=2,
        sqlbi_reference="https://learn.microsoft.com/en-us/power-bi/visuals/power-bi-visualization-kpi",
        rationale="Single value against a target with trend; best when a goal exists.",
    ),
    VisualTypeEntry(
        type_id="lineChart",
        display_name="Line chart",
        category="trend",
        semantic_types=("trend",),
        cardinality_range=(2, 365),
        supports_time=True,
        color_safe_default=True,
        priority_default=1,
        sqlbi_reference="https://www.sqlbi.com/articles/line-charts/",
        rationale="Continuous metric over time; the standard for trends.",
    ),
    VisualTypeEntry(
        type_id="barChart",
        display_name="Bar chart (clustered)",
        category="comparison",
        semantic_types=("comparison",),
        cardinality_range=(2, 20),
        supports_time=False,
        color_safe_default=True,
        priority_default=1,
        sqlbi_reference="https://www.sqlbi.com/articles/bar-charts/",
        rationale="Categorical comparison; most versatile comparison visual.",
    ),
    VisualTypeEntry(
        type_id="donutChart",
        display_name="Donut chart",
        category="composition",
        semantic_types=("composition",),
        cardinality_range=(2, 5),
        supports_time=False,
        color_safe_default=False,
        priority_default=3,
        sqlbi_reference="https://www.sqlbi.com/articles/donut-charts/",
        rationale="Show composition of a small whole; AVOID for >5 categories.",
    ),
    VisualTypeEntry(
        type_id="pieChart",
        display_name="Pie chart",
        category="composition",
        semantic_types=("composition",),
        cardinality_range=(2, 4),
        supports_time=False,
        color_safe_default=False,
        priority_default=4,
        sqlbi_reference=None,
        rationale="Last-resort composition visual; prefer bar/donut for accessibility.",
    ),
    VisualTypeEntry(
        type_id="scatterChart",
        display_name="Scatter chart",
        category="distribution",
        semantic_types=("correlation", "distribution"),
        cardinality_range=(20, 10000),
        supports_time=False,
        color_safe_default=True,
        priority_default=1,
        sqlbi_reference="https://www.sqlbi.com/articles/scatter-charts/",
        rationale="Two-metric correlation; shows clusters and outliers.",
    ),
    VisualTypeEntry(
        type_id="tableEx",
        display_name="Table",
        category="tabular",
        semantic_types=("tabular",),
        cardinality_range=(1, 1000),
        supports_time=False,
        color_safe_default=True,
        priority_default=1,
        sqlbi_reference=None,
        rationale="Exact values; the fallback when no other visual fits.",
    ),
)


def lookup(type_id: str) -> VisualTypeEntry | None:
    """Find a visual type entry by id. Returns None if not found."""
    for entry in VISUAL_REGISTRY:
        if entry.type_id == type_id:
            return entry
    return None


def entries_matching(
    *,
    semantic_type: str | None = None,
    cardinality: int | None = None,
    supports_time: bool | None = None,
) -> list[VisualTypeEntry]:
    """Filter registry by optional criteria. Empty result means no match."""
    out: list[VisualTypeEntry] = []
    for entry in VISUAL_REGISTRY:
        if semantic_type is not None and semantic_type not in entry.semantic_types:
            continue
        if cardinality is not None:
            lo, hi = entry.cardinality_range
            if not (lo <= cardinality <= hi):
                continue
        if supports_time is not None and entry.supports_time != supports_time:
            continue
        out.append(entry)
    return out


def all_entries() -> list[VisualTypeEntry]:
    """Return all catalog entries (snapshot)."""
    return list(VISUAL_REGISTRY)


def categories() -> tuple[str, ...]:
    """Return all categories defined in the catalog."""
    return CATEGORIES


__all__ = [
    "CATEGORIES",
    "VisualTypeEntry",
    "VISUAL_REGISTRY",
    "all_entries",
    "categories",
    "entries_matching",
    "lookup",
]
