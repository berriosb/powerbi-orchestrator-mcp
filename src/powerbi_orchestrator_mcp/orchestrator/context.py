"""Session context - state management for active sessions."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Pydantic models (spec section 2.3)
# ---------------------------------------------------------------------------


class EngineStatus(BaseModel):
    """Status of a single engine."""

    name: str
    available: bool
    version: str | None = None
    reason_unavailable: str | None = None


class UndoEntry(BaseModel):
    """Single entry in the undo stack."""

    step_id: str
    description: str
    snapshot_path: str | None = None


class Target(BaseModel):
    """Connected target reference."""

    target_type: str
    target_ref: str
    auth_mode: str = "interactive"
    tenant_id: str | None = None


class SessionContext(BaseModel):
    """Session state persisted in SQLite WAL (spec section 2.3)."""

    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    target: Target | None = None
    engines_available: dict[str, EngineStatus] = Field(default_factory=dict)
    metadata_cache: dict[str, Any] = Field(default_factory=dict)
    undo_stack: list[UndoEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# SQLite WAL persistence
# ---------------------------------------------------------------------------

SESSIONS_DIR = Path.home() / ".powerbi-orchestrator-mcp" / "sessions"


def _ensure_sessions_dir() -> Path:
    """Create sessions directory if it doesn't exist."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return SESSIONS_DIR


def _get_db_path(session_id: str) -> Path:
    """Get the SQLite database path for a session."""
    return _ensure_sessions_dir() / f"{session_id}.db"


def _init_db(conn: sqlite3.Connection) -> None:
    """Initialize the sessions table with WAL mode."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            target_json TEXT,
            engines_json TEXT NOT NULL DEFAULT '{}',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            undo_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()


def _row_to_context(row: sqlite3.Row) -> SessionContext:
    """Convert a database row to SessionContext."""
    return SessionContext(
        session_id=row["session_id"],
        target=Target.model_validate_json(row["target_json"]) if row["target_json"] else None,
        engines_available={
            k: EngineStatus.model_validate(v)
            for k, v in json.loads(row["engines_json"]).items()
        },
        metadata_cache=json.loads(row["metadata_json"]),
        undo_stack=[UndoEntry.model_validate(e) for e in json.loads(row["undo_json"])],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class SessionStore:
    """SQLite WAL-backed session persistence."""

    def create(self, context: SessionContext | None = None) -> SessionContext:
        """Create a new session and persist it."""
        ctx = context or SessionContext()
        db_path = _get_db_path(ctx.session_id)
        conn = sqlite3.connect(str(db_path))
        try:
            _init_db(conn)
            conn.execute(
                """
                INSERT INTO sessions (session_id, target_json, engines_json, metadata_json, undo_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    ctx.session_id,
                    ctx.target.model_dump_json() if ctx.target else None,
                    json.dumps({k: v.model_dump() for k, v in ctx.engines_available.items()}),
                    json.dumps(ctx.metadata_cache),
                    json.dumps([e.model_dump() for e in ctx.undo_stack]),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return ctx

    def get(self, session_id: str) -> SessionContext | None:
        """Retrieve a session by ID."""
        db_path = _get_db_path(session_id)
        if not db_path.exists():
            return None
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            _init_db(conn)
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            return _row_to_context(row)
        finally:
            conn.close()

    def update(self, context: SessionContext) -> None:
        """Update an existing session."""
        db_path = _get_db_path(context.session_id)
        conn = sqlite3.connect(str(db_path))
        try:
            _init_db(conn)
            conn.execute(
                """
                UPDATE sessions
                SET target_json = ?, engines_json = ?, metadata_json = ?, undo_json = ?,
                    updated_at = datetime('now')
                WHERE session_id = ?
                """,
                (
                    context.target.model_dump_json() if context.target else None,
                    json.dumps({k: v.model_dump() for k, v in context.engines_available.items()}),
                    json.dumps(context.metadata_cache),
                    json.dumps([e.model_dump() for e in context.undo_stack]),
                    context.session_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def delete(self, session_id: str) -> bool:
        """Delete a session. Returns True if it existed."""
        db_path = _get_db_path(session_id)
        if not db_path.exists():
            return False
        conn = sqlite3.connect(str(db_path))
        try:
            _init_db(conn)
            cursor = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def list_sessions(self) -> list[SessionContext]:
        """List all persisted sessions."""
        _ensure_sessions_dir()
        sessions: list[SessionContext] = []
        for db_file in SESSIONS_DIR.glob("*.db"):
            conn = sqlite3.connect(str(db_file))
            conn.row_factory = sqlite3.Row
            try:
                _init_db(conn)
                rows = conn.execute("SELECT * FROM sessions").fetchall()
                for row in rows:
                    sessions.append(_row_to_context(row))
            finally:
                conn.close()
        return sessions
