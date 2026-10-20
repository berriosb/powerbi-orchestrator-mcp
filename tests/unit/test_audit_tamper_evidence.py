"""Tests for the tamper-evidence of the audit chain.

The chain has to detect a row that was edited, a row that was removed,
and an attacker who rewrote the anchor to cover up the removal.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.orchestrator import audit as audit_mod
from powerbi_orchestrator_mcp.orchestrator.audit import AuditLog


@pytest.fixture()
def chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[AuditLog, Path]:
    monkeypatch.setenv("PBI_ORCHESTRATOR_AUDIT_SECRET", "test-secret")
    monkeypatch.setattr(audit_mod, "AUDIT_DIR", tmp_path / "audit")
    db = tmp_path / "audit" / "audit.db"
    monkeypatch.setattr(audit_mod, "AUDIT_DB", db)
    log = AuditLog(db_path=db)
    log.insert(tool_name="t", tool_args={"a": 1}, result_status="success", target_id="ws1")
    log.insert(tool_name="t", tool_args={"a": 2}, result_status="success", target_id="ws2")
    return log, db


class TestFieldBoundaries:
    def test_moving_text_between_fields_is_detected(self, chain: tuple[AuditLog, Path]) -> None:
        """The boundary between two hashed fields has to be unambiguous.

        The fields used to be concatenated, so moving a character from
        the end of one to the start of the next produced a row that
        verified clean.
        """
        log, db = chain
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE audit_log SET target_id='ws1success', result_status='' WHERE id=1")
        conn.commit()
        conn.close()

        result = log.verify()

        assert result.valid is False
        assert result.first_bad_row == 1


class TestAnchorIsSigned:
    def test_a_truncation_is_detected(self, chain: tuple[AuditLog, Path]) -> None:
        log, db = chain
        conn = sqlite3.connect(str(db))
        conn.execute("DELETE FROM audit_log WHERE id=2")
        conn.commit()
        conn.close()

        assert log.verify().valid is False

    def test_rewriting_the_anchor_to_cover_the_truncation_is_detected(
        self, chain: tuple[AuditLog, Path]
    ) -> None:
        """The anchor is signed with the chain key.

        Without the signature, an attacker who can rewrite the
        database can also rewrite the anchor that says where the chain
        should end, and the removal goes unnoticed.
        """
        log, db = chain
        conn = sqlite3.connect(str(db))
        conn.execute("DELETE FROM audit_log WHERE id=2")
        conn.commit()
        conn.close()

        row = (
            sqlite3.connect(str(db))
            .execute("SELECT row_hash FROM audit_log ORDER BY id DESC LIMIT 1")
            .fetchone()
        )
        anchor = audit_mod._anchor_file()
        data = json.loads(anchor.read_text())
        data.update({"last_id": 1, "count": 1, "last_hash": row[0]})
        anchor.write_text(json.dumps(data))

        result = log.verify()

        assert result.valid is False
        assert "anchor signature mismatch" in (result.error_message or "")


class TestKeyLocation:
    def test_the_key_is_not_stored_next_to_the_database(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A key next to the database it protects protects nothing.

        The audit directory is the thing that gets shared, backed up
        and shipped around.
        """
        monkeypatch.delenv("PBI_ORCHESTRATOR_AUDIT_SECRET", raising=False)
        monkeypatch.setenv("PBI_ORCHESTRATOR_HOME", str(tmp_path / "home"))
        audit_dir = tmp_path / "audit"
        monkeypatch.setattr(audit_mod, "AUDIT_DIR", audit_dir)

        audit_mod._get_hmac_key()

        assert not (audit_dir / ".audit_key").exists()
        assert (tmp_path / "home" / ".audit_key").exists()
