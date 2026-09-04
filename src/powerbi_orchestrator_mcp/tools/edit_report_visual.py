"""edit_report_visual tool — v1.1 (SPEC §6.3).

Deterministic CRUD on a single visualContainer in a PBIR page. Field-level
merge: only the fields in the input change; unspecified fields are
preserved. Atomic write via temp-then-rename to prevent partial updates.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class EditReportVisual(BaseModel):
    """Input schema for ``edit_report_visual`` (SPEC §6.3 #3).

    All change parameters are optional — only the ones provided are
    applied. Field-level merge preserves unspecified properties.
    """

    pbip_path: str
    page_name: str
    visual_id: str
    type: str | None = None
    fields_json: str | None = None  # JSON of fields to merge (e.g. {"Values": ["[Sales]"]})
    format_json: str | None = None  # JSON of format/objects
    position_json: str | None = None  # JSON of {x, y, width, height}
    alt_text: str | None = None
    is_hidden: bool | None = None


def edit_report_visual(
    pbip_path: str,
    page_name: str,
    visual_id: str,
    *,
    type: str | None = None,
    fields_json: str | None = None,
    format_json: str | None = None,
    position_json: str | None = None,
    alt_text: str | None = None,
    is_hidden: bool | None = None,
) -> dict[str, Any]:
    """Apply field-level changes to a single visualContainer.

    Returns success=False with error_message if:
    - PBIP path / report dir / page.json doesn't exist.
    - visual_id is not found in the page.
    - Any *_json argument is malformed JSON.

    Atomic write: serialize the modified page.json to a temp file in
    the same directory, then `os.replace()` it over the original.
    """
    pbip_root = Path(pbip_path)
    if not pbip_root.exists():
        return {
            "success": False,
            "visual_id": visual_id,
            "changes_applied": [],
            "page_path": None,
            "rollback_handle": None,
            "error_message": f"PBIP path does not exist: {pbip_path}",
        }

    # Locate report dir.
    report_dirs = sorted(pbip_root.glob("*.Report"))
    if not report_dirs:
        return {
            "success": False,
            "visual_id": visual_id,
            "changes_applied": [],
            "page_path": None,
            "rollback_handle": None,
            "error_message": f"no .Report directory under {pbip_path}",
        }
    page_json = report_dirs[0] / "pages" / page_name / "page.json"
    if not page_json.exists():
        return {
            "success": False,
            "visual_id": visual_id,
            "changes_applied": [],
            "page_path": str(page_json),
            "rollback_handle": None,
            "error_message": f"page {page_name!r} does not exist",
        }

    try:
        data = json.loads(page_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "success": False,
            "visual_id": visual_id,
            "changes_applied": [],
            "page_path": str(page_json),
            "rollback_handle": None,
            "error_message": f"page.json is not valid JSON: {exc}",
        }

    # Locate visualContainer.
    containers = data.get("visualContainers", [])
    target_vc = None
    for vc in containers:
        if vc.get("id") == visual_id:
            target_vc = vc
            break
    if target_vc is None:
        return {
            "success": False,
            "visual_id": visual_id,
            "changes_applied": [],
            "page_path": str(page_json),
            "rollback_handle": None,
            "error_message": (
                f"visual {visual_id!r} not found on page {page_name!r}"
            ),
        }

    # Parse JSON inputs.
    try:
        fields = json.loads(fields_json) if fields_json is not None else None
    except json.JSONDecodeError as exc:
        return {
            "success": False,
            "visual_id": visual_id,
            "changes_applied": [],
            "page_path": str(page_json),
            "rollback_handle": None,
            "error_message": f"fields_json is not valid JSON: {exc}",
        }
    try:
        format_obj = json.loads(format_json) if format_json is not None else None
    except json.JSONDecodeError as exc:
        return {
            "success": False,
            "visual_id": visual_id,
            "changes_applied": [],
            "page_path": str(page_json),
            "rollback_handle": None,
            "error_message": f"format_json is not valid JSON: {exc}",
        }
    try:
        position = json.loads(position_json) if position_json is not None else None
    except json.JSONDecodeError as exc:
        return {
            "success": False,
            "visual_id": visual_id,
            "changes_applied": [],
            "page_path": str(page_json),
            "rollback_handle": None,
            "error_message": f"position_json is not valid JSON: {exc}",
        }

    # Field-level merge.
    changes_applied: list[str] = []
    if type is not None:
        visual_obj = target_vc.setdefault("visual", {})
        old_type = visual_obj.get("$type")
        visual_obj["$type"] = type
        changes_applied.append("type")
        _ = old_type  # noqa: F841
    if fields is not None:
        visual_obj = target_vc.setdefault("visual", {})
        proj = visual_obj.setdefault("projections", {})
        proj.update(fields)
        changes_applied.append("fields")
    if format_obj is not None:
        visual_obj = target_vc.setdefault("visual", {})
        visual_obj["objects"] = format_obj
        changes_applied.append("format")
    if position is not None:
        for k, v in position.items():
            target_vc[k] = v
        changes_applied.append("position")
    if alt_text is not None:
        target_vc["altText"] = alt_text
        changes_applied.append("altText")
    if is_hidden is not None:
        target_vc["isHidden"] = is_hidden
        changes_applied.append("isHidden")

    if not changes_applied:
        return {
            "success": False,
            "visual_id": visual_id,
            "changes_applied": [],
            "page_path": str(page_json),
            "rollback_handle": None,
            "error_message": "no changes provided",
        }

    # Atomic write: serialize to temp file in same dir, rename.
    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(page_json.parent), prefix=".page.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path_str, page_json)
    except Exception as exc:  # noqa: BLE001
        # Clean up tmp file on failure.
        with contextlib.suppress(OSError):
            os.unlink(tmp_path_str)
        return {
            "success": False,
            "visual_id": visual_id,
            "changes_applied": changes_applied,
            "page_path": str(page_json),
            "rollback_handle": None,
            "error_message": f"atomic write failed: {exc}",
        }

    return {
        "success": True,
        "visual_id": visual_id,
        "changes_applied": changes_applied,
        "page_path": str(page_json),
        "rollback_handle": None,
        "error_message": None,
    }
