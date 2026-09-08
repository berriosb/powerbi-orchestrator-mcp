"""select_visuals_for_kpis tool — v2 (SPEC §6.2).

Composes `viz.visual_suggester` for a list of KPIs and returns ranked
visual recommendations. Pure-Python (no subprocess); reads model schema
via the injected `inspector`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.viz.visual_suggester import (
    SuggesterInput,
    VisualSuggestion,
    suggest,
)


class SelectVisualsForKpis(BaseModel):
    """Input schema for ``select_visuals_for_kpis`` (SPEC §6.2 #2)."""

    kpis_json: str  # JSON list of KPIs; each: name, semantic_type, fields, audience
    audience: str = "executive"
    max_results: int = 3
    inspector: Any = None  # optional: provides cardinality via list_tables


class VisualRecommendation(BaseModel):
    """Single visual recommendation for one KPI."""

    kpi_name: str
    primary: VisualSuggestion
    alternatives: list[VisualSuggestion] = Field(default_factory=list)
    rationale: str = ""


class SelectVisualsResult(BaseModel):
    """Output of select_visuals_for_kpis."""

    recommendations: list[VisualRecommendation]
    coverage_pct: float  # % of KPIs that got at least 1 recommendation
    warnings: list[str] = Field(default_factory=list)


def _parse_kpis(kpis_json: str) -> list[SuggesterInput]:
    """Parse the JSON KPI list into SuggesterInput objects."""
    import json as _json

    raw = _json.loads(kpis_json)
    out: list[SuggesterInput] = []
    for k in raw:
        out.append(
            SuggesterInput(
                name=k.get("name", "?"),
                semantic_type=k.get("semantic_type", "single_value"),
                fields=k.get("fields", []),
                cardinality=k.get("cardinality"),
                has_time=k.get("has_time", False),
                audience=k.get("audience", "executive"),
            )
        )
    return out


def _infer_cardinality(inspector: Any, fields: list[str]) -> int | None:
    """Best-effort cardinality lookup against the inspector.

    For MVP: if the first field matches a table or column we can ask
    the inspector for cardinality. Otherwise return None (no penalty).
    """
    if inspector is None or not fields:
        return None
    try:
        for table in inspector.list_tables():
            cols = inspector.list_columns(table["name"])
            col_names = {c["name"] for c in cols}
            if any(f in col_names for f in fields):
                # Rough estimate: 10 if we can't count exactly.
                return 10
    except Exception:  # noqa: BLE001
        return None
    return None


def select_visuals_for_kpis(
    kpis_json: str,
    audience: str = "executive",  # noqa: ARG001
    max_results: int = 3,
    *,
    inspector: Any = None,
) -> SelectVisualsResult:
    """For each KPI in the JSON list, suggest a primary visual + alternatives."""
    try:
        inputs = _parse_kpis(kpis_json)
    except Exception as exc:  # noqa: BLE001
        return SelectVisualsResult(
            recommendations=[],
            coverage_pct=0.0,
            warnings=[f"failed to parse kpis_json: {exc}"],
        )

    if not inputs:
        return SelectVisualsResult(
            recommendations=[],
            coverage_pct=100.0,
            warnings=["kpis_json parsed to empty list"],
        )

    recommendations: list[VisualRecommendation] = []
    covered = 0
    warnings: list[str] = []
    for inp in inputs:
        # Inject cardinality from inspector if not specified in input.
        if inp.cardinality is None:
            inp.cardinality = _infer_cardinality(inspector, inp.fields)
        suggestions = suggest(inp, max_alternatives=max_results)
        if not suggestions:
            warnings.append(f"no suggestion for KPI {inp.name!r}")
            continue
        primary = suggestions[0]
        alternatives = suggestions[1:]
        recommendations.append(
            VisualRecommendation(
                kpi_name=inp.name,
                primary=primary,
                alternatives=alternatives,
                rationale=primary.justification,
            )
        )
        covered += 1

    coverage_pct = (covered / len(inputs)) * 100.0 if inputs else 100.0

    return SelectVisualsResult(
        recommendations=recommendations,
        coverage_pct=coverage_pct,
        warnings=warnings,
    )


__all__ = [
    "SelectVisualsForKpis",
    "SelectVisualsResult",
    "VisualRecommendation",
    "select_visuals_for_kpis",
]
