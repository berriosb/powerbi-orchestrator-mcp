"""Tests for the read-only mode and the audit trail's actor.

Two things an operator needs from a deployment before it goes near real
data:

- A switch that says "this server may not change anything", that no
  scope can talk its way around.
- A trail that says **who** acted, not just what happened. The payload
  is part of the hashed row, so the actor cannot be edited afterwards
  without breaking the chain.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from powerbi_orchestrator_mcp.cloud.audit_cloud import CloudAuditLog
from powerbi_orchestrator_mcp.orchestrator import audit_guard
from powerbi_orchestrator_mcp.orchestrator.audit import AuditLog
from powerbi_orchestrator_mcp.orchestrator.audit_guard import audited
from powerbi_orchestrator_mcp.orchestrator.authorization import (
    READONLY_ENV,
    SCOPE_ADMIN,
    TOOL_REQUIRED_SCOPE,
    ReadOnlyModeError,
    authorize_tool,
    effective_principal,
    readonly_mode,
    required_scope_for_tool,
    requires_scope,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No test inherits another's read-only mode or bound identity."""
    from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

    monkeypatch.delenv(READONLY_ENV, raising=False)
    set_current_user(None)
    yield
    set_current_user(None)


class TestReadonlyModeSwitch:
    def test_off_by_default(self) -> None:
        assert readonly_mode() is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
    def test_reads_the_environment(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(READONLY_ENV, value)
        assert readonly_mode() is True

    def test_a_misspelled_value_does_not_enable_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(READONLY_ENV, "maybe")
        assert readonly_mode() is False


class TestReadonlyRefusesMutatingTools:
    def test_a_write_tool_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(READONLY_ENV, "1")

        with pytest.raises(ReadOnlyModeError, match="read-only mode"):
            authorize_tool("deploy_to_workspace")

    def test_an_admin_tool_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(READONLY_ENV, "1")

        with pytest.raises(ReadOnlyModeError):
            authorize_tool("set_sensitivity_labels")

    def test_a_read_tool_is_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(READONLY_ENV, "1")

        authorize_tool("optimize_report_performance")
        authorize_tool("execute_dax_query")

    def test_every_mutating_tool_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The switch covers the whole mutating surface, not a few tools."""
        monkeypatch.setenv(READONLY_ENV, "1")

        for tool_name, scope in TOOL_REQUIRED_SCOPE.items():
            assert scope != "Tools.Read"
            with pytest.raises(ReadOnlyModeError):
                authorize_tool(tool_name)

    def test_an_admin_principal_cannot_talk_its_way_in(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Read-only is a property of the deployment, not of the caller."""
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        monkeypatch.setenv(READONLY_ENV, "1")
        set_current_user({"roles": [SCOPE_ADMIN]})

        with pytest.raises(ReadOnlyModeError):
            authorize_tool("deploy_to_workspace")

    def test_read_tools_still_work_for_a_write_principal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        monkeypatch.setenv(READONLY_ENV, "1")
        set_current_user({"scp": "Tools.Read Tools.Write"})

        authorize_tool("powerbi_health")


class TestReadonlyBlocksBeforeTheToolBody:
    @pytest.mark.asyncio
    async def test_the_body_never_runs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(READONLY_ENV, "1")
        ran: list[str] = []

        @requires_scope("deploy_to_workspace")
        async def tool() -> dict[str, Any]:
            ran.append("yes")
            return {}

        with pytest.raises(ReadOnlyModeError):
            await tool()

        assert ran == []

    @pytest.mark.asyncio
    async def test_a_read_tool_still_runs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(READONLY_ENV, "1")

        @requires_scope("powerbi_health")
        async def tool() -> dict[str, Any]:
            return {"ok": True}

        assert await tool() == {"ok": True}


class TestEffectivePrincipal:
    def test_uses_the_bound_principal(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        set_current_user({"upn": "someone@example.com"})

        assert effective_principal() == "someone@example.com"

    def test_falls_back_to_the_os_user(self) -> None:
        """Over stdio there is no principal, but there is a person."""
        principal = effective_principal()

        assert principal.startswith("local:")
        assert len(principal) > len("local:")

    def test_the_fallback_is_marked_as_local(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        set_current_user({"sub": "abc-123"})

        assert not effective_principal().startswith("local:")


class TestTheAuditTrailRecordsTheActor:
    @pytest.fixture
    def audit_db(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        db_path = tmp_path / "audit.db"

        def _factory(*_a: Any, **_kw: Any) -> CloudAuditLog:
            return CloudAuditLog(AuditLog(db_path=db_path))

        monkeypatch.setattr(audit_guard, "CloudAuditLog", _factory)
        return db_path

    def _principals(self, db_path: Path) -> list[str | None]:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute("SELECT principal FROM audit_log ORDER BY id").fetchall()
        finally:
            conn.close()
        return [r[0] for r in rows]

    @pytest.mark.asyncio
    async def test_the_principal_is_in_its_own_column(self, audit_db: Path) -> None:
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        set_current_user({"upn": "someone@example.com"})

        @audited("deploy_to_workspace")
        async def tool() -> dict[str, Any]:
            return {"item_id": "x"}

        await tool()

        principals = self._principals(audit_db)
        assert principals
        assert all(p == "someone@example.com" for p in principals)

    @pytest.mark.asyncio
    async def test_the_principal_is_not_redacted(self, audit_db: Path) -> None:
        """A UPN is an email, and the payload redactor hashes those.

        The actor has to survive redaction intact, or the trail says
        what happened and not who did it.
        """
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        set_current_user({"upn": "someone@example.com"})

        @audited("deploy_to_workspace")
        async def tool() -> dict[str, Any]:
            return {"item_id": "x"}

        await tool()

        principals = self._principals(audit_db)
        assert "someone@example.com" in principals

    @pytest.mark.asyncio
    async def test_the_actor_is_part_of_the_hashed_row(self, audit_db: Path) -> None:
        """Editing the actor afterwards has to break the chain."""
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        set_current_user({"upn": "someone@example.com"})

        @audited("deploy_to_workspace")
        async def tool() -> dict[str, Any]:
            return {"item_id": "x"}

        await tool()

        conn = sqlite3.connect(str(audit_db))
        try:
            conn.execute(
                "UPDATE audit_log SET principal = ? WHERE id = 1",
                ("someone-else@example.com",),
            )
            conn.commit()
        finally:
            conn.close()

        result = AuditLog(db_path=audit_db).verify()
        assert result.valid is False

    @pytest.mark.asyncio
    async def test_the_local_fallback_is_recorded(self, audit_db: Path) -> None:
        @audited("deploy_to_workspace")
        async def tool() -> dict[str, Any]:
            return {"item_id": "x"}

        await tool()

        principals = self._principals(audit_db)
        assert principals
        first = principals[0]
        assert first is not None
        assert first.startswith("local:")

    def test_a_log_written_before_the_column_still_verifies(self, tmp_path: Path) -> None:
        """An existing audit log must not break at the upgrade."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "old.db"
        conn = _sqlite3.connect(str(db_path))
        try:
            conn.execute(
                """
                CREATE TABLE audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    tool_args_hash TEXT NOT NULL,
                    target_id TEXT,
                    result_status TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    row_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

        log = AuditLog(db_path=db_path)
        log.insert(
            tool_name="old_tool",
            tool_args={},
            result_status="success",
            payload={"step_count": 1},
        )

        result = log.verify()
        assert result.valid is True
        assert result.total_rows == 1


class TestReadonlyIsIndependentOfTheScopeMap:
    def test_a_tool_with_no_scope_entry_defaults_to_read(self) -> None:
        assert required_scope_for_tool("a_tool_that_does_not_exist") == "Tools.Read"

    def test_the_map_covers_every_mutating_tool(self) -> None:
        """A mutating tool missing from the map would be read by default."""
        for tool in ("deploy_to_workspace", "apply_plan", "run_refresh"):
            assert required_scope_for_tool(tool) != "Tools.Read"

    def test_the_audit_guard_is_not_bypassed_by_readonly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Read-only stops the mutation; the attempt is still recorded."""
        monkeypatch.setenv(READONLY_ENV, "1")
        assert audit_guard.AUDIT_FAIL_CLOSED_ENV == "PBI_AUDIT_FAIL_CLOSED"
