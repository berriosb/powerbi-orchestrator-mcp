"""Audit log with HMAC chain - tamper-evident logging.

Public API:
- ``AuditLog`` class for write + verify.
- ``count_entries()`` for diagnostic / health-check usage.
- ``AUDIT_DB`` for path introspection (used by ``powerbi_health`` tool).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from powerbi_orchestrator_mcp.orchestrator.paths import (
    atomic_write_private,
    ensure_private_dir,
    restrict_file,
)

log = logging.getLogger(__name__)


def _resolve_audit_dir() -> Path:
    from powerbi_orchestrator_mcp.orchestrator.paths import get_orchestrator_home

    return get_orchestrator_home() / "audit"


AUDIT_DIR = _resolve_audit_dir()
AUDIT_DB = AUDIT_DIR / "audit.db"
_HMAC_KEY_ENV = "PBI_ORCHESTRATOR_AUDIT_SECRET"


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
    principal: str | None = None
    """Who acted.

    A column of its own rather than part of the payload: the payload is
    redacted, and a principal is usually a UPN — an email — which the
    redactor would replace with a hash. A hash is not an attribution.
    The column is part of the hashed row, so the actor cannot be edited
    afterwards without breaking the chain.
    """


class AuditVerifyResult(BaseModel):
    """Result of audit chain verification."""

    valid: bool
    total_rows: int
    first_bad_row: int | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# HMAC helpers
# ---------------------------------------------------------------------------


def _fallback_key_path() -> Path:
    """Where the generated chain key is stored when no secret is set.

    The key is kept out of the audit directory on purpose: the audit
    directory is the thing that gets shared, backed up and shipped
    around, and a key next to the database it protects is no
    protection at all against whoever can write there.
    """
    from powerbi_orchestrator_mcp.orchestrator.paths import get_orchestrator_home

    return get_orchestrator_home() / ".audit_key"


def _get_hmac_key() -> bytes:
    """Get the HMAC secret key from the environment or generate one.

    Only ``PBI_ORCHESTRATOR_AUDIT_SECRET`` is recognised. A misspelled
    variant used to be accepted silently, which made a misconfigured
    deployment look configured.

    Without that variable the key is generated once and stored outside
    the audit directory. That protects the chain against an attacker who
    can write to the audit directory alone, but not against one who can
    read the whole orchestrator home — for that, set the variable.
    """
    secret = os.environ.get(_HMAC_KEY_ENV)
    if secret:
        return secret.encode("utf-8")
    key_path = _fallback_key_path()
    if key_path.exists():
        return key_path.read_bytes()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    new_key = secrets.token_bytes(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(key_path), flags, 0o600)
    with open(fd, "wb") as f:
        f.write(new_key)
    log.warning(
        "audit.key_generated: %s is not set, so the chain key was generated "
        "and stored at %s. Set %s to a secret held outside this machine to "
        "protect the audit log against an attacker who can read the "
        "orchestrator home.",
        _HMAC_KEY_ENV,
        key_path,
        _HMAC_KEY_ENV,
    )
    return new_key


def _compute_row_hash(
    key: bytes,
    prev_hash: str,
    row_id: int,
    timestamp: str,
    fields: Sequence[str],
) -> str:
    """Compute the HMAC-SHA256 row hash.

    The fields are encoded as a canonical JSON array rather than
    concatenated. Without a delimiter the boundary between two fields is
    ambiguous: moving a character from the end of one field to the
    start of the next produced a row that verified clean.
    """
    message = json.dumps([prev_hash, row_id, timestamp, *fields], separators=(",", ":")).encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _row_fields(
    tool_name: str,
    args_hash: str,
    target_id: str | None,
    result_status: str,
    payload_json: str,
    principal: str | None = None,
) -> list[str]:
    """The hashed fields of a row, in a fixed order.

    The principal is hashed with the rest when there is one, so an actor
    cannot be rewritten after the fact without invalidating the chain.

    A row with no principal hashes the older five fields. That keeps an
    audit log written before the column existed verifiable after the
    upgrade, instead of failing the whole chain at row one.
    """
    fields = [tool_name, args_hash, target_id or "", result_status, payload_json]
    if principal:
        fields.append(principal)
    return fields


def _ensure_audit_dir() -> Path:
    ensure_private_dir(AUDIT_DIR)
    return AUDIT_DIR


def _anchor_file() -> Path:
    return AUDIT_DIR / ".audit_anchor"


def _read_anchor() -> dict[str, Any] | None:
    path = _anchor_file()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _anchor_mac(key: bytes, last_id: int, last_hash: str, count: int) -> str:
    """MAC of the anchor's contents.

    Without it, an attacker who can rewrite the audit database can also
    rewrite the anchor that says where the chain should end, and the
    truncation is no longer detected.
    """
    message = json.dumps([last_id, last_hash, count], separators=(",", ":")).encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _write_anchor(last_id: int, last_hash: str, count: int, key: bytes) -> None:
    """Persist the expected chain tail outside the audit database.

    ``verify()`` recomputes the chain from genesis, so deleting the newest
    rows leaves a self-consistent prefix that still verifies. Comparing the
    database tail against this sidecar is what makes removal detectable.
    The anchor is signed with the chain key, so rewriting it needs the key
    too.
    """
    payload = json.dumps(
        {
            "last_id": last_id,
            "last_hash": last_hash,
            "count": count,
            "version": 1,
            "mac": _anchor_mac(key, last_id, last_hash, count),
        }
    )
    try:
        atomic_write_private(_anchor_file(), payload)
    except OSError:
        log.warning("audit.anchor_write_failed: %s", _anchor_file())


def _get_conn() -> sqlite3.Connection:
    _ensure_audit_dir()
    conn = sqlite3.connect(str(AUDIT_DB))
    restrict_file(AUDIT_DB)
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
            payload_json TEXT NOT NULL,
            principal TEXT
        )
        """
    )
    _add_principal_column(conn)
    conn.commit()
    return conn


