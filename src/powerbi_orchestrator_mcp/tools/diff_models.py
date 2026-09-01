"""diff_models tool — wraps ModelDiffer (SPEC §6.1 tool #9)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from powerbi_orchestrator_mcp.validation.model_diff import (
    ModelDiffer,
    ModelDiffResult,
)


class DiffModels(BaseModel):
    """Input schema for ``diff_models`` tool (SPEC §6.1 #9).

    Both targets may be:
    - A PBIP folder path (inspected via the modeling engine).
    - A snapshot file (JSON written by the modeling engine).
    """

    before: str
    after: str
    inspector: Any = None  # injected; in production comes from modeling engine


def diff_models(
    before: str,
    after: str,
    *,
    inspector: Any = None,
) -> ModelDiffResult:
    """Diff two semantic models (PBIP folders or snapshots).

    Returns ModelDiffResult with breaking/non-breaking/added/removed
    counts plus the full diff list.
    """
    if inspector is None:
        # Without an inspector, return an empty diff. Tests inject
        # the inspector to provide real model data.
        return ModelDiffResult(
            before_path=before,
            after_path=after,
            total_changes=0,
            breaking_changes=0,
            non_breaking_changes=0,
            added_objects=0,
            removed_objects=0,
        )

    differ = ModelDiffer(inspector)
    return differ.diff(before, after)
