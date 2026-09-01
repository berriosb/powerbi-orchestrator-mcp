"""Tests for cloud.audit_cloud — redaction + CloudAuditLog."""

from __future__ import annotations

import pytest

from powerbi_orchestrator_mcp.cloud.audit_cloud import (
    CloudAuditLog,
    redact_payload,
)


class TestRedactPayload:
    def test_redact_bearer_token(self) -> None:
        text = "Authorization: Bearer eyJabc.def-ghi_123"
        out = redact_payload({"auth": text})
        assert "Bearer [REDACTED]" in out["auth"]
        assert "eyJabc.def-ghi_123" not in out["auth"]

    def test_redact_oauth_code(self) -> None:
        text = "https://login.example/?code=abc123def456ghi789jkl012"
        out = redact_payload({"url": text})
        assert "code=[REDACTED]" in out["url"]
        assert "abc123def456" not in out["url"]

    def test_redact_connection_string_password(self) -> None:
        text = "Data Source=server.db;Password=hunter2;Initial Catalog=x"
        out = redact_payload({"conn": text})
        assert "Password=[REDACTED]" in out["conn"]
        assert "hunter2" not in out["conn"]

    def test_redact_standalone_jwt(self) -> None:
        text = "Token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature"
        out = redact_payload({"token": text})
        assert "[JWT_REDACTED]" in out["token"]
        assert "eyJhbGciOiJI" not in out["token"]

    def test_redact_email_in_effective_identity(self) -> None:
        text = "alice@acme.example.com"
        out = redact_payload({"effective_identity": text})
        assert "alice@acme.example.com" not in out["effective_identity"]
        assert "<email_hash:" in out["effective_identity"]

    def test_redact_email_consistent_hash(self) -> None:
        # Same email → same hash (deterministic).
        text = "bob@example.org"
        out1 = redact_payload({"x": text})
        out2 = redact_payload({"x": text})
        assert out1["x"] == out2["x"]

    def test_redact_nested_dict(self) -> None:
        payload = {
            "user": {
                "id": "alice@acme.test",
                "token": "Bearer abc",
                "nested": {"code": "code=longcode1234567890ab"},
            }
        }
        out = redact_payload(payload)
        assert "alice@" not in out["user"]["id"]
        assert out["user"]["token"] == "Bearer [REDACTED]"
        assert "[REDACTED]" in out["user"]["nested"]["code"]

    def test_redact_list(self) -> None:
        payload = {"logs": ["Bearer abc", "no token here", "code=abc123def456ghi789jkl"]}
        out = redact_payload(payload)
        assert "Bearer [REDACTED]" in out["logs"][0]
        assert out["logs"][1] == "no token here"
        assert "[REDACTED]" in out["logs"][2]

    def test_no_change_passthrough(self) -> None:
        payload = {"score": 100, "name": "alice"}
        out = redact_payload(payload)
        assert out == payload


class TestCloudAuditLog:
    def test_record_prefixes_tool_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Use the real AuditLog but redirect to a tmp file.
        from pathlib import Path

        from powerbi_orchestrator_mcp.orchestrator import audit as audit_mod

        tmp = Path("/tmp/test_audit.db")
        monkeypatch.setattr(audit_mod, "AUDIT_DB", tmp)
        # Also redirect key file.
        monkeypatch.setattr(audit_mod, "AUDIT_DIR", Path("/tmp/test_audit_dir"))
        # Clear any previous state.
        if tmp.exists():
            tmp.unlink()

        from powerbi_orchestrator_mcp.orchestrator.audit import AuditLog

        base = AuditLog(db_path=tmp)
        cloud = CloudAuditLog(base)
        entry = cloud.record(
            operation="dataset.refresh",
            workspace_id="ws-1",
            dataset_id="ds-1",
            payload={
                "refresh_id": "r1",
                "effective_identity": "alice@acme.test",
                "auth_header": "Bearer abc.def.ghi",
            },
        )
        assert entry.tool_name == "cloud:dataset.refresh"
        # Payload was redacted before persisting.
        import json as _json

        loaded = _json.loads(entry.payload_json)
        assert "<email_hash:" in loaded["effective_identity"]
        assert "Bearer [REDACTED]" in loaded["auth_header"]

    def test_record_includes_retention_days(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pathlib import Path

        from powerbi_orchestrator_mcp.orchestrator import audit as audit_mod

        tmp = Path("/tmp/test_audit2.db")
        monkeypatch.setattr(audit_mod, "AUDIT_DB", tmp)
        monkeypatch.setattr(audit_mod, "AUDIT_DIR", Path("/tmp/test_audit_dir2"))
        if tmp.exists():
            tmp.unlink()

        from powerbi_orchestrator_mcp.orchestrator.audit import AuditLog

        base = AuditLog(db_path=tmp)
        cloud = CloudAuditLog(base, retention_days=30)
        entry = cloud.record(operation="deploy.publish", payload={"workspace_id": "ws-1"})
        import json as _json

        loaded = _json.loads(entry.payload_json)
        assert loaded["retention_days"] == 30
