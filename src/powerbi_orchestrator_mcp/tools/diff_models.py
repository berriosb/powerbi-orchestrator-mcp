"""diff_models tool — wraps ModelDiffer (SPEC §6.1 tool #9)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from powerbi_orchestrator_mcp.validation.model_diff import (
    ModelDiffer,
    ModelDiffResult,
)
from powerbi_orchestrator_mcp.validation.model_metadata import (
    MetadataError,
    pbip_inspector,
)


class DiffModels(BaseModel):
    """Input schema for ``diff_models`` tool (SPEC §6.1 #9)."""

    before: str
    after: str


class ModelDiffUnavailableError(RuntimeError):
    """Raised when one of the two models could not be inspected.

    Reporting "0 changes" for a model that could not be read is a
    false green: it reads as "these two models are identical".
    """


def diff_models(
    before: str,
    after: str,
    *,
    inspector: Any = None,
) -> ModelDiffResult:
    """Diff two semantic models (PBIP folders or snapshots).

    Without an explicit ``inspector`` the PBIP folders are read from
    disk, which is the only path an MCP client can take: a callable
    cannot be passed over the wire.

    Returns ModelDiffResult with breaking/non-breaking/added/removed
    counts plus the full diff list.

    Raises:
        ModelDiffUnavailableError: one of the two models could not be
            inspected.
    """
    if inspector is None:
        inspector = _pbip_inspector

    differ = ModelDiffer(inspector)
    return differ.diff(before, after)


def _pbip_inspector(target_ref: str) -> dict[str, Any]:
    """Inspect a PBIP folder, turning a read failure into a clear error."""
    try:
        return pbip_inspector(target_ref)
    except MetadataError as exc:
        raise ModelDiffUnavailableError(str(exc)) from exc


__all__ = ["DiffModels", "ModelDiffUnavailableError", "diff_models"]
