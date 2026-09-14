"""Audit log with HMAC chain - tamper-evident logging.

Public API:
- ``AuditLog`` class for write + verify.
- ``count_entries()`` for diagnostic / health-check usage.
- ``AUDIT_DB`` for path introspection (used by ``powerbi_health`` tool).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AUDIT_DIR = Path.home() / ".powerbi-orchestrator-mcp" / "audit"
AUDIT_DB = AUDIT_DIR / "audit.db"
_HMAC_KEY_ENV = "PBI_ORCHestrATOR_AUDIT_SECRET"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AuditEntry(BaseModel):
    """Single audit log entry (spec section 2.4)."""

    id: int = 0
    timestamp: str
    tool_name: str
    tool_args_hash: str
    target_id: str | None = None
    result_status: str  # 'success' | 'rolled_back' | 'partial' | 'failed'
    prev_hash: str
    row_hash: str
    payload_json: str


class AuditVerifyResult(BaseModel):
    """Result of audit chain verification."""

    valid: bool
    total_rows: int
    first_bad_row: int | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# HMAC helpers
# ---------------------------------------------------------------------------


def _get_hmac_key() -> bytes:
    """Get the HMAC secret key from env or generate one."""
    secret = os.environ.get(_HMAC_KEY_ENV)
    if secret:
        return secret.encode("utf-8")
    # Generate a persistent key on first use
    key_path = AUDIT_DIR / ".audit_key"
    if key_path.exists():
        return key_path.read_bytes()
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    new_key = secrets.token_bytes(32)
    key_path.write_bytes(new_key)
    return new_key


def _compute_row_hash(key: bytes, prev_hash: str, row_id: int, timestamp: str, rest: str) -> str:
    """Compute HMAC-SHA256 row hash."""
    message = f"{prev_hash}{row_id}{timestamp}{rest}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _ensure_audit_dir() -> None:
    """Create audit directory if needed."""
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)


def _get_conn() -> sqlite3.Connection:
    """Get a connection to the audit database with WAL mode."""
    _ensure_audit_dir()
    conn = sqlite3.connect(str(AUDIT_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
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
    return conn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class AuditLog:
    """SQLite audit log with HMAC chain."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or AUDIT_DB

    def _connect(self) -> sqlite3.Connection:
        _ensure_audit_dir()
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
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
        return conn

    def _get_last_hash(self, conn: sqlite3.Connection) -> str:
        """Get the row_hash of the last entry (or '0' for genesis)."""
        row = conn.execute(
            "SELECT row_hash FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else "0"

    def insert(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        result_status: str,
        target_id: str | None = None,
        payload: dict[str, Any] | None = None,
        *,
        timestamp: str | None = None,
    ) -> AuditEntry:
        """Insert a new audit entry with HMAC chain.

        Args:
            tool_name: Name of the tool that was called.
            tool_args: Arguments passed to the tool.
            result_status: Result status (success, rolled_back, partial, failed).
            target_id: Optional target identifier.
            payload: Optional additional payload data.
            timestamp: Optional timestamp override (for testing).

        Returns:
            The created AuditEntry.
        """
        import datetime

        ts = timestamp or datetime.datetime.now(datetime.UTC).isoformat()
        args_hash = hashlib.sha256(json.dumps(tool_args, sort_keys=True).encode()).hexdigest()
        payload_json = json.dumps(payload or {})

        key = _get_hmac_key()
        conn = self._connect()
        try:
            cursor = conn.execute("SELECT MAX(id) FROM audit_log")
            max_id = cursor.fetchone()[0] or 0
            new_id = max_id + 1

            prev_hash = self._get_last_hash(conn)
            rest = f"{tool_name}{args_hash}{target_id or ''}{result_status}{payload_json}"
            row_hash = _compute_row_hash(key, prev_hash, new_id, ts, rest)

            conn.execute(
                """
                INSERT INTO audit_log (id, timestamp, tool_name, tool_args_hash,
                    target_id, result_status, prev_hash, row_hash, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (new_id, ts, tool_name, args_hash, target_id, result_status,
                 prev_hash, row_hash, payload_json),
            )
            conn.commit()

            return AuditEntry(
                id=new_id,
                timestamp=ts,
                tool_name=tool_name,
                tool_args_hash=args_hash,
                target_id=target_id,
                result_status=result_status,
                prev_hash=prev_hash,
                row_hash=row_hash,
                payload_json=payload_json,
            )
        finally:
            conn.close()

    def verify(self) -> AuditVerifyResult:
        """Verify the entire HMAC chain.

        Returns:
            AuditVerifyResult with validation status.
        """
        key = _get_hmac_key()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, timestamp, tool_name, tool_args_hash, target_id, "
                "result_status, prev_hash, row_hash, payload_json "
                "FROM audit_log ORDER BY id ASC"
            ).fetchall()

            if not rows:
                return AuditVerifyResult(valid=True, total_rows=0)

            prev_hash = "0"
            for row in rows:
                row_id, ts, tool_name, args_hash, target_id, result_status, stored_prev, stored_hash, payload = row

                # Verify prev_hash chain
                if stored_prev != prev_hash:
                    return AuditVerifyResult(
                        valid=False,
                        total_rows=len(rows),
                        first_bad_row=row_id,
                        error_message=f"prev_hash mismatch at row {row_id}: "
                        f"expected {prev_hash}, got {stored_prev}",
                    )

                # Recompute row_hash
                rest = f"{tool_name}{args_hash}{target_id or ''}{result_status}{payload}"
                expected_hash = _compute_row_hash(key, prev_hash, row_id, ts, rest)

                if not hmac.compare_digest(stored_hash, expected_hash):
                    return AuditVerifyResult(
                        valid=False,
                        total_rows=len(rows),
                        first_bad_row=row_id,
                        error_message=f"row_hash mismatch at row {row_id}: "
                        f"expected {expected_hash}, got {stored_hash}",
                    )

                prev_hash = stored_hash

            return AuditVerifyResult(valid=True, total_rows=len(rows))
        finally:
            conn.close()


def count_entries(db_path: Path | None = None) -> int:
    """Return the number of rows in the audit log (0 if it doesn't exist).

    Used by ``powerbi_health`` for diagnostics. Cheap: O(1) COUNT(*).
    Does not create the DB or the parent directory.
    """
    path = db_path or AUDIT_DB
    if not path.exists():
        return 0
    try:
        with sqlite3.connect(str(path)) as conn:
            row = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
            return int(row[0]) if row else 0
    except sqlite3.DatabaseError:
        return 0


# ---------------------------------------------------------------------------
# CLI verify command
# ---------------------------------------------------------------------------


def verify_cli() -> None:
    """CLI command: python -m powerbi_orchestrator_mcp.orchestrator.audit verify."""
    log = AuditLog()
    result = log.verify()
    if result.valid:
        print(f"✓ Audit chain valid ({result.total_rows} entries)")
    else:
        print(
            f"✗ Audit chain BROKEN at row {result.first_bad_row} "
            f"({result.total_rows} total entries): {result.error_message}"
        )
        raise SystemExit(1)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "verify":
        verify_cli()
    else:
        print("Usage: python -m powerbi_orchestrator_mcp.orchestrator.audit verify")
        raise SystemExit(1)
