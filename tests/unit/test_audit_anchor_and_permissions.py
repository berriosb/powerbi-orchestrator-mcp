from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.orchestrator import audit as audit_mod
from powerbi_orchestrator_mcp.orchestrator.audit import AuditLog


@pytest.fixture
def audit_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "audit" / "audit.db"
    monkeypatch.setattr(audit_mod, "AUDIT_DIR", db_path.parent)
    monkeypatch.setattr(audit_mod, "AUDIT_DB", db_path)
    monkeypatch.setenv("PBI_ORCHESTRATOR_AUDIT_SECRET", "k")
    return db_path


def _delete_where(db_path: Path, clause: str) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(clause)
        conn.commit()
    finally:
        conn.close()


class TestTruncationDetection:
    def test_intact_log_verifies(self, audit_db: Path) -> None:
        log = AuditLog()
        for i in range(5):
            log.insert(tool_name=f"t{i}", tool_args={"i": i}, result_status="success")

        result = log.verify()

        assert result.valid is True
        assert result.total_rows == 5

    def test_deleting_tail_rows_is_detected(self, audit_db: Path) -> None:
        log = AuditLog()
        for i in range(5):
            log.insert(tool_name=f"t{i}", tool_args={"i": i}, result_status="success")

        _delete_where(audit_db, "DELETE FROM audit_log WHERE id > 3")
        result = log.verify()

        assert result.valid is False
        assert "removed outside the audit writer" in (result.error_message or "")

    def test_deleting_every_row_is_detected(self, audit_db: Path) -> None:
        log = AuditLog()
        for i in range(3):
            log.insert(tool_name=f"t{i}", tool_args={"i": i}, result_status="success")

        _delete_where(audit_db, "DELETE FROM audit_log")
        result = log.verify()

        assert result.valid is False
        assert "truncated" in (result.error_message or "")

    def test_deleting_a_middle_row_is_detected_by_the_chain(self, audit_db: Path) -> None:
        log = AuditLog()
        for i in range(5):
            log.insert(tool_name=f"t{i}", tool_args={"i": i}, result_status="success")

        _delete_where(audit_db, "DELETE FROM audit_log WHERE id = 3")
        result = log.verify()

        assert result.valid is False

    def test_rewriting_a_row_is_detected(self, audit_db: Path) -> None:
        log = AuditLog()
        for i in range(3):
            log.insert(tool_name=f"t{i}", tool_args={"i": i}, result_status="success")

        _delete_where(audit_db, "UPDATE audit_log SET tool_name = 'forged' WHERE id = 2")
        result = log.verify()

        assert result.valid is False

    def test_empty_log_without_anchor_is_valid(self, audit_db: Path) -> None:
        assert AuditLog().verify().valid is True

    def test_anchor_records_the_tail(self, audit_db: Path) -> None:
        log = AuditLog()
        entry = log.insert(
            tool_name="t", tool_args={"a": 1}, result_status="success", payload={"x": 1}
        )

        anchor = audit_mod._read_anchor()

        assert anchor is not None
        assert anchor["last_id"] == entry.id
        assert anchor["last_hash"] == entry.row_hash
        assert anchor["count"] == 1

    def test_anchor_survives_a_corrupt_sidecar(self, audit_db: Path) -> None:
        log = AuditLog()
        log.insert(tool_name="t", tool_args={"a": 1}, result_status="success")
        audit_mod._anchor_file().write_text("{not json", encoding="utf-8")

        assert log.verify().valid is True


class TestFilePermissions:
    def test_audit_dir_is_owner_only(self, audit_db: Path) -> None:
        AuditLog().insert(tool_name="t", tool_args={"a": 1}, result_status="success")

        assert stat.S_IMODE(audit_db.parent.stat().st_mode) == 0o700

    def test_audit_db_is_owner_only(self, audit_db: Path) -> None:
        AuditLog().insert(tool_name="t", tool_args={"a": 1}, result_status="success")

        assert stat.S_IMODE(audit_db.stat().st_mode) == 0o600

    def test_anchor_is_owner_only(self, audit_db: Path) -> None:
        AuditLog().insert(tool_name="t", tool_args={"a": 1}, result_status="success")

        anchor = audit_mod._anchor_file()

        assert stat.S_IMODE(anchor.stat().st_mode) == 0o600

    def test_hmac_key_is_owner_only(self, audit_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PBI_ORCHESTRATOR_AUDIT_SECRET", raising=False)

        audit_mod._get_hmac_key()

        assert stat.S_IMODE((audit_db.parent / ".audit_key").stat().st_mode) == 0o600

    def test_plan_db_is_owner_only(self, tmp_path: Path) -> None:
        from powerbi_orchestrator_mcp.orchestrator import plan_store as plan_mod

        monkey_dir = tmp_path / "plans"
        monkey_dir.mkdir()
        original_dir = plan_mod.PLAN_DIR
        original_db = plan_mod.PLAN_DB
        plan_mod.PLAN_DIR = monkey_dir
        plan_mod.PLAN_DB = monkey_dir / "plans.db"
        try:
            conn = plan_mod._connect()
            conn.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
            conn.commit()
            conn.close()
        finally:
            plan_mod.PLAN_DIR = original_dir
            plan_mod.PLAN_DB = original_db

        assert stat.S_IMODE((monkey_dir / "plans.db").stat().st_mode) == 0o600
        assert stat.S_IMODE(monkey_dir.stat().st_mode) == 0o700

    def test_session_db_is_owner_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from powerbi_orchestrator_mcp.orchestrator import context as ctx_mod

        sessions_dir = tmp_path / "sessions"
        monkeypatch.setattr(ctx_mod, "SESSIONS_DIR", sessions_dir)

        store = ctx_mod.SessionStore()
        session = store.create()
        db_file = sessions_dir / f"{session.session_id}.db"

        assert db_file.exists()
        assert stat.S_IMODE(db_file.stat().st_mode) == 0o600