def _add_principal_column(conn: sqlite3.Connection) -> None:
    """Add the ``principal`` column to an audit log that predates it.

    The column is new. An audit log written before it existed has rows
    without an actor, and those rows keep verifying: a row with no
    principal hashes the older five fields.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(audit_log)")}
    if "principal" not in columns:
        conn.execute("ALTER TABLE audit_log ADD COLUMN principal TEXT")


class AuditLog:
    """SQLite audit log with HMAC chain."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or AUDIT_DB

    def _connect(self) -> sqlite3.Connection:
        ensure_private_dir(self._db_path.parent)
        conn = sqlite3.connect(str(self._db_path))
        restrict_file(self._db_path)
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
                payload_json TEXT NOT NULL,
                principal TEXT
            )
            """
        )
        _add_principal_column(conn)
        conn.commit()
        return conn

    def _get_last_hash(self, conn: sqlite3.Connection) -> str:
        """Get the row_hash of the last entry (or '0' for genesis)."""
        row = conn.execute("SELECT row_hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row else "0"

    def insert(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        result_status: str,
        target_id: str | None = None,
        payload: dict[str, Any] | None = None,
        *,
        principal: str | None = None,
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
        # The id is derived from MAX(id) + 1, so two processes writing at
        # the same time could pick the same one. The write lock makes the
        # read and the insert atomic with respect to other writers.
        conn.isolation_level = None
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute("SELECT MAX(id) FROM audit_log")
            max_id = cursor.fetchone()[0] or 0
            new_id = max_id + 1

            prev_hash = self._get_last_hash(conn)
            fields = _row_fields(
                tool_name, args_hash, target_id, result_status, payload_json, principal
            )
            row_hash = _compute_row_hash(key, prev_hash, new_id, ts, fields)

            conn.execute(
                """
                INSERT INTO audit_log (id, timestamp, tool_name, tool_args_hash,
                    target_id, result_status, prev_hash, row_hash, payload_json,
                    principal)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id,
                    ts,
                    tool_name,
                    args_hash,
                    target_id,
                    result_status,
                    prev_hash,
                    row_hash,
                    payload_json,
                    principal,
                ),
            )
            conn.execute("COMMIT")
        except sqlite3.IntegrityError:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            conn.close()
            # Another writer took the id between the read and the insert.
            # The lock makes this unlikely, but a lost audit row is not
            # acceptable, so it is retried once.
            return self.insert(
                tool_name=tool_name,
                tool_args=tool_args,
                result_status=result_status,
                target_id=target_id,
                payload=payload,
                principal=principal,
                timestamp=ts,
            )
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
        finally:
            with contextlib.suppress(sqlite3.Error):
                conn.close()

        _write_anchor(new_id, row_hash, new_id, key)

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
            principal=principal,
        )

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
                "result_status, prev_hash, row_hash, payload_json, principal "
                "FROM audit_log ORDER BY id ASC"
            ).fetchall()

            if not rows:
                anchor = _read_anchor()
                if anchor and int(anchor.get("count", 0)) > 0:
                    return AuditVerifyResult(
                        valid=False,
                        total_rows=0,
                        error_message="audit log is empty but the anchor recorded "
                        f"{anchor.get('count')} entries: the log was truncated",
                    )
                return AuditVerifyResult(valid=True, total_rows=0)

            prev_hash = "0"
            for row in rows:
                (
                    row_id,
                    ts,
                    tool_name,
                    args_hash,
                    target_id,
                    result_status,
                    stored_prev,
                    stored_hash,
                    payload,
                    principal,
                ) = row

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
                expected_hash = _compute_row_hash(
                    key,
                    prev_hash,
                    row_id,
                    ts,
                    _row_fields(tool_name, args_hash, target_id, result_status, payload, principal),
                )

                if not hmac.compare_digest(stored_hash, expected_hash):
                    return AuditVerifyResult(
                        valid=False,
                        total_rows=len(rows),
                        first_bad_row=row_id,
                        error_message=f"row_hash mismatch at row {row_id}: "
                        f"expected {expected_hash}, got {stored_hash}",
                    )

                prev_hash = stored_hash

            anchor = _read_anchor()
            if anchor is not None:
                anchor_id: Any = anchor.get("last_id")
                anchor_hash: Any = anchor.get("last_hash")
                anchor_count: Any = anchor.get("count")
                # The anchor is signed with the chain key: rewriting it
                # to match a truncated database needs the key.
                anchor_mac = anchor.get("mac")
                if anchor_mac is not None:
                    expected_mac = _anchor_mac(
                        key, int(anchor_id or 0), str(anchor_hash or ""), int(anchor_count or 0)
                    )
                    if not hmac.compare_digest(str(anchor_mac), expected_mac):
                        return AuditVerifyResult(
                            valid=False,
                            total_rows=len(rows),
                            error_message="anchor signature mismatch: the anchor was "
                            "rewritten to match a modified audit log",
                        )
                if anchor_count is not None and int(anchor_count) != len(rows):
                    return AuditVerifyResult(
                        valid=False,
                        total_rows=len(rows),
                        error_message=f"entry count mismatch: anchor recorded "
                        f"{anchor_count}, database holds {len(rows)}; "
                        "entries were added or removed outside the audit writer",
                    )
                last_id, last_hash = rows[-1][0], rows[-1][7]
                if anchor_id is not None and int(anchor_id) != int(last_id):
                    return AuditVerifyResult(
                        valid=False,
                        total_rows=len(rows),
                        error_message=f"tail anchor mismatch: expected last id "
                        f"{anchor_id}, found {last_id}; the newest entries were removed",
                    )
                if anchor_hash is not None and not hmac.compare_digest(
                    str(anchor_hash), str(last_hash)
                ):
                    return AuditVerifyResult(
                        valid=False,
                        total_rows=len(rows),
                        error_message="tail anchor mismatch: the newest entries were removed",
                    )

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
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
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
