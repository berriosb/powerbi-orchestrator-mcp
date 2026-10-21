"""simplify_dax_measure tool — the "simplificar" half of the model refactoring.

Reads a DAX expression, or a measure in a PBIP project, and returns an
equivalent expression that is written the short way.

It does not write anything. The rewritten expression comes back with the
list of rewrites that produced it, and the caller decides what to do
with it — by writing the measure through
``add_measure_with_validation``, or by applying a plan.

Two things it refuses to do:

- It does not claim the rewritten expression is equivalent in the
  mathematical sense. The expression is never evaluated against data,
  so the result says the equivalence is *structural*: the rewrite
  changes the shape and not the meaning, and a reader can check it.
- It does not touch an expression it could not parse. An expression
  beyond what the parser understands comes back unchanged, with the
  reason.
"""

from __future__ import annotations

from typing import Any

from powerbi_orchestrator_mcp.validation.dax_linter import DaxLinter
from powerbi_orchestrator_mcp.validation.dax_simplifier import simplify_expression
from powerbi_orchestrator_mcp.validation.model_metadata import (
    MetadataError,
    gather_pbip_metadata,
)

__all__ = ["simplify_dax_measure"]


def _lint(expression: str) -> list[dict[str, Any]]:
    return [
        {
            "rule_id": finding.rule_id,
            "severity": finding.severity,
            "message": finding.message,
            "rewrite_suggestion": finding.rewrite_suggestion,
        }
        for finding in DaxLinter().lint(expression)
    ]


def _simplify_one(expression: str, *, apply_lint: bool) -> dict[str, Any]:
    """Simplify one expression and describe what happened."""
    result = simplify_expression(expression)
    payload: dict[str, Any] = {
        "original_expression": result["original_expression"],
        "simplified_expression": result["simplified_expression"],
        "rewrites": result["rewrites"],
        "changed": result["changed"],
        "equivalence": result["equivalence"],
        "error_message": result["error_message"],
    }
    if apply_lint:
        # The linter verdict before and after: a rewrite that silences
        # a finding is worth knowing about, and so is one that creates
        # one.
        payload["lint_before"] = _lint(result["original_expression"])
        payload["lint_after"] = _lint(result["simplified_expression"])
    return payload


def simplify_dax_measure(
    expression: str | None = None,
    pbip_path: str | None = None,
    measure: str | None = None,
    all_measures: bool = False,
    apply_lint: bool = True,
) -> dict[str, Any]:
    """Simplify a DAX expression, a measure, or every measure in a project.

    Args:
        expression: A DAX expression to simplify. Mutually exclusive
            with ``pbip_path``.
        pbip_path: A PBIP project to read measures from.
        measure: The measure to simplify. Requires ``pbip_path``.
        all_measures: Simplify every measure in the project and report
            which ones could be written shorter. Requires
            ``pbip_path``.
        apply_lint: Include the linter verdict before and after.

    Returns:
        A dict with the simplified expression, the rewrites that
        produced it, and — in project mode — one entry per measure.
    """
    if expression and pbip_path:
        return {
            "success": False,
            "mode": "none",
            "error_message": (
                "give either an expression or a pbip_path, not both: the "
                "expression is simplified as written, and the project is "
                "read from disk"
            ),
        }

    if expression:
        payload = _simplify_one(expression, apply_lint=apply_lint)
        return {
            "success": payload["error_message"] is None,
            "mode": "expression",
            **payload,
        }

    if not pbip_path:
        return {
            "success": False,
            "mode": "none",
            "error_message": "give an expression to simplify, or a pbip_path to read from",
        }

    if not measure and not all_measures:
        return {
            "success": False,
            "mode": "none",
            "error_message": (
                "give the name of the measure to simplify, or set "
                "all_measures=True to look at every measure in the project"
            ),
        }

    try:
        meta = gather_pbip_metadata(pbip_path)
    except MetadataError as exc:
        return {
            "success": False,
            "mode": "none",
            "error_message": str(exc),
        }

    if measure:
        if measure not in meta.measures:
            return {
                "success": False,
                "mode": "measure",
                "error_message": (
                    f"the project has no measure named {measure!r}. "
                    f"It has: {', '.join(sorted(meta.measures)) or 'none'}"
                ),
                "available_measures": sorted(meta.measures),
            }
        payload = _simplify_one(meta.measures[measure], apply_lint=apply_lint)
        return {
            "success": payload["error_message"] is None,
            "mode": "measure",
            "measure": measure,
            "table": meta.measure_tables.get(measure),
            **payload,
        }

    # Every measure in the project.
    simplified_measures: list[dict[str, Any]] = []
    changed = 0
    for name, measure_expression in sorted(meta.measures.items()):
        payload = _simplify_one(measure_expression, apply_lint=apply_lint)
        if payload["changed"]:
            changed += 1
        simplified_measures.append(
            {
                "measure": name,
                "table": meta.measure_tables.get(name),
                **payload,
            }
        )

    return {
        "success": True,
        "mode": "project",
        "measures_examined": len(simplified_measures),
        "measures_simplifiable": changed,
        "measures": simplified_measures,
        "equivalence": "structural",
        "error_message": None,
    }
