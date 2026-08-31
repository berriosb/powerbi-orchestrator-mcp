"""Plan execution state machine and crash recovery.

Implements spec section 2.8 from specs/01-orchestrator.md.

The server process can die at any moment: Ctrl-C from a developer, OOM
in CI, ``kill -9`` from a runner, segfault in a subprocess engine.
If it dies between step 3 of 5 and its rollback, the next ``apply_plan``
must detect the partially-applied plan and offer reconciliation.

State machine (per spec §2.8):

    PENDING ──apply_plan──> IN_PROGRESS ──all ok──> COMPLETED
                                │
                                ├──step fail + rollback ok──> ROLLED_BACK
                                ├──step fail + rollback fail──> PARTIAL
                                └──process dies──> ORPHANED (on next boot)

Design decision (2026-08-26): detect + elicit on next ``apply_plan``.
NOT auto-rollback (too risky in interactive mode).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.orchestrator.identifiers import (
    new_execution_id,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Per spec §2.8: executions with heartbeat older than this are orphaned.
ORPHAN_HEARTBEAT_CUTOFF_S: int = 60

# Path next to the audit log so we share infrastructure.
EXEC_DIR = Path.home() / ".powerbi-orchestrator-mcp" / "executions"
EXEC_DB = EXEC_DIR / "executions.db"

# Snapshot retention per spec §2.8 rule 3.
SNAPSHOT_GC_DAYS: int = 30


# ---------------------------------------------------------------------------
# Enum + Pydantic models
# ---------------------------------------------------------------------------


class PlanExecutionState(str, Enum):  # noqa: UP042
    """State of a plan execution (spec §2.8)."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"
    PARTIAL = "partial"
    ORPHANED = "orphaned"


class PlanExecution(BaseModel):
    """A single execution of a plan (spec §2.8)."""

    execution_id: str = Field(default_factory=new_execution_id)
    plan_id: str
    plan_yaml: str
    state: PlanExecutionState = PlanExecutionState.PENDING
    current_step_index: int | None = None
    started_at: str
    last_heartbeat_at: str
    completed_at: str | None = None
    result_status: str | None = None
    target_id: str | None = None


class OrphanReconciliation(BaseModel):
    """Summary of a single orphan execution detected at boot (spec §2.8)."""

    execution_id: str
    plan_id: str
    state: PlanExecutionState
    current_step_index: int | None
    started_at: str
    last_heartbeat_at: str
    age_seconds: int


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


