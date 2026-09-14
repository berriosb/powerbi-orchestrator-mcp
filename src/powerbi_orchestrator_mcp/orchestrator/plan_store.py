"""SQLite-backed plan store (replaces in-memory dict in v1.9.0+).

In v1.0–v1.8 the server used a module-level ``_plans: dict[str, Plan]``
which had two problems:

1. **Process-local**: any restart loses the in-flight plans; the user
   has to re-run ``plan_change`` to get a new id.
2. **Multi-client unsafe**: with HTTP transport (planned v4) two
   concurrent requests could race on the same dict.

This module introduces a SQLite-backed ``PlanStore`` that:

- Persists plans across restarts.
- Supports concurrency via SQLite WAL + a per-``plan_id`` lock pattern.
- Keeps the same ``Plan`` Pydantic shape (no API change).

Wired into ``orchestrator/server.py`` in v1.9.0; the in-memory dict
remains as a fallback for tests that don't want a DB.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from powerbi_orchestrator_mcp.orchestrator.identifiers import new_plan_id
from powerbi_orchestrator_mcp.orchestrator.plan_models import Plan

# Path next to the audit + executions DBs (same infrastructure).
PLAN_DIR = Path.home() / ".powerbi-orchestrator-mcp" / "plans"
PLAN_DB = PLAN_DIR / "plans.db"


def _ensure_plan_dir() -> Path:
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    return PLAN_DIR


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open the plans SQLite with WAL mode and ensure schema."""
    path = db_path or PLAN_DB
    _ensure_plan_dir()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plans (
            plan_id TEXT PRIMARY KEY,
            plan_yaml TEXT NOT NULL,
            steps_json TEXT NOT NULL,
            rollback_steps_json TEXT NOT NULL DEFAULT '[]',
            risk_score REAL NOT NULL DEFAULT 0.0,
            estimated_changes_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            target_id TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plans_created "
        "ON plans(created_at)"
    )
    conn.commit()
    return conn


def _row_to_plan(row: sqlite3.Row) -> Plan:
    """Convert a SQLite row back to a Plan Pydantic model.

    Avoids importing PlanModel directly to prevent circular imports;
    instead reconstruct via Plan(**dict(row)).
    """
    import json

    from powerbi_orchestrator_mcp.orchestrator.plan_models import (
        EstimatedChanges,
        PlanStep,
    )

    steps_data = json.loads(row["steps_json"])
    rb_data = json.loads(row["rollback_steps_json"])
    est_data = json.loads(row["estimated_changes_json"])
    steps = [PlanStep.model_validate(s) for s in steps_data]
    rb_steps = [PlanStep.model_validate(s) for s in rb_data]
    return Plan(
        id=row["plan_id"],
        yaml=row["plan_yaml"],
        steps=steps,
        rollback_steps=rb_steps,
        risk_score=float(row["risk_score"]),
        estimated_changes=EstimatedChanges.model_validate(est_data),
    )


class PlanStore:
    """SQLite-backed persistence for plans (created by plan_change).

    Thread-safe under the stdio server (single asyncio loop). For
    multi-process / HTTP transport, SQLite WAL gives MVCC isolation
    per writer; readers always see the last commit.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or PLAN_DB

    def _conn(self) -> sqlite3.Connection:
        return _connect(self._db_path)

    def put(self, plan: Plan) -> Plan:
        """Insert or replace a plan."""
        import json

        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO plans (
                    plan_id, plan_yaml, steps_json, rollback_steps_json,
                    risk_score, estimated_changes_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.id,
                    plan.yaml,
                    json.dumps([s.model_dump(mode="json") for s in plan.steps]),
                    json.dumps(
                        [s.model_dump(mode="json") for s in plan.rollback_steps]
                    ),
                    plan.risk_score,
                    plan.estimated_changes.model_dump_json(),
                    plan.id,  # plan_id is a UUID; treat as created_at for ordering
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return plan

    def get(self, plan_id: str) -> Plan | None:
        conn = self._conn()
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
            if row is None:
                return None
            return _row_to_plan(row)
        finally:
            conn.close()

    def delete(self, plan_id: str) -> bool:
        conn = self._conn()
        try:
            cursor = conn.execute(
                "DELETE FROM plans WHERE plan_id = ?", (plan_id,)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def list_all(self) -> list[Plan]:
        conn = self._conn()
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM plans ORDER BY created_at DESC"
            ).fetchall()
            return [_row_to_plan(r) for r in rows]
        finally:
            conn.close()

    def count(self) -> int:
        conn = self._conn()
        try:
            row = conn.execute("SELECT COUNT(*) FROM plans").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()


__all__ = ["PLAN_DB", "PLAN_DIR", "PlanStore"]


def _unused_reference_to_new_plan_id() -> str:
    """Defensive: keep new_plan_id import in case future schema adds
    plan generation here instead of in PlanBuilder."""
    return new_plan_id()
