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
        # The attempt is recorded before the action, the outcome after.
        assert len(rows) == 2
        assert rows[0][2] == audit_guard.RESULT_ATTEMPTED
        assert rows[1][0] == "cloud:demo_tool"
        assert rows[1][1] == "ws-9"
        assert rows[1][2] == "success"
        assert AuditLog(db_path=audit_db).verify().valid

    @pytest.mark.asyncio
    async def test_the_attempt_is_recorded_before_the_action(self, audit_db: Path) -> None:
        """A crash in the middle must leave a trace of what was attempted.

        Without the write-ahead record, a mutation that dies half way
        through leaves nothing in the log at all.
        """
        seen: list[str] = []

        @audited("mid_tool")
        async def mid_tool() -> dict[str, Any]:
            seen.append(_rows(audit_db)[0][2])
            raise RuntimeError("died half way")

        with pytest.raises(RuntimeError, match="died half way"):
            await mid_tool()

        assert seen == [audit_guard.RESULT_ATTEMPTED]

    @pytest.mark.asyncio
    async def test_records_failure_and_reraises(self, audit_db: Path) -> None:
        @audited("boom_tool")
        async def boom_tool() -> dict[str, Any]:
            raise RuntimeError("kaboom")

        with pytest.raises(RuntimeError, match="kaboom"):
            await boom_tool()

        rows = _rows(audit_db)
        assert rows[-1][2] == "failed"
        assert "RuntimeError" in rows[-1][3]
        assert "kaboom" not in rows[-1][3]

    @pytest.mark.asyncio
    async def test_records_dry_run_status(self, audit_db: Path) -> None:
        @audited("dry_tool")
        async def dry_tool() -> dict[str, Any]:
            return {"dry_run": True, "planned_actions": ["a"]}

        await dry_tool()

        assert _rows(audit_db)[-1][2] == "dry_run"

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


class TestFailClosed:
    """The audit log as a precondition, not a report.

    Failing closed is only possible because the attempt is recorded
    before the action: if the write happens after, the action has
    already happened and there is nothing left to refuse.
    """

    @pytest.fixture
    def broken_audit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _factory(*_a: Any, **_kw: Any) -> CloudAuditLog:
            raise OSError("disk full")

        monkeypatch.setattr(audit_guard, "CloudAuditLog", _factory)

    @pytest.mark.asyncio
    async def test_the_tool_does_not_run_when_the_audit_cannot_be_written(
        self, broken_audit: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(audit_guard.AUDIT_FAIL_CLOSED_ENV, "1")
        ran: list[str] = []

        @audited("blocked_tool")
        async def blocked_tool() -> dict[str, Any]:
            ran.append("ran")
            return {"status": "ok"}

        with pytest.raises(audit_guard.AuditUnavailableError):
            await blocked_tool()

        assert ran == [], "the tool ran even though the audit could not be written"

    @pytest.mark.asyncio
    async def test_the_default_is_fail_open(
        self, broken_audit: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without the opt-in, a broken audit log must not block the tool.

        A full disk would otherwise take every mutation down with it,
        and the local audit log is a forensic trail, not the compliance
        system of record.
        """
        monkeypatch.delenv(audit_guard.AUDIT_FAIL_CLOSED_ENV, raising=False)
        ran: list[str] = []

        @audited("open_tool")
        async def open_tool() -> dict[str, Any]:
            ran.append("ran")
            return {"status": "ok"}

        result = await open_tool()

        assert ran == ["ran"]
        assert AUDIT_WARNING_KEY in result
        assert "audit_log_write_failed" in result[AUDIT_WARNING_KEY]

    @pytest.mark.asyncio
    async def test_the_error_names_the_variable_that_relaxes_it(
        self, broken_audit: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(audit_guard.AUDIT_FAIL_CLOSED_ENV, "1")

        @audited("blocked_tool")
        async def blocked_tool() -> dict[str, Any]:
            return {}

        with pytest.raises(audit_guard.AuditUnavailableError) as exc:
            await blocked_tool()

        assert audit_guard.AUDIT_FAIL_CLOSED_ENV in str(exc.value)

    @pytest.mark.asyncio
    async def test_fail_closed_accepts_the_documented_spellings(
        self, broken_audit: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for value in ("1", "true", "TRUE", "yes"):
            monkeypatch.setenv(audit_guard.AUDIT_FAIL_CLOSED_ENV, value)

            @audited("blocked_tool")
            async def blocked_tool() -> dict[str, Any]:
                return {}

            with pytest.raises(audit_guard.AuditUnavailableError):
                await blocked_tool()
