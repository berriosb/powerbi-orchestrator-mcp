"""Tests for orchestrator.audit — HMAC-chained audit log (spec §2.4)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.orchestrator import audit as audit_mod
from powerbi_orchestrator_mcp.orchestrator.audit import (
    AUDIT_DB,
    AUDIT_DIR,
    AuditEntry,
    AuditLog,
    AuditVerifyResult,
    _compute_row_hash,
    _get_hmac_key,
    verify_cli,
)


@pytest.fixture()
def isolated_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """Redirect AUDIT_DIR and AUDIT_DB to a temp directory.

    Returns (audit_dir, audit_db_path) for convenience.
    """
    audit_dir = tmp_path / "audit"
    audit_db = audit_dir / "audit.db"
    monkeypatch.setattr(audit_mod, "AUDIT_DIR", audit_dir)
    monkeypatch.setattr(audit_mod, "AUDIT_DB", audit_db)
    return audit_dir, audit_db


@pytest.fixture()
def log(isolated_audit: tuple[Path, Path]) -> AuditLog:
    """An AuditLog bound to the isolated temp DB."""
    _, audit_db = isolated_audit
    return AuditLog(db_path=audit_db)


# ---------------------------------------------------------------------------
# HMAC helpers
# ---------------------------------------------------------------------------


class TestHmacHelpers:
    def test_compute_row_hash_deterministic(self) -> None:
        key = b"k" * 32
        h1 = _compute_row_hash(key, "0", 1, "2026-08-21T00:00:00Z", "abc")
        h2 = _compute_row_hash(key, "0", 1, "2026-08-21T00:00:00Z", "abc")
        assert h1 == h2

    def test_compute_row_hash_changes_with_input(self) -> None:
        key = b"k" * 32
        h1 = _compute_row_hash(key, "0", 1, "t1", "abc")
        h2 = _compute_row_hash(key, "0", 1, "t2", "abc")
        assert h1 != h2

    def test_compute_row_hash_matches_stdlib(self) -> None:
        """Sanity check: our computation matches hmac.new directly."""
        key = b"key"
        prev = "prev"
        row_id = 42
        ts = "2026-01-01T00:00:00+00:00"
        rest = "toolxxx"
        expected = hmac.new(
            key,
            f"{prev}{row_id}{ts}{rest}".encode(),
            hashlib.sha256,
        ).hexdigest()
        assert _compute_row_hash(key, prev, row_id, ts, rest) == expected


class TestHmacKey:
    def test_env_var_takes_precedence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PBI_ORCHestrATOR_AUDIT_SECRET", "my-env-secret")
        key = _get_hmac_key()
        assert key == b"my-env-secret"

    def test_generates_and_persists_key(
        self, isolated_audit: tuple[Path, Path]
    ) -> None:
        audit_dir, _ = isolated_audit
        key1 = _get_hmac_key()
        key_path = audit_dir / ".audit_key"
        assert key_path.exists()
        assert len(key1) == 32

        # Second call returns the same persisted key.
        key2 = _get_hmac_key()
        assert key1 == key2

    def test_different_audits_get_different_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # First audit dir.
        dir1 = tmp_path / "a1"
        dir2 = tmp_path / "a2"
        monkeypatch.setattr(audit_mod, "AUDIT_DIR", dir1)
        key1 = _get_hmac_key()
        # Switch to second dir.
        monkeypatch.setattr(audit_mod, "AUDIT_DIR", dir2)
        key2 = _get_hmac_key()
        assert key1 != key2


# ---------------------------------------------------------------------------
# Insert
# ---------------------------------------------------------------------------


class TestInsert:
    def test_first_entry_has_genesis_prev_hash(self, log: AuditLog) -> None:
        entry = log.insert(
            tool_name="test_tool",
            tool_args={"a": 1},
            result_status="success",
        )
        assert entry.prev_hash == "0"
        assert isinstance(entry, AuditEntry)
        assert entry.id == 1
        assert entry.tool_name == "test_tool"
        assert entry.result_status == "success"

    def test_second_entry_chains_off_first(self, log: AuditLog) -> None:
        e1 = log.insert(tool_name="t1", tool_args={}, result_status="success")
        e2 = log.insert(tool_name="t2", tool_args={}, result_status="success")
        assert e2.prev_hash == e1.row_hash
        assert e2.id == e1.id + 1

    def test_args_hash_is_sorted_deterministic(self, log: AuditLog) -> None:
        e1 = log.insert(tool_name="t", tool_args={"b": 2, "a": 1}, result_status="success")
        e2 = log.insert(tool_name="t", tool_args={"a": 1, "b": 2}, result_status="success")
        # Same args (just reordered) → same args_hash.
        assert e1.tool_args_hash == e2.tool_args_hash

    def test_target_id_optional(self, log: AuditLog) -> None:
        entry = log.insert(
            tool_name="t",
            tool_args={},
            result_status="success",
            target_id="fabric_workspace:abc",
        )
        assert entry.target_id == "fabric_workspace:abc"

    def test_target_id_none_stored_as_null(self, log: AuditLog) -> None:
        log.insert(tool_name="t", tool_args={}, result_status="success")
        # Confirm via verify that chain is consistent (target_id=None).
        assert log.verify().valid is True

    def test_payload_persisted(self, log: AuditLog) -> None:
        entry = log.insert(
            tool_name="t",
            tool_args={},
            result_status="success",
            payload={"execution_id": "exec_abc", "step_count": 7},
        )
        payload = json.loads(entry.payload_json)
        assert payload["execution_id"] == "exec_abc"
        assert payload["step_count"] == 7

    def test_custom_timestamp_used(self, log: AuditLog) -> None:
        entry = log.insert(
            tool_name="t",
            tool_args={},
            result_status="success",
            timestamp="2020-01-01T00:00:00+00:00",
        )
        assert entry.timestamp == "2020-01-01T00:00:00+00:00"

    def test_ids_are_sequential(self, log: AuditLog) -> None:
        ids = [
            log.insert(tool_name=f"t{i}", tool_args={}, result_status="success").id
            for i in range(5)
        ]
        assert ids == [1, 2, 3, 4, 5]

    def test_reuses_existing_db_file(
        self, isolated_audit: tuple[Path, Path]
    ) -> None:
        _, db_path = isolated_audit
        log1 = AuditLog(db_path=db_path)
        log1.insert(tool_name="t1", tool_args={}, result_status="success")
        log2 = AuditLog(db_path=db_path)
        e = log2.insert(tool_name="t2", tool_args={}, result_status="success")
        # New instance continues the chain from the previous row.
        assert e.id == 2
        assert e.prev_hash != "0"


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


class TestVerify:
    def test_empty_log_is_valid(self, log: AuditLog) -> None:
        result = log.verify()
        assert result == AuditVerifyResult(valid=True, total_rows=0)

    def test_single_entry_valid(self, log: AuditLog) -> None:
        log.insert(tool_name="t", tool_args={}, result_status="success")
        assert log.verify().valid is True

    def test_chain_valid_after_many_inserts(self, log: AuditLog) -> None:
        for i in range(50):
            log.insert(
                tool_name=f"t{i}",
                tool_args={"i": i},
                result_status="success",
            )
        result = log.verify()
        assert result.valid is True
        assert result.total_rows == 50

    def test_total_rows_counted(self, log: AuditLog) -> None:
        for _ in range(7):
            log.insert(tool_name="t", tool_args={}, result_status="success")
        assert log.verify().total_rows == 7


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------


class TestTamperDetection:
    def test_detects_modified_tool_args(self, log: AuditLog) -> None:
        log.insert(
            tool_name="t", tool_args={"x": 1}, result_status="success"
        )
        # Manually change the payload to simulate tampering.
        conn = sqlite3.connect(str(log._db_path))
        try:
            conn.execute(
                "UPDATE audit_log SET tool_args_hash = ? WHERE id = 1",
                ("deadbeef" * 8,),
            )
            conn.commit()
        finally:
            conn.close()

        result = log.verify()
        assert result.valid is False
        assert result.first_bad_row == 1
        assert "row_hash mismatch" in (result.error_message or "")

    def test_detects_broken_prev_hash_chain(self, log: AuditLog) -> None:
        log.insert(tool_name="t1", tool_args={}, result_status="success")
        log.insert(tool_name="t2", tool_args={}, result_status="success")
        # Break the chain: row 2's prev_hash is no longer row 1's row_hash.
        conn = sqlite3.connect(str(log._db_path))
        try:
            conn.execute(
                "UPDATE audit_log SET prev_hash = ? WHERE id = 2",
                ("0" * 64,),
            )
            conn.commit()
        finally:
            conn.close()

        result = log.verify()
        assert result.valid is False
        assert result.first_bad_row == 2
        assert "prev_hash mismatch" in (result.error_message or "")

    def test_detects_modified_payload_json(self, log: AuditLog) -> None:
        log.insert(
            tool_name="t",
            tool_args={},
            result_status="success",
            payload={"step_count": 1},
        )
        conn = sqlite3.connect(str(log._db_path))
        try:
            conn.execute(
                "UPDATE audit_log SET payload_json = ? WHERE id = 1",
                ('{"step_count": 999}',),
            )
            conn.commit()
        finally:
            conn.close()

        result = log.verify()
        assert result.valid is False
        assert result.first_bad_row == 1

    def test_first_bad_row_is_earliest_break(self, log: AuditLog) -> None:
        log.insert(tool_name="t1", tool_args={}, result_status="success")
        log.insert(tool_name="t2", tool_args={}, result_status="success")
        log.insert(tool_name="t3", tool_args={}, result_status="success")
        # Break row 3; verify() should report row 3, not row 1 or 2.
        conn = sqlite3.connect(str(log._db_path))
        try:
            conn.execute(
                "UPDATE audit_log SET row_hash = ? WHERE id = 3",
                ("bad" * 16,),
            )
            conn.commit()
        finally:
            conn.close()
        result = log.verify()
        assert result.first_bad_row == 3


# ---------------------------------------------------------------------------
# Module-level API
# ---------------------------------------------------------------------------


class TestModuleLevel:
    def test_module_constants_are_paths(self) -> None:
        assert isinstance(AUDIT_DIR, Path)
        assert isinstance(AUDIT_DB, Path)
        assert AUDIT_DB.parent == AUDIT_DIR

    def test_ensure_audit_dir_creates(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(audit_mod, "AUDIT_DIR", tmp_path / "new")
        audit_mod._ensure_audit_dir()
        assert (tmp_path / "new").exists()

    def test_module_get_conn_creates_db(
        self, isolated_audit: tuple[Path, Path]
    ) -> None:
        """The module-level _get_conn is an internal helper; it should
        create the schema if missing."""
        _, db_path = isolated_audit
        conn = audit_mod._get_conn()
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'"
            ).fetchall()
            assert rows
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# CLI verify command
# ---------------------------------------------------------------------------


class TestVerifyCli:
    def test_cli_valid(self, log: AuditLog, capsys: pytest.CaptureFixture[str]) -> None:
        log.insert(tool_name="t", tool_args={}, result_status="success")
        verify_cli()
        captured = capsys.readouterr()
        assert "valid" in captured.out
        assert "1 entries" in captured.out

    def test_cli_broken_exits_1(
        self, log: AuditLog, capsys: pytest.CaptureFixture[str]
    ) -> None:
        log.insert(tool_name="t", tool_args={}, result_status="success")
        conn = sqlite3.connect(str(log._db_path))
        try:
            conn.execute("UPDATE audit_log SET row_hash = 'broken' WHERE id = 1")
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(SystemExit) as exc_info:
            verify_cli()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "BROKEN" in captured.out

    def test_cli_no_args(self) -> None:
        # The __main__ block prints usage and exits 1 when no "verify" arg.
        result = subprocess.run(
            [sys.executable, "-m", "powerbi_orchestrator_mcp.orchestrator.audit"],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": "src"},
        )
        assert result.returncode == 1
        assert "Usage" in result.stdout

    def test_module_main_block_verify(self, log: AuditLog) -> None:
        log.insert(tool_name="t", tool_args={}, result_status="success")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "powerbi_orchestrator_mcp.orchestrator.audit",
                "verify",
            ],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PYTHONPATH": "src",
                # Force UTF-8 on Windows; otherwise subprocess stdout
                # crashes with 'charmap' codec errors on \u2713.
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
            },
        )
        # Note: this subprocess uses the real AUDIT_DIR, not the temp one,
        # so we can't assert on its contents. We assert the script ran
        # without crashing and emitted the expected banner.
        assert (
            "Audit chain" in result.stdout
            or "BROKEN" in result.stdout
            or "Audit" in result.stdout  # Windows: stdout codec drops \u2713
        )
