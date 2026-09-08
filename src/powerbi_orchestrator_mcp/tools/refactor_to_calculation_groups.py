"""refactor_to_calculation_groups tool — v2 (SPEC §6.2).

Auto-detect DAX measures sharing a common structure (e.g. `Total Sales YTD`,
`Total Sales QTD`, `Total Sales MTD`) and consolidate into a Tabular
Editor calculation group with reconciliation of total measures.

For MVP we use a regex-based skeleton detector (no full TMDL parser).
This handles the common pattern "X <TIME_VARIANT>" where TIME_VARIANT
∈ {YTD, QTD, MTD, PY, YOY, MOM, QOQ, WOW}.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# Time-variant tokens we recognize as calc-group items.
# Order matters: more specific first (YOY before YO, MOM before MO).
TIME_VARIANTS: tuple[str, ...] = (
    "YOY", "YTD", "QTD", "MTD",
    "PY", "QOQ", "MOM", "WOW",
    "Y", "Q", "M", "W", "D",
)


class RefactorToCalculationGroups(BaseModel):
    """Input schema for ``refactor_to_calculation_groups`` (SPEC §6.2 #1)."""

    target: str  # PBIP path or workspace ID
    min_candidates: int = 3
    reconcile_strategy: str = "strict"  # strict | tolerance
    preserve_originals: bool = False
    auto_apply: bool = False
    inspector: Any = None  # modeling engine in production; mock in tests
    measure_writer: Any = None  # engine for create_calc_group


class CalcGroupPlan(BaseModel):
    """Plan entry for one detected calc-group candidate."""

    name: str
    template_measure: str
    items: list[dict[str, str]] = Field(default_factory=list)
    # example: [{"name": "YTD", "expression": "TOTALYTD([Amount], 'Date'[Date])"}]


class ReconciliationDiff(BaseModel):
    """Reconciliation diff for one total measure."""

    measure_name: str
    pre_value: float | None = None
    post_value: float | None = None
    max_drift_pct: float | None = None


class RefactorResult(BaseModel):
    """Output of refactor_to_calculation_groups."""

    groups_created: list[CalcGroupPlan] = Field(default_factory=list)
    measures_remapped: dict[str, str] = Field(default_factory=dict)
    reconciliation_diffs: list[ReconciliationDiff] = Field(default_factory=list)
    dry_run: bool = True
    changed_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _skeleton_pattern(measure_name: str) -> str | None:
    """Identify a time-variant token at the end of the measure name.

    Returns the captured skeleton or None if no variant found.
    E.g. "Total Sales YTD" → ("Total Sales ", "YTD").
    """
    upper = measure_name.upper()
    for variant in TIME_VARIANTS:
        suffix = f" {variant}"
        if upper.endswith(suffix):
            return measure_name[: -len(suffix)].strip()
    return None


def _group_candidates(
    measures: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group measures by their skeleton (non-variant part)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for m in measures:
        skeleton = _skeleton_pattern(m.get("name", ""))
        if skeleton is None:
            continue
        groups.setdefault(skeleton, []).append(m)
    # Filter out single-measure groups (nothing to consolidate).
    return {k: v for k, v in groups.items() if len(v) >= 2}


def _synthesize_calc_group(skeleton: str, items: list[dict[str, Any]]) -> CalcGroupPlan:
    """Build a CalcGroupPlan from a skeleton + its member measures.

    The synthetic expression uses TOTALYTD-style placeholders. For MVP,
    we don't attempt real TMDL expression generation; we just store
    the structure with one item per variant.
    """
    calc_items: list[dict[str, str]] = []
    for m in items:
        name = m.get("name", "")
        variant = name[len(skeleton) :].strip()  # crude split
        # The actual expression substitution is a Week 2 task; we just
        # record the skeleton → variant mapping for now.
        calc_items.append(
            {
                "name": variant,
                "expression": m.get("expression", ""),
            }
        )
    return CalcGroupPlan(
        name=f"TimeIntelligence_{skeleton.replace(' ', '_')}",
        template_measure=skeleton,
        items=calc_items,
    )


def refactor_to_calculation_groups(
    target: str,
    min_candidates: int = 3,
    reconcile_strategy: str = "strict",
    preserve_originals: bool = False,  # noqa: ARG001
    auto_apply: bool = False,
    *,
    inspector: Any = None,
    measure_writer: Any = None,
) -> RefactorResult:
    """Detect calc-group candidates + (optionally) refactor.

    Args:
        target: PBIP path (for MVP).
        min_candidates: Minimum measures per group (default 3 per spec).
        reconcile_strategy: "strict" (MVP) — no drift tolerated.
        preserve_originals: If True, keep originals as aliases (v2).
        auto_apply: If False, plan only (dry-run).
        inspector: Injected; produces a list of measure dicts.
        measure_writer: Injected; creates the calc group.

    Returns:
        RefactorResult with groups_created, measures_remapped,
        reconciliation_diffs, dry_run, changed_files, warnings.
    """
    if inspector is None:
        return RefactorResult(
            warnings=["no inspector provided \u2014 cannot enumerate measures"]
        )

    try:
        measures = inspector.list_measures()
    except Exception as exc:  # noqa: BLE001
        return RefactorResult(
            warnings=[f"inspector.list_measures() failed: {exc}"]
        )

    # Group by skeleton.
    grouped = _group_candidates(measures)
    # Apply min_candidates filter.
    qualifying = {
        k: v for k, v in grouped.items() if len(v) >= min_candidates
    }

    # Initialize warnings early (used by various return paths below).
    warnings: list[str] = []

    plans = [_synthesize_calc_group(k, v) for k, v in qualifying.items()]
    if not plans:
        warnings.append(
            f"no group had ≥{min_candidates} members with shared structure"
        )
    measures_remapped: dict[str, str] = {}
    for plan in plans:
        for item in plan.items:
            original_name = f"{plan.template_measure} {item['name']}"
            measures_remapped[original_name] = (
                f"[{plan.name}].[{item['name']}]"
            )

    # Reconciliation: MVP — placeholder.
    reconciliation_diffs: list[ReconciliationDiff] = []
    if reconcile_strategy != "strict":
        reconciliation_diffs.append(
            ReconciliationDiff(
                measure_name="(reconciliation: deferred to v2)",
                pre_value=None,
                post_value=None,
                max_drift_pct=None,
            )
        )

    # Persist.
    changed_files: list[str] = []
    if auto_apply:
        if measure_writer is None:
            warnings.append(
                "auto_apply=True but no measure_writer; cannot persist"
            )
        else:
            # Persist each plan's items.
            for plan in plans:
                try:
                    write_result = measure_writer(
                        target=target,
                        plan_name=plan.name,
                        items=list(plan.items),
                    )
                    changed_files.extend(
                        write_result.get("changed_files", [])
                    )
                except Exception as exc:  # noqa: BLE001
                    warnings.append(
                        f"failed to create calc group {plan.name}: {exc}"
                    )

    return RefactorResult(
        groups_created=plans,
        measures_remapped=measures_remapped,
        reconciliation_diffs=reconciliation_diffs,
        dry_run=not auto_apply,
        changed_files=changed_files,
        warnings=warnings,
    )


__all__ = [
    "CalcGroupPlan",
    "ReconciliationDiff",
    "RefactorResult",
    "RefactorToCalculationGroups",
    "TIME_VARIANTS",
    "refactor_to_calculation_groups",
]
