"""abbreviate_names tool — the "abreviar" half of the model refactoring.

Finds the measures and columns whose names have grown too long to
read, and proposes a shorter one for each.

It does not rename anything. The proposal comes back as a rename map,
which is what ``plan_change(intent="safe_rename")`` takes — so the
rename goes through the same path as every other rename: a plan, a
review, and a rollback if it goes wrong.

Two things it refuses to do:

- It does not truncate a name. A name that cannot be shortened without
  changing what it says is reported as needing a name by hand, because
  ``Total Revenue Amt Incl Tax 2`` says less than the original and
  looks like a mistake.
- It does not propose a name that collides with one that already
  exists. A proposal that collides is adjusted, and says so.
"""

from __future__ import annotations

from typing import Any

from powerbi_orchestrator_mcp.validation.model_metadata import (
    MetadataError,
    gather_pbip_metadata,
)
from powerbi_orchestrator_mcp.validation.name_abbreviator import (
    propose_shorter_names,
)

__all__ = ["abbreviate_names"]


def abbreviate_names(
    pbip_path: str,
    kind: str = "both",
    max_length: int = 40,
    abbreviate_words: bool = True,
    drop_filler_words: bool = True,
) -> dict[str, Any]:
    """Propose shorter names for the measures and columns that need them.

    Args:
        pbip_path: Path to the root .pbip directory.
        kind: ``measures``, ``columns`` or ``both``.
        max_length: Above this length, a name is a candidate.
        abbreviate_words: Shorten the words that have a standard
            abbreviation.
        drop_filler_words: Drop the words that carry no meaning.

    Returns:
        Dict with the proposals and a rename map ready for
        ``plan_change(intent="safe_rename")``.
    """
    if kind not in ("measures", "columns", "both"):
        return {
            "success": False,
            "error_message": (f"kind must be 'measures', 'columns' or 'both', got {kind!r}"),
        }
    if max_length < 8:
        return {
            "success": False,
            "error_message": f"max_length must be at least 8, got {max_length}",
        }

    try:
        meta = gather_pbip_metadata(pbip_path)
    except MetadataError as exc:
        return {
            "success": False,
            "error_message": str(exc),
        }

    proposals: list[dict[str, Any]] = []
    rename_map: dict[str, str] = {}

    measure_names = set(meta.measures)
    column_names: set[str] = set()
    for columns in meta.columns.values():
        for column in columns:
            column_names.add(str(column["name"]))

    if kind in ("columns", "both"):
        for table, columns in meta.columns.items():
            column_proposals = propose_shorter_names(
                {str(column["name"]): str(column["name"]) for column in columns},
                kind="column",
                table=table,
                max_length=max_length,
                abbreviate_words=abbreviate_words,
                drop_filler_words=drop_filler_words,
                # Every name in the model is taken, not just the ones
                # of this table.
                taken=column_names | measure_names,
            )
            proposals.extend(proposal.to_dict() for proposal in column_proposals)

    if kind in ("measures", "both"):
        measure_proposals = propose_shorter_names(
            {name: name for name in meta.measures},
            kind="measure",
            max_length=max_length,
            abbreviate_words=abbreviate_words,
            drop_filler_words=drop_filler_words,
            taken=measure_names | column_names,
        )
        proposals.extend(proposal.to_dict() for proposal in measure_proposals)

    for proposal in proposals:
        if proposal["proposed"] and proposal["proposed"] != proposal["name"]:
            rename_map[proposal["name"]] = proposal["proposed"]

    candidates = [p for p in proposals if p["proposed"]]
    needs_a_name = [p for p in proposals if p["needs_a_name_by_hand"]]

    return {
        "success": True,
        "kind": kind,
        "max_length": max_length,
        "names_examined": len(proposals),
        "names_with_a_proposal": len(candidates),
        "names_needing_a_name_by_hand": len(needs_a_name),
        "proposals": proposals,
        "rename_map": rename_map,
        "error_message": None,
    }
