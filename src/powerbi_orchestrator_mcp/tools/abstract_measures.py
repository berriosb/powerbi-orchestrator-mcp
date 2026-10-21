"""abstract_measures tool — the "abstraer" half of the model refactoring.

Finds the logic that is written more than once in a semantic model and
says what the duplication looks like: which measures share it, what
they have in common, and where exactly they differ.

It does not write anything, and it does not apply the abstraction. A
Tabular measure cannot take a parameter, so "one measure with an
argument" is not something that can be written down as a DAX
expression — the answer is a calculation group, a helper table, or a
rewrite of the measures in place, and which one is right depends on
the model. The tool reports the shape of the answer and leaves that
choice to the caller.

Two kinds of duplication are reported:

``identical``
    The expressions are the same. One measure can serve both, and the
    references to it have to be repointed.

``same_shape``
    The expressions differ only in their literals or their columns.
    The differing places are listed slot by slot.
"""

from __future__ import annotations

from typing import Any

from powerbi_orchestrator_mcp.validation.model_metadata import (
    MetadataError,
    gather_pbip_metadata,
)
from powerbi_orchestrator_mcp.validation.model_refactor import (
    DuplicationGroup,
    find_duplication,
)

__all__ = ["abstract_measures"]


def _suggestion(group: DuplicationGroup) -> str:
    """What this duplication looks like, in one sentence."""
    if group.kind == "identical":
        return (
            "these measures compute the same thing; one of them can serve "
            "the rest, and the references to the others have to be repointed"
        )
    differing = group.differing_slots
    if not differing:
        return "these measures have the same shape without differing in it"
    kinds = {slot.kind for slot in differing}
    if kinds == {"literal"}:
        return (
            "these measures differ only in a number, which is what a "
            "calculation group over that measure is for"
        )
    if kinds == {"column"}:
        return (
            "these measures differ only in the column they read, which is "
            "what a calculation group over that column is for"
        )
    return (
        "these measures differ in a number and in the column they read; a "
        "calculation group over that column usually covers both"
    )


def abstract_measures(
    pbip_path: str,
    table: str | None = None,
    min_group_size: int = 2,
) -> dict[str, Any]:
    """Find the logic that is written more than once in a semantic model.

    Args:
        pbip_path: Path to the root .pbip directory.
        table: Only look at the measures of this table.
        min_group_size: How many measures have to share a shape before
            it is reported. Two is the minimum.

    Returns:
        Dict with the groups found, what each one means, and the
        measures that could not be parsed.
    """
    if min_group_size < 2:
        return {
            "success": False,
            "error_message": f"min_group_size must be at least 2, got {min_group_size}",
        }

    try:
        meta = gather_pbip_metadata(pbip_path)
    except MetadataError as exc:
        return {
            "success": False,
            "error_message": str(exc),
        }

    measures = dict(meta.measures)
    tables = dict(meta.measure_tables)
    if table:
        measures = {
            name: expression for name, expression in measures.items() if tables.get(name) == table
        }
        if not measures:
            return {
                "success": False,
                "error_message": (
                    f"the project has no measure in the table {table!r}. "
                    f"It has tables: {', '.join(sorted(meta.tables)) or 'none'}"
                ),
            }

    result = find_duplication(measures, min_group_size=min_group_size)

    groups = []
    for group in result.groups:
        payload = group.to_dict()
        payload["tables"] = sorted({tables.get(name, "") for name in group.measures})
        payload["suggestion"] = _suggestion(group)
        groups.append(payload)

    return {
        "success": True,
        "table": table,
        "measures_examined": result.measures_examined,
        "measures_skipped": result.measures_skipped,
        "skipped": result.skipped,
        "groups": groups,
        "group_count": len(groups),
        "error_message": None,
    }
