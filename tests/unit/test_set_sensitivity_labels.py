"""Tests for set_sensitivity_labels (Sprint 12)."""

from __future__ import annotations

import re
from typing import Any

import pytest

from powerbi_orchestrator_mcp.tools.set_sensitivity_labels import (
    LabelTarget,
    SetSensitivityLabels,
    set_sensitivity_labels,
)


VALID_GUID = "00000000-0000-0000-0000-000000000001"
VALID_LABEL_GUID = "11111111-1111-1111-1111-111111111111"


class _AdminClientStub:
    def __init__(self, raise_error: Exception | None = None) -> None:
        self._raise_error = raise_error
        self.calls: list[dict[str, Any]] = []

    def admin_bulk_set_labels(
        self,
        tenant_id: str | None,
        token: str | None,
        label_id: str,
        item_ids: list[str],
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "token": token,
                "label_id": label_id,
                "item_ids": item_ids,
            }
        )
        if self._raise_error is not None:
            raise self._raise_error
        return {
            "items": [
                {"item_id": i, "status": "applied"} for i in item_ids
            ]
        }


class TestSpec:
    def test_label_id_must_be_guid(self) -> None:
        with pytest.raises(Exception):
            SetSensitivityLabels(
                items=[LabelTarget(item_id=VALID_GUID)],
                label_id="not-a-guid",
                label_name="x",
            )

    def test_item_id_must_be_guid(self) -> None:
        with pytest.raises(Exception):
            SetSensitivityLabels(
                items=[LabelTarget(item_id="not-a-guid")],
                label_id=VALID_LABEL_GUID,
                label_name="x",
            )


class TestScopeGate:
    def test_no_scopes_returns_warning(self) -> None:
        r = set_sensitivity_labels(
            items=[LabelTarget(item_id=VALID_GUID, item_name="Sales")],
            label_id=VALID_LABEL_GUID,
            label_name="Confidential",
            admin_scopes=[],
            dry_run=False,
            fabric_admin_client=_AdminClientStub(),
        )
        assert any("admin" in w.lower() for w in r.warnings)
        assert any("remediation" in w for w in r.warnings)

    def test_wildcard_admin_scope_passes(self) -> None:
        r = set_sensitivity_labels(
            items=[LabelTarget(item_id=VALID_GUID, item_name="Sales")],
            label_id=VALID_LABEL_GUID,
            label_name="Confidential",
            admin_scopes=["*.Admin.*"],
            dry_run=False,
            fabric_admin_client=_AdminClientStub(),
        )
        assert r.bulk_set_calls == 1
        assert any(a.status == "applied" for a in r.applied)

    def test_explicit_apply_all_scope_passes(self) -> None:
        r = set_sensitivity_labels(
            items=[LabelTarget(item_id=VALID_GUID, item_name="Sales")],
            label_id=VALID_LABEL_GUID,
            label_name="Confidential",
            admin_scopes=["InformationProtectionPolicy.Apply.All"],
            dry_run=False,
            fabric_admin_client=_AdminClientStub(),
        )
        assert r.bulk_set_calls == 1


class TestExecution:
    def test_no_items_returns_warning(self) -> None:
        r = set_sensitivity_labels(
            items=[],
            label_id=VALID_LABEL_GUID,
            label_name="x",
            admin_scopes=["*.Admin.*"],
        )
        assert any("no items" in w for w in r.warnings)

    def test_missing_label_id_returns_warning(self) -> None:
        r = set_sensitivity_labels(
            items=[LabelTarget(item_id=VALID_GUID)],
            label_id="",
            label_name="x",
            admin_scopes=["*.Admin.*"],
        )
        assert any("label_id" in w for w in r.warnings)

    def test_dry_run_no_admin_client(self) -> None:
        r = set_sensitivity_labels(
            items=[LabelTarget(item_id=VALID_GUID, item_name="Sales")],
            label_id=VALID_LABEL_GUID,
            label_name="x",
            admin_scopes=["*.Admin.*"],
            dry_run=True,
            fabric_admin_client=None,
        )
        assert r.dry_run is True
        assert all(a.reason == "dry_run" for a in r.applied)

    def test_real_call_invokes_admin_client(self) -> None:
        client = _AdminClientStub()
        r = set_sensitivity_labels(
            items=[
                LabelTarget(item_id=VALID_GUID, item_name="Sales"),
                LabelTarget(
                    item_id="00000000-0000-0000-0000-000000000002",
                    item_name="Finance",
                ),
            ],
            label_id=VALID_LABEL_GUID,
            label_name="Confidential",
            admin_scopes=["*.Admin.*"],
            dry_run=False,
            fabric_admin_client=client,
        )
        assert client.calls == [
            {
                "tenant_id": None,
                "token": None,
                "label_id": VALID_LABEL_GUID,
                "item_ids": [
                    "00000000-0000-0000-0000-000000000001",
                    "00000000-0000-0000-0000-000000000002",
                ],
            }
        ]
        assert r.bulk_set_calls == 1
        assert len(r.applied) == 2

    def test_duplicate_items_dedup(self) -> None:
        client = _AdminClientStub()
        r = set_sensitivity_labels(
            items=[
                LabelTarget(item_id=VALID_GUID, item_name="a"),
                LabelTarget(item_id=VALID_GUID, item_name="a"),
            ],
            label_id=VALID_LABEL_GUID,
            label_name="x",
            admin_scopes=["*.Admin.*"],
            dry_run=False,
            fabric_admin_client=client,
        )
        assert client.calls[0]["item_ids"] == [VALID_GUID]
        assert len(r.applied) == 1

    def test_already_labeled_marked_skipped(self) -> None:
        client = _AdminClientStub()
        # Override the client's response to mark skipped.
        client.admin_bulk_set_labels = (  # type: ignore[assignment]
            lambda tenant_id, token, label_id, item_ids: {
                "items": [
                    {
                        "item_id": i,
                        "status": "skipped",
                        "reason": "already_labeled",
                    }
                    for i in item_ids
                ]
            }
        )
        r = set_sensitivity_labels(
            items=[LabelTarget(item_id=VALID_GUID, item_name="x")],
            label_id=VALID_LABEL_GUID,
            label_name="y",
            admin_scopes=["*.Admin.*"],
            dry_run=False,
            fabric_admin_client=client,
        )
        assert r.applied[0].status == "skipped"
        assert r.applied[0].reason == "already_labeled"

    def test_failed_status_recorded(self) -> None:
        client = _AdminClientStub()
        client.admin_bulk_set_labels = (  # type: ignore[assignment]
            lambda tenant_id, token, label_id, item_ids: {
                "items": [
                    {
                        "item_id": item_ids[0],
                        "status": "failed",
                        "reason": "permission denied",
                    }
                ]
            }
        )
        r = set_sensitivity_labels(
            items=[LabelTarget(item_id=VALID_GUID, item_name="x")],
            label_id=VALID_LABEL_GUID,
            label_name="y",
            admin_scopes=["*.Admin.*"],
            dry_run=False,
            fabric_admin_client=client,
        )
        assert r.applied[0].status == "failed"

    def test_403_maps_to_elicitation(self) -> None:
        client = _AdminClientStub(raise_error=Exception("HTTP 403 Forbidden"))
        r = set_sensitivity_labels(
            items=[LabelTarget(item_id=VALID_GUID, item_name="x")],
            label_id=VALID_LABEL_GUID,
            label_name="y",
            admin_scopes=["*.Admin.*"],
            dry_run=False,
            fabric_admin_client=client,
        )
        assert any("403" in w for w in r.warnings)
        assert any("grant" in w.lower() for w in r.warnings)

    def test_generic_error_recorded(self) -> None:
        client = _AdminClientStub(raise_error=Exception("network error"))
        r = set_sensitivity_labels(
            items=[LabelTarget(item_id=VALID_GUID, item_name="x")],
            label_id=VALID_LABEL_GUID,
            label_name="y",
            admin_scopes=["*.Admin.*"],
            dry_run=False,
            fabric_admin_client=client,
        )
        assert any("network error" in w for w in r.warnings)