def _ensure_exec_dir() -> Path:
    EXEC_DIR.mkdir(parents=True, exist_ok=True)
    return EXEC_DIR


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open the executions SQLite with WAL mode and ensure schema."""
    path = db_path or EXEC_DB
    _ensure_exec_dir()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plan_executions (
            execution_id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL,
            plan_yaml TEXT NOT NULL,
            state TEXT NOT NULL,
            current_step_index INTEGER,
            started_at TEXT NOT NULL,
            last_heartbeat_at TEXT NOT NULL,
            completed_at TEXT,
            result_status TEXT,
            target_id TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plan_executions_state "
        "ON plan_executions(state)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plan_executions_heartbeat "
        "ON plan_executions(last_heartbeat_at)"
    )
    conn.commit()
    return conn


def _row_to_execution(row: sqlite3.Row) -> PlanExecution:
    return PlanExecution(
        execution_id=row["execution_id"],
        plan_id=row["plan_id"],
        plan_yaml=row["plan_yaml"],
        state=PlanExecutionState(row["state"]),
        current_step_index=row["current_step_index"],
        started_at=row["started_at"],
        last_heartbeat_at=row["last_heartbeat_at"],
        completed_at=row["completed_at"],
        result_status=row["result_status"],
        target_id=row["target_id"],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class PlanExecutionStore:
    """SQLite-backed persistence for plan executions."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or EXEC_DB

    def _conn(self) -> sqlite3.Connection:
        return _connect(self._db_path)

    def create(self, execution: PlanExecution) -> PlanExecution:
        """Insert a new execution. Raises if execution_id already exists."""
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO plan_executions (
                    execution_id, plan_id, plan_yaml, state,
                    current_step_index, started_at, last_heartbeat_at,
                    completed_at, result_status, target_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution.execution_id,
                    execution.plan_id,
                    execution.plan_yaml,
                    execution.state.value,
                    execution.current_step_index,
                    execution.started_at,
                    execution.last_heartbeat_at,
                    execution.completed_at,
                    execution.result_status,
                    execution.target_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return execution

    def get(self, execution_id: str) -> PlanExecution | None:
        conn = self._conn()
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM plan_executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            if row is None:
                return None
            return _row_to_execution(row)
        finally:
            conn.close()

    def update_heartbeat(
        self,
        execution_id: str,
        *,
        current_step_index: int | None = None,
        state: PlanExecutionState | None = None,
    ) -> None:
        """Touch last_heartbeat_at to NOW; optionally bump state / step.

        Called by the PlanExecutor before each step and after each step
        per spec §2.8 "Heartbeat protocol".
        """
        now = datetime.now(UTC).isoformat()
        conn = self._conn()
        try:
            if current_step_index is not None and state is not None:
                conn.execute(
                    """
                    UPDATE plan_executions
                    SET last_heartbeat_at = ?, current_step_index = ?, state = ?
                    WHERE execution_id = ?
                    """,
                    (now, current_step_index, state.value, execution_id),
                )
            elif current_step_index is not None:
                conn.execute(
                    """
                    UPDATE plan_executions
                    SET last_heartbeat_at = ?, current_step_index = ?
                    WHERE execution_id = ?
                    """,
                    (now, current_step_index, execution_id),
                )
            elif state is not None:
                conn.execute(
                    """
                    UPDATE plan_executions
                    SET last_heartbeat_at = ?, state = ?
                    WHERE execution_id = ?
                    """,
                    (now, state.value, execution_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE plan_executions
                    SET last_heartbeat_at = ?
                    WHERE execution_id = ?
                    """,
                    (now, execution_id),
                )
            conn.commit()
        finally:
            conn.close()

    def complete(
        self,
        execution_id: str,
        result_status: str,
        state: PlanExecutionState,
    ) -> None:
        """Mark execution as terminal (completed | rolled_back | partial)."""
        now = datetime.now(UTC).isoformat()
        conn = self._conn()
        try:
            conn.execute(
                """
                UPDATE plan_executions
                SET state = ?, result_status = ?, completed_at = ?,
                    last_heartbeat_at = ?
                WHERE execution_id = ?
                """,
                (state.value, result_status, now, now, execution_id),
            )
            conn.commit()
        finally:
            conn.close()

    def list_by_state(self, state: PlanExecutionState) -> list[PlanExecution]:
        conn = self._conn()
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM plan_executions WHERE state = ? "
                "ORDER BY started_at ASC",
                (state.value,),
            ).fetchall()
            return [_row_to_execution(r) for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Crash recovery (spec §2.8)
    # ------------------------------------------------------------------

    def find_orphans(
        self,
        *,
        now: datetime | None = None,
        cutoff_seconds: int = ORPHAN_HEARTBEAT_CUTOFF_S,
    ) -> list[OrphanReconciliation]:
        """Find executions that look abandoned (in_progress/partial + old heartbeat)."""
        ref = now or datetime.now(UTC)
        cutoff = ref - timedelta(seconds=cutoff_seconds)
        conn = self._conn()
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM plan_executions
                WHERE state IN ('in_progress', 'partial')
                  AND last_heartbeat_at < ?
                ORDER BY last_heartbeat_at ASC
                """,
                (cutoff.isoformat(),),
            ).fetchall()
            out: list[OrphanReconciliation] = []
            for r in rows:
                try:
                    heartbeat = datetime.fromisoformat(r["last_heartbeat_at"])
                except ValueError:
                    # Bad timestamp format: treat as very old so it's
                    # surfaced for human review.
                    heartbeat = ref - timedelta(days=365)
                if heartbeat.tzinfo is None:
                    heartbeat = heartbeat.replace(tzinfo=UTC)
                age = int((ref - heartbeat).total_seconds())
                out.append(
                    OrphanReconciliation(
                        execution_id=r["execution_id"],
                        plan_id=r["plan_id"],
                        state=PlanExecutionState(r["state"]),
                        current_step_index=r["current_step_index"],
                        started_at=r["started_at"],
                        last_heartbeat_at=r["last_heartbeat_at"],
                        age_seconds=age,
                    )
                )
            return out
        finally:
            conn.close()

    def mark_orphaned(self, execution_id: str) -> bool:
        """Transition an execution to ``orphaned``. Returns True if a row changed."""
        conn = self._conn()
        try:
            cursor = conn.execute(
                """
                UPDATE plan_executions
                SET state = 'orphaned'
                WHERE execution_id = ? AND state IN ('in_progress', 'partial')
                """,
                (execution_id,),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()


def reconcile_orphan_executions_on_boot(
    *,
    store: PlanExecutionStore | None = None,
    cutoff_seconds: int = ORPHAN_HEARTBEAT_CUTOFF_S,
) -> list[OrphanReconciliation]:
    """Detect abandoned executions on server boot (spec §2.8).

    Performs two actions:
    1. Finds executions with state IN ('in_progress', 'partial') and
       heartbeat older than ``cutoff_seconds``.
    2. Transitions each one to state='orphaned'.

    Returns the list of newly-orphaned executions (the caller is
    responsible for triggering the elicitation on the next apply_plan).
    """
    s = store or PlanExecutionStore()
    orphans = s.find_orphans(cutoff_seconds=cutoff_seconds)
    for o in orphans:
        s.mark_orphaned(o.execution_id)
    return orphans
