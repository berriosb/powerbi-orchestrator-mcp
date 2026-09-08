"""Visual suggester — recommend the best visual for a given KPI (Capa 5).

Implements [`specs/04-viz-ux.md`](../specs/04-viz-ux.md) §2.2.

Algorithm (deterministic, no ML):
1. Filter `VISUAL_REGISTRY` by semantic_type match.
2. Filter by cardinality fit (data shape).
3. Filter by supports_time if KPI is time-driven.
4. Penalize visuals with color_safe_default=False unless the user
   accepts them (NOT exposed in MVP).
5. Penalize visuals outside the audience preference (executive →
   simple visuals; analyst → detailed).
6. Score = (semantic_type match) + (cardinality fit) + (audience fit) -
   (anti-recommendations).
7. Sort by score descending. Top 1 = primary; top 2..N+1 = alternatives.

Anti-recommendations implemented:
- Pie / donut with >5 categories.
- Pie / donut with time data (use line chart instead).
- KPI / card with >1 measure (use multi-row card or detail).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from powerbi_orchestrator_mcp.viz.visual_registry import (
    VisualTypeEntry,
    entries_matching,
)


@dataclass
class VisualSuggestion:
    """Result of suggester for one KPI."""

    type: str
    justification: str
    expected_fields: list[str] = field(default_factory=list)
    sqlbi_reference: str | None = None
    color_safe: bool = True


@dataclass
class SuggesterInput:
    """Input to the suggester for a single KPI."""

    name: str
    semantic_type: str  # single_value | comparison | trend | composition | distribution | correlation | tabular
    fields: list[str] = field(default_factory=list)
    cardinality: int | None = None
    has_time: bool = False
    audience: str = "executive"  # executive | analyst | operational


# Anti-recommendation penalties (subtract from score).
_ANTI_REC_PENALTIES: dict[str, list[tuple[str, str]]] = {
    "donutChart": [
        ("card_high", "donut with >5 slices is hard to read"),
        ("with_time", "donut with time axis is meaningless"),
    ],
    "pieChart": [
        ("card_high", "pie with >5 slices is hard to read"),
        ("with_time", "pie with time axis is meaningless"),
    ],
    "kpi": [
        ("multi_measure", "KPI visual with >1 measure is confusing"),
    ],
}

# Audience preference: simpler visuals first for executives.
_AUDIENCE_PRIORITY_BUMP: dict[str, set[str]] = {
    "executive": {"card", "kpi", "lineChart"},
    "analyst": {"barChart", "scatterChart", "tableEx"},
    "operational": {"barChart", "tableEx", "lineChart"},
}


def suggest(input: SuggesterInput, max_alternatives: int = 3) -> list[VisualSuggestion]:
    """Return ranked VisualSuggestions (best first)."""
    candidates = entries_matching(semantic_type=input.semantic_type)
    if not candidates:
        # Fall back: try with relaxed matching.
        candidates = entries_matching()

    scored: list[tuple[float, VisualTypeEntry]] = []
    for entry in candidates:
        score = _score(entry, input)
        scored.append((score, entry))
    scored.sort(key=lambda x: (-x[0], x[1].priority_default))

    suggestions: list[VisualSuggestion] = []
    for score, entry in scored[: 1 + max_alternatives]:
        if score < 0:
            continue
        suggestion = VisualSuggestion(
            type=entry.type_id,
            justification=_build_justification(entry, input),
            expected_fields=_expected_fields_for(entry, input),
            sqlbi_reference=entry.sqlbi_reference,
            color_safe=entry.color_safe_default,
        )
        suggestions.append(suggestion)
    return suggestions


def _score(entry: VisualTypeEntry, input: SuggesterInput) -> float:
    score = 1.0
    # Cardinality fit.
    if input.cardinality is not None:
        lo, hi = entry.cardinality_range
        if lo <= input.cardinality <= hi:
            score += 1.0
        else:
            score -= 0.5
    # Time support.
    if input.has_time and not entry.supports_time:
        score -= 0.5
    # Audience preference bump.
    preferred: set[str] = _AUDIENCE_PRIORITY_BUMP.get(input.audience, set())
    if entry.type_id in preferred:
        score += 0.5
    # Anti-recommendations (penalties).
    score -= _anti_rec_penalty(entry, input)
    return score


def _anti_rec_penalty(entry: VisualTypeEntry, input: SuggesterInput) -> float:
    penalty = 0.0
    rules = _ANTI_REC_PENALTIES.get(entry.type_id, [])
    for rule_code, _msg in rules:
        if rule_code == "card_high" and (
            input.cardinality is not None and input.cardinality > 5
        ):
            penalty += 1.5
        elif rule_code == "with_time" and input.has_time:
            penalty += 2.0
        elif rule_code == "multi_measure" and len(input.fields) > 1:
            penalty += 0.5
    return penalty


def _build_justification(entry: VisualTypeEntry, input: SuggesterInput) -> str:
    parts = [entry.rationale]
    if input.cardinality is not None:
        lo, hi = entry.cardinality_range
        if lo <= input.cardinality <= hi:
            parts.append(
                f"Cardinality ({input.cardinality}) fits in range "
                f"({lo}-{hi})."
            )
        else:
            parts.append(
                f"Cardinality ({input.cardinality}) is outside the "
                f"recommended range ({lo}-{hi}); consider an alternative."
            )
    if input.audience:
        preferred: set[str] = _AUDIENCE_PRIORITY_BUMP.get(input.audience, set())
        if entry.type_id in preferred:
            parts.append(f"Good fit for {input.audience} audience.")
    if not entry.color_safe_default:
        parts.append(
            "⚠️ Default theme colors are NOT colorblind-safe; "
            "consider apply_theme_and_accessibility_rules first."
        )
    return " ".join(parts)


def _expected_fields_for(entry: VisualTypeEntry, input: SuggesterInput) -> list[str]:
    """Return a sensible list of fields the visual should project."""
    if not input.fields:
        return []
    if entry.category == "kpi" and input.fields:
        return [input.fields[0]]
    if entry.category == "trend" and len(input.fields) >= 1:
        return [input.fields[0]]  # time axis; visual handles measures
    if entry.category == "comparison" and len(input.fields) >= 1:
        return [input.fields[0]]
    if entry.category == "composition" and len(input.fields) >= 1:
        return [input.fields[0]]
    return input.fields[:2]


def suggest_many(
    inputs: list[SuggesterInput], max_alternatives: int = 3
) -> dict[str, list[VisualSuggestion]]:
    """Suggest visuals for many KPIs; returns {kpi_name: suggestions}."""
    return {
        inp.name: suggest(inp, max_alternatives=max_alternatives)
        for inp in inputs
    }


__all__ = [
    "SuggesterInput",
    "VisualSuggestion",
    "suggest",
    "suggest_many",
]