class TestRedactionAndAudit:
    def test_name_redaction(self) -> None:
        r = set_sensitivity_labels(
            items=[LabelTarget(item_id=VALID_GUID, item_name="PII Sales")],
            label_id=VALID_LABEL_GUID,
            label_name="x",
            admin_scopes=["*.Admin.*"],
            dry_run=False,
            fabric_admin_client=_AdminClientStub(),
            redact_names=True,
        )
        entry = r.applied[0]
        assert entry.item_name_redacted is not None
        assert entry.item_name_redacted.startswith("sha256:")
        assert entry.item_name_raw is None
        assert "PII Sales" not in r.warnings

    def test_no_redaction_when_disabled(self) -> None:
        r = set_sensitivity_labels(
            items=[LabelTarget(item_id=VALID_GUID, item_name="Visible")],
            label_id=VALID_LABEL_GUID,
            label_name="x",
            admin_scopes=["*.Admin.*"],
            dry_run=False,
            fabric_admin_client=_AdminClientStub(),
            redact_names=False,
        )
        assert r.applied[0].item_name_raw == "Visible"
        assert r.applied[0].item_name_redacted is None

    def test_audit_logger_invoked(self) -> None:
        events: list[dict[str, Any]] = []

        def logger(**kwargs: Any) -> None:
            events.append(kwargs)

        set_sensitivity_labels(
            items=[LabelTarget(item_id=VALID_GUID, item_name="x")],
            label_id=VALID_LABEL_GUID,
            label_name="Confidential",
            admin_scopes=["*.Admin.*"],
            dry_run=False,
            fabric_admin_client=_AdminClientStub(),
            audit_logger=logger,
        )
        assert any(e.get("action") == "sensitivity_labeled" for e in events)

    def test_audit_logger_invoked_on_block(self) -> None:
        events: list[dict[str, Any]] = []

        def logger(**kwargs: Any) -> None:
            events.append(kwargs)

        set_sensitivity_labels(
            items=[LabelTarget(item_id=VALID_GUID, item_name="x")],
            label_id=VALID_LABEL_GUID,
            label_name="x",
            admin_scopes=[],
            fabric_admin_client=_AdminClientStub(),
            audit_logger=logger,
        )
        assert any(
            e.get("action") == "sensitivity_labels_blocked" for e in events
        )

    def test_audit_logger_failure_doesnt_crash(self) -> None:
        def logger(**kwargs: Any) -> None:
            raise RuntimeError("disk full")

        # Should not raise.
        r = set_sensitivity_labels(
            items=[LabelTarget(item_id=VALID_GUID, item_name="x")],
            label_id=VALID_LABEL_GUID,
            label_name="x",
            admin_scopes=["*.Admin.*"],
            dry_run=False,
            fabric_admin_client=_AdminClientStub(),
            audit_logger=logger,
        )
        assert r.bulk_set_calls == 1

    def test_redaction_pattern(self) -> None:
        r = set_sensitivity_labels(
            items=[LabelTarget(item_id=VALID_GUID, item_name="X")],
            label_id=VALID_LABEL_GUID,
            label_name="x",
            admin_scopes=["*.Admin.*"],
            dry_run=False,
            fabric_admin_client=_AdminClientStub(),
        )
        m = re.match(r"sha256:[0-9a-f]{16}", r.applied[0].item_name_redacted or "")
        assert m is not None
