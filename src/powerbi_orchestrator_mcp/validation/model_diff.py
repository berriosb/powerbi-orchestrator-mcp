"""Model diffing — compare two semantic models (TMDL or PBIP).

Implements ``specs/03-validation.md`` §2.4 (Pydantic models for the
``diff_models`` tool).

MVP scope:
- Compare PBIP folder A vs B.
- Classify each diff as breaking (changes the consumer-facing API) vs
  non-breaking (cosmetic or additive).
- Report summary: counts of breaking, non-breaking, added, removed.
- No deep TMDL parsing — use the existing modeling engine for that
  (passes through ``list_tables`` / ``list_measures`` etc.).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DiffSeverity(str, Enum):  # noqa: UP042
    """Severity classification per spec §2.4."""

    BREAKING = "breaking"
    NON_BREAKING = "non_breaking"
    ADDED = "added"
    REMOVED = "removed"


class ObjectDiff(BaseModel):
    """Diff for a single object (table, column, measure, relationship)."""

    object_type: str  # table | column | measure | relationship
    object_name: str
    severity: DiffSeverity
    details: str = ""
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


class ModelDiffResult(BaseModel):
    """Top-level diff result."""

    before_path: str
    after_path: str
    total_changes: int
    breaking_changes: int
    non_breaking_changes: int
    added_objects: int
    removed_objects: int
    diffs: list[ObjectDiff] = Field(default_factory=list)


class ModelDiffer:
    """Compute a ModelDiffResult between two models.

    The actual model introspection is delegated to the injected
    ``inspector`` callable (in production, wraps ``ModelingEngine``).
    For MVP, the inspector returns:
    ``{"tables": [...], "columns": {table: [...]}, "measures": [...],
        "relationships": [...]}``
    """

    # Properties whose change is breaking (per spec §2.4 table).
    BREAKING_PROPS = {
        "table": {"name"},  # rename = breaking
        "column": {"name", "data_type"},  # rename or type change = breaking
        "measure": {"name", "expression"},  # rename or expression = breaking
        "relationship": {
            "from_table", "from_column", "to_table", "to_column",
            "cardinality", "cross_filter",
        },
    }

    def __init__(self, inspector: Any) -> None:
        """``inspector(target_ref) -> dict[str, list]`` per docstring."""
        self._inspector = inspector

    def diff(self, before_ref: str, after_ref: str) -> ModelDiffResult:
        before = self._inspector(before_ref)
        after = self._inspector(after_ref)
        diffs: list[ObjectDiff] = []
        diffs.extend(self._diff_lists(before, after, "tables"))
        diffs.extend(self._diff_measures(before, after))
        diffs.extend(self._diff_relationships(before, after))
        breaking = sum(1 for d in diffs if d.severity == DiffSeverity.BREAKING)
        non_breaking = sum(
            1 for d in diffs if d.severity == DiffSeverity.NON_BREAKING
        )
        added = sum(1 for d in diffs if d.severity == DiffSeverity.ADDED)
        removed = sum(1 for d in diffs if d.severity == DiffSeverity.REMOVED)
        return ModelDiffResult(
            before_path=before_ref,
            after_path=after_ref,
            total_changes=len(diffs),
            breaking_changes=breaking,
            non_breaking_changes=non_breaking,
            added_objects=added,
            removed_objects=removed,
            diffs=diffs,
        )

    # ------------------------------------------------------------------
    # Internal diffing helpers
    # ------------------------------------------------------------------

    def _diff_lists(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
        key: str,
    ) -> list[ObjectDiff]:
        """Generic diff for lists of dicts keyed by ``name``."""
        diffs: list[ObjectDiff] = []
        b = {x["name"]: x for x in before.get(key, [])}
        a = {x["name"]: x for x in after.get(key, [])}
        for name in b.keys() - a.keys():
            diffs.append(
                ObjectDiff(
                    object_type=key.rstrip("s"),
                    object_name=name,
                    severity=DiffSeverity.REMOVED,
                    details=f"{key[:-1]} {name!r} was removed",
                    before=b[name],
                    after=None,
                )
            )
        for name in a.keys() - b.keys():
            diffs.append(
                ObjectDiff(
                    object_type=key.rstrip("s"),
                    object_name=name,
                    severity=DiffSeverity.ADDED,
                    details=f"{key[:-1]} {name!r} was added",
                    before=None,
                    after=a[name],
                )
            )
        for name in a.keys() & b.keys():
            changes = _diff_dicts(b[name], a[name], self.BREAKING_PROPS.get(key.rstrip("s"), set()))
            if changes:
                breaking = any(c[3] for c in changes)
                diffs.append(
                    ObjectDiff(
                        object_type=key.rstrip("s"),
                        object_name=name,
                        severity=(
                            DiffSeverity.BREAKING if breaking
                            else DiffSeverity.NON_BREAKING
                        ),
                        details="; ".join(f"{k}: {b_v!r} → {a_v!r}" for k, b_v, a_v, _ in changes),
                        before=b[name],
                        after=a[name],
                    )
                )
        return diffs

    def _diff_measures(
        self, before: dict[str, Any], after: dict[str, Any]
    ) -> list[ObjectDiff]:
        return self._diff_lists(before, after, "measures")

    def _diff_relationships(
        self, before: dict[str, Any], after: dict[str, Any]
    ) -> list[ObjectDiff]:
        """Relationships keyed by composite (from_table, from_column, to_table, to_column)."""
        diffs: list[ObjectDiff] = []
        b = {_rel_key(r): r for r in before.get("relationships", [])}
        a = {_rel_key(r): r for r in after.get("relationships", [])}
        for k in b.keys() - a.keys():
            diffs.append(
                ObjectDiff(
                    object_type="relationship",
                    object_name=" → ".join(k),
                    severity=DiffSeverity.REMOVED,
                    before=b[k],
                    after=None,
                )
            )
        for k in a.keys() - b.keys():
            diffs.append(
                ObjectDiff(
                    object_type="relationship",
                    object_name=" → ".join(k),
                    severity=DiffSeverity.ADDED,
                    before=None,
                    after=a[k],
                )
            )
        for k in a.keys() & b.keys():
            changes = _diff_dicts(
                b[k], a[k], self.BREAKING_PROPS["relationship"]
            )
            if changes:
                breaking = any(c[3] for c in changes)
                diffs.append(
                    ObjectDiff(
                        object_type="relationship",
                        object_name=" → ".join(k),
                        severity=(
                            DiffSeverity.BREAKING if breaking
                            else DiffSeverity.NON_BREAKING
                        ),
                        details="; ".join(
                            f"{kk}: {bv!r} → {av!r}" for kk, bv, av, _ in changes
                        ),
                        before=b[k],
                        after=a[k],
                    )
                )
        return diffs


def _rel_key(r: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        r["from_table"],
        r["from_column"],
        r["to_table"],
        r["to_column"],
    )


def _diff_dicts(
    before: dict[str, Any], after: dict[str, Any], breaking_keys: set[str]
) -> list[tuple[str, Any, Any, bool]]:
    """Return list of (key, before, after, is_breaking) for changed keys."""
    changes: list[tuple[str, Any, Any, bool]] = []
    for k in before.keys() | after.keys():
        if before.get(k) != after.get(k):
            breaking = k in breaking_keys
            changes.append((k, before.get(k), after.get(k), breaking))
    return changes


__all__ = [
    "DiffSeverity",
    "ModelDiffer",
    "ModelDiffResult",
    "ObjectDiff",
]
