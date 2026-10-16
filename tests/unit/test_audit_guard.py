from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from powerbi_orchestrator_mcp.cloud.audit_cloud import CloudAuditLog
from powerbi_orchestrator_mcp.orchestrator import audit_guard
from powerbi_orchestrator_mcp.orchestrator.audit import AuditLog
from powerbi_orchestrator_mcp.orchestrator.audit_guard import (
    AUDIT_WARNING_KEY,
    audited,
)


def _rows(db_path: Path) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(str(db_path))
    try:
        return list(
            conn.execute(
                "SELECT tool_name, target_id, result_status, payload_json, tool_args_hash "
                "FROM audit_log ORDER BY id"
            )
        )
    finally:
        conn.close()


class TestClassification:
    def test_dry_run_detected(self) -> None:
        assert audit_guard._classify({"dry_run": True}) == "dry_run"

    def test_dry_run_detected_from_status(self) -> None:
        assert audit_guard._classify({"status": "DryRun"}) == "dry_run"

    def test_plain_success(self) -> None:
        assert audit_guard._classify({"item_id": "x"}) == "success"

    def test_errors_without_partial_progress_is_failed(self) -> None:
        assert audit_guard._classify({"errors": ["boom"]}) == "failed"

    def test_errors_with_progress_is_partial(self) -> None:
        assert audit_guard._classify({"errors": ["boom"], "publish_ok": True}) == "partial"

    def test_rolled_back(self) -> None:
        assert audit_guard._classify({"rolled_back": True}) == "rolled_back"

    def test_non_dict_is_success(self) -> None:
        assert audit_guard._classify([1, 2, 3]) == "success"


class TestRedaction:
    def test_secret_like_keys_are_masked(self) -> None:
        masked = audit_guard._redact({"client_secret": "hunter2", "workspace_id": "ws"})

        assert masked["client_secret"] == "[redacted]"
        assert masked["workspace_id"] == "ws"

    @pytest.mark.parametrize(
        "key", ["client_secret", "CLIENT_SECRET", "api_key", "access_token", "password"]
    )
    def test_all_secret_hints_masked(self, key: str) -> None:
        assert audit_guard._redact({key: "v"})[key] == "[redacted]"

    def test_non_scalar_values_are_type_summarised(self) -> None:
        masked = audit_guard._redact({"findings": [{"a": 1}]})

        assert masked["findings"] == "<list>"


class TestSummary:
    def test_only_known_identifiers_are_kept(self) -> None:
        summary = audit_guard._summarize(
            {"item_id": "i1", "dax_query": "EVALUATE Customers", "rows": [1, 2]}
        )

        assert summary == {"item_id": "i1"}

    def test_error_count_included(self) -> None:
        assert audit_guard._summarize({"errors": ["a", "b"]})["error_count"] == 2

    def test_non_dict_gives_empty(self) -> None:
        assert audit_guard._summarize("nope") == {}


class TestAuditedDecorator:
    @pytest.fixture
    def audit_db(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        db_path = tmp_path / "audit.db"

        def _factory(*_a: Any, **_kw: Any) -> CloudAuditLog:
            return CloudAuditLog(AuditLog(db_path=db_path))

        monkeypatch.setattr(audit_guard, "CloudAuditLog", _factory)
        return db_path

    @pytest.mark.asyncio
    async def test_records_successful_call(self, audit_db: Path) -> None:
        @audited("demo_tool", target_arg="workspace_id")
        async def demo_tool(workspace_id: str) -> dict[str, Any]:
            return {"item_id": "abc"}

        await demo_tool(workspace_id="ws-9")

        rows = _rows(audit_db)
        assert len(rows) == 1
        assert rows[0][0] == "cloud:demo_tool"
        assert rows[0][1] == "ws-9"
        assert rows[0][2] == "success"
        assert AuditLog(db_path=audit_db).verify().valid

    @pytest.mark.asyncio
    async def test_records_failure_and_reraises(self, audit_db: Path) -> None:
        @audited("boom_tool")
        async def boom_tool() -> dict[str, Any]:
            raise RuntimeError("kaboom")

        with pytest.raises(RuntimeError, match="kaboom"):
            await boom_tool()

        rows = _rows(audit_db)
        assert rows[0][2] == "failed"
        assert "RuntimeError" in rows[0][3]
        assert "kaboom" not in rows[0][3]

    @pytest.mark.asyncio
    async def test_records_dry_run_status(self, audit_db: Path) -> None:
        @audited("dry_tool")
        async def dry_tool() -> dict[str, Any]:
            return {"dry_run": True, "planned_actions": ["a"]}

        await dry_tool()

        assert _rows(audit_db)[0][2] == "dry_run"

    @pytest.mark.asyncio
    async def test_secret_args_are_not_persisted_in_clear(self, audit_db: Path) -> None:
        @audited("auth_tool")
        async def auth_tool(client_secret: str = "s3cr3t-value") -> dict[str, Any]:
            return {"status": "ok"}

        await auth_tool()

        blob = audit_db.read_bytes()
        assert b"s3cr3t-value" not in blob

    @pytest.mark.asyncio
    async def test_decorator_preserves_function_metadata(self) -> None:
        @audited("meta_tool")
        async def meta_tool(workspace_id: str) -> dict[str, Any]:
            """Docstring stays."""
            return {}

        assert meta_tool.__name__ == "meta_tool"
        assert meta_tool.__doc__ == "Docstring stays."

    @pytest.mark.asyncio
    async def test_successful_write_adds_no_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PBI_ORCHESTRATOR_AUDIT_SECRET", "k")

        @audited("ok_tool")
        async def ok_tool() -> dict[str, Any]:
            return {"status": "ok"}

        result = await ok_tool()

        assert AUDIT_WARNING_KEY not in result
