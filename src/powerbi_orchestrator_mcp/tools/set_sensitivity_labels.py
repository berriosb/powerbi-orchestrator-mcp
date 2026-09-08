"""set_sensitivity_labels tool — v3 (SPEC §6.2; spec: specs/tools/set-sensitivity-labels.md).

Apply Microsoft Purview sensitivity labels to one or more Fabric items
in a single ``POST /admin/items/labels/bulkSet`` call. The tool wraps
the admin endpoint behind an injected ``fabric_admin_client`` and
emits an audit-log row for each item that was touched (or skipped
because already labeled).

For MVP the audit logger is just a callable; in production it wraps
the SQLite WAL from `orchestrator/audit.py`. Item names are
optionally redacted (replaced with SHA-256 prefix) for compliance.

The tool is gated by an explicit ``ADMIN`` scope check — see
``02-cloud-fabric.md` ADMIN gate. Without the gate present, the tool
refuses to invoke the admin endpoint and emits a remediation hint.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Scope gate
# ---------------------------------------------------------------------------

_REQUIRED_SCOPES: tuple[str, ...] = (
    "*.Admin.*",
    "InformationProtectionPolicy.Apply.All",
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class LabelTarget(BaseModel):
    """One item to label."""

    item_id: str
    item_name: str | None = None

    @field_validator("item_id")
    @classmethod
    def _validate_guid(cls, v: str) -> str:
        if not _GUID_RE.match(v):
            raise ValueError(
                f"item_id must be a GUID; got {v!r}"
            )
        return v


class SetSensitivityLabels(BaseModel):
    """Input schema."""

    tenant_id: str | None = None
    items: list[LabelTarget]
    label_id: str
    label_name: str
    admin_token: str | None = None  # explicit bearer token (test seam)
    fabric_admin_client: Any = None  # injected; sees admin_bulk_set_labels
    admin_scopes: list[str] = Field(default_factory=list)
    audit_logger: Any = None
    redact_names: bool = True
    dry_run: bool = True

    @field_validator("label_id")
    @classmethod
    def _validate_label_id(cls, v: str) -> str:
        if not _GUID_RE.match(v):
            raise ValueError(
                f"label_id must be a GUID; got {v!r}"
            )
        return v


class LabeledItem(BaseModel):
    """Per-item outcome."""

    item_id: str
    item_name_redacted: str | None = None
    item_name_raw: str | None = None
    status: str  # applied | skipped | failed
    reason: str = ""


class SetSensitivityLabelsResult(BaseModel):
    """Output."""

    applied: list[LabeledItem] = Field(default_factory=list)
    bulk_set_calls: int = 0
    audit_log_entries: list[str] = Field(default_factory=list)
    dry_run: bool = True
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _redact(name: str) -> str:
    """SHA-256 prefix (16 chars) — deterministic, non-reversible within tool scope."""
    return "sha256:" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]


def _check_admin_scope(scopes: list[str]) -> tuple[bool, str]:
    """Return (ok, reason). Either at least one wildcard-Admin scope, or
    the explicit Apply.All scope, must be present."""
    if not scopes:
        return False, "no admin_scopes provided"
    if any(s == "*.Admin.*" or s.endswith(".Admin.*") for s in scopes):
        return True, ""
    if "InformationProtectionPolicy.Apply.All" in scopes:
        return True, ""
    return False, (
        "missing required admin scope; need *.Admin.* or "
        "InformationProtectionPolicy.Apply.All"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def set_sensitivity_labels(
    items: list[LabelTarget] | list[dict[str, str]] | None = None,
    label_id: str = "",
    label_name: str = "",
    tenant_id: str | None = None,
    fabric_admin_client: Any = None,
    admin_scopes: list[str] | None = None,
    admin_token: str | None = None,
    audit_logger: Any = None,
    redact_names: bool = True,
    dry_run: bool = True,
) -> SetSensitivityLabelsResult:
    """Apply a sensitivity label to one or more Fabric items in bulk."""
    warnings: list[str] = []
    applied: list[LabeledItem] = []
    audit_entries: list[str] = []
    bulk_set_calls = 0

    # Normalise input.
    if items is None or not items:
        return SetSensitivityLabelsResult(
            dry_run=dry_run,
            warnings=["no items to label"],
        )
    if not label_id or not label_name:
        return SetSensitivityLabelsResult(
            dry_run=dry_run,
            warnings=["label_id and label_name are required"],
        )

    # Re-validate label_id is a GUID; raises early.
    if not _GUID_RE.match(label_id):
        return SetSensitivityLabelsResult(
            dry_run=dry_run,
            warnings=[f"label_id must be a GUID; got {label_id!r}"],
        )

    normalised: list[LabelTarget] = []
    for it in items:
        if isinstance(it, LabelTarget):
            normalised.append(it)
        elif isinstance(it, dict):
            normalised.append(
                LabelTarget(item_id=it["item_id"], item_name=it.get("item_name"))
            )
        else:
            return SetSensitivityLabelsResult(
                dry_run=dry_run,
                warnings=[f"unsupported item entry: {it!r}"],
            )

    # Validate item IDs.
    bad = [
        it for it in normalised if not _GUID_RE.match(it.item_id)
    ]
    if bad:
        return SetSensitivityLabelsResult(
            dry_run=dry_run,
            warnings=[
                f"item_id must be a GUID; got {bad[0].item_id!r}"
            ],
        )

    # Admin scope gate.
    ok, reason = _check_admin_scope(admin_scopes or [])
    if not ok:
        if audit_logger is not None:
            try:
                audit_logger(
                    action="sensitivity_labels_blocked",
                    tenant_id=tenant_id,
                    label_id=label_id,
                    reason=reason,
                )
                audit_entries.append("blocked:scope")
            except Exception:  # noqa: BLE001
                pass
        return SetSensitivityLabelsResult(
            dry_run=dry_run,
            warnings=[
                reason,
                "remediation: assign *.Admin.* or "
                "InformationProtectionPolicy.Apply.All scope to the SPN "
                "via Purview admin center",
            ],
        )

    # Pre-dedup: merge duplicate item_ids to avoid double-call.
    seen: set[str] = set()
    deduped: list[LabelTarget] = []
    for it in normalised:
        if it.item_id in seen:
            continue
        seen.add(it.item_id)
        deduped.append(it)

    if dry_run or fabric_admin_client is None:
        for it in deduped:
            applied.append(
                LabeledItem(
                    item_id=it.item_id,
                    item_name_redacted=_redact(it.item_name)
                    if (redact_names and it.item_name)
                    else None,
                    item_name_raw=None if redact_names else it.item_name,
                    status="applied",
                    reason="dry_run",
                )
            )
        return SetSensitivityLabelsResult(
            applied=applied,
            bulk_set_calls=0,
            audit_log_entries=audit_entries,
            dry_run=True,
            warnings=[
                "no fabric_admin_client injected; run=true is required to "
                "actually invoke /admin/items/labels/bulkSet"
                if fabric_admin_client is None
                else "dry_run=True; no REST call attempted"
            ],
        )

    # Real call.
    try:
        result = fabric_admin_client.admin_bulk_set_labels(
            tenant_id=tenant_id,
            token=admin_token,
            label_id=label_id,
            item_ids=[it.item_id for it in deduped],
        )
    except Exception as exc:  # noqa: BLE001
        # Map common HTTP errors to elicitation messages.
        msg = str(exc)
        if "403" in msg or "Forbidden" in msg:
            return SetSensitivityLabelsResult(
                dry_run=dry_run,
                warnings=[
                    "403 Forbidden from /admin/items/labels/bulkSet: "
                    "grant *.Admin.* scope to the SPN via Purview admin center"
                ],
            )
        return SetSensitivityLabelsResult(
            dry_run=dry_run,
            warnings=[f"bulk_set_labels failed: {exc}"],
        )

    bulk_set_calls = 1
    # Per-item outcome from the response; default: all "applied".
    response_items = result.get("items", []) if isinstance(result, dict) else []
    response_items_by_id = {
        str(item.get("item_id")): item for item in response_items
    }

    for it in deduped:
        resp = response_items_by_id.get(it.item_id, {})
        status = str(resp.get("status", "applied"))
        reason = str(resp.get("reason", ""))
        if status in {"skipped", "already_labeled"}:
            applied.append(
                LabeledItem(
                    item_id=it.item_id,
                    item_name_redacted=_redact(it.item_name)
                    if (redact_names and it.item_name)
                    else None,
                    item_name_raw=None if redact_names else it.item_name,
                    status="skipped",
                    reason=reason or "already_labeled",
                )
            )
        elif status == "failed":
            applied.append(
                LabeledItem(
                    item_id=it.item_id,
                    item_name_redacted=_redact(it.item_name)
                    if (redact_names and it.item_name)
                    else None,
                    item_name_raw=None if redact_names else it.item_name,
                    status="failed",
                    reason=reason,
                )
            )
        else:
            applied.append(
                LabeledItem(
                    item_id=it.item_id,
                    item_name_redacted=_redact(it.item_name)
                    if (redact_names and it.item_name)
                    else None,
                    item_name_raw=None if redact_names else it.item_name,
                    status="applied",
                )
            )

    # Audit log.
    if audit_logger is not None:
        for entry in applied:
            try:
                audit_logger(
                    action="sensitivity_labeled",
                    tenant_id=tenant_id,
                    label_id=label_id,
                    label_name=label_name,
                    item_id=entry.item_id,
                    status=entry.status,
                    reason=entry.reason,
                )
                audit_entries.append(f"{entry.item_id}:{entry.status}")
            except Exception:  # noqa: BLE001
                warnings.append("audit_logger raised; ignored")

    return SetSensitivityLabelsResult(
        applied=applied,
        bulk_set_calls=bulk_set_calls,
        audit_log_entries=audit_entries,
        dry_run=dry_run,
        warnings=warnings,
    )


__all__ = [
    "LabelTarget",
    "LabeledItem",
    "SetSensitivityLabels",
    "SetSensitivityLabelsResult",
    "set_sensitivity_labels",
]
