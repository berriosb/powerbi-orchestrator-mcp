"""Tests for orchestrator.plan_executions — state machine + crash recovery (spec §2.8)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.orchestrator import identifiers as ids
from powerbi_orchestrator_mcp.orchestrator.plan_executions import (
    ORPHAN_HEARTBEAT_CUTOFF_S,
    OrphanReconciliation,
    PlanExecution,
    PlanExecutionState,
    PlanExecutionStore,
    reconcile_orphan_executions_on_boot,
)


@pytest.fixture()
def store(tmp_path: Path) -> PlanExecutionStore:
    return PlanExecutionStore(db_path=tmp_path / "executions.db")


def _make_execution(
    *,
    state: PlanExecutionState = PlanExecutionState.IN_PROGRESS,
    last_heartbeat_at: str | None = None,
    current_step_index: int | None = 0,
) -> PlanExecution:
    ts = last_heartbeat_at or datetime.now(UTC).isoformat()
    return PlanExecution(
        execution_id=ids.new_execution_id(),
        plan_id=ids.new_plan_id(),
        plan_yaml="steps: []\n",
        state=state,
        current_step_index=current_step_index,
        started_at=ts,
        last_heartbeat_at=ts,
    )


class TestStateMachine:
    def test_create_and_get(self, store: PlanExecutionStore) -> None:
        ex = _make_execution()
        store.create(ex)
        fetched = store.get(ex.execution_id)
        assert fetched is not None
        assert fetched.execution_id == ex.execution_id
        assert fetched.state == PlanExecutionState.IN_PROGRESS

    def test_heartbeat_updates_timestamp(self, store: PlanExecutionStore) -> None:
        ex = _make_execution()
        store.create(ex)
        before = datetime.now(UTC) - timedelta(seconds=10)
        store.update_heartbeat(ex.execution_id, current_step_index=2)
        fetched = store.get(ex.execution_id)
        assert fetched is not None
        assert fetched.current_step_index == 2
        # Heartbeat should be > before timestamp
        fetched_ts = datetime.fromisoformat(fetched.last_heartbeat_at)
        assert fetched_ts >= before

    def test_complete_marks_terminal(self, store: PlanExecutionStore) -> None:
        ex = _make_execution()
        store.create(ex)
        store.complete(
            ex.execution_id,
            result_status="success",
            state=PlanExecutionState.COMPLETED,
        )
        fetched = store.get(ex.execution_id)
        assert fetched is not None
        assert fetched.state == PlanExecutionState.COMPLETED
        assert fetched.result_status == "success"
        assert fetched.completed_at is not None

    def test_list_by_state(self, store: PlanExecutionStore) -> None:
        for _ in range(3):
            store.create(_make_execution(state=PlanExecutionState.IN_PROGRESS))
        store.create(_make_execution(state=PlanExecutionState.COMPLETED))
        in_progress = store.list_by_state(PlanExecutionState.IN_PROGRESS)
        assert len(in_progress) == 3

    def test_duplicate_execution_id_rejected(self, store: PlanExecutionStore) -> None:
        ex = _make_execution()
        store.create(ex)
        with pytest.raises(sqlite3.IntegrityError):
            store.create(ex)


class TestCrashRecovery:
    def test_find_orphans_no_results_when_fresh(
        self, store: PlanExecutionStore
    ) -> None:
        ex = _make_execution()  # heartbeat = now
        store.create(ex)
        orphans = store.find_orphans()
        assert orphans == []

    def test_find_orphans_detects_old_in_progress(
        self, store: PlanExecutionStore
    ) -> None:
        old_heartbeat = (
            datetime.now(UTC) - timedelta(seconds=ORPHAN_HEARTBEAT_CUTOFF_S * 2)
        ).isoformat()
        ex = _make_execution(last_heartbeat_at=old_heartbeat)
        store.create(ex)
        orphans = store.find_orphans()
        assert len(orphans) == 1
        assert orphans[0].execution_id == ex.execution_id
        assert orphans[0].state == PlanExecutionState.IN_PROGRESS
        assert orphans[0].age_seconds > ORPHAN_HEARTBEAT_CUTOFF_S

    def test_find_orphans_ignores_completed(self, store: PlanExecutionStore) -> None:
        old = (
            datetime.now(UTC) - timedelta(hours=1)
        ).isoformat()
        ex = _make_execution(
            state=PlanExecutionState.COMPLETED, last_heartbeat_at=old
        )
        store.create(ex)
        orphans = store.find_orphans()
        assert orphans == []

    def test_find_orphans_detects_partial(self, store: PlanExecutionStore) -> None:
        old = (
            datetime.now(UTC) - timedelta(hours=1)
        ).isoformat()
        ex = _make_execution(state=PlanExecutionState.PARTIAL, last_heartbeat_at=old)
        store.create(ex)
        orphans = store.find_orphans()
        assert len(orphans) == 1

    def test_mark_orphaned_transitions_state(self, store: PlanExecutionStore) -> None:
        old = (
            datetime.now(UTC) - timedelta(hours=1)
        ).isoformat()
        ex = _make_execution(last_heartbeat_at=old)
        store.create(ex)
        result = store.mark_orphaned(ex.execution_id)
        assert result is True
        fetched = store.get(ex.execution_id)
        assert fetched is not None
        assert fetched.state == PlanExecutionState.ORPHANED

    def test_mark_orphaned_no_op_on_completed(self, store: PlanExecutionStore) -> None:
        ex = _make_execution(state=PlanExecutionState.COMPLETED)
        store.create(ex)
        result = store.mark_orphaned(ex.execution_id)
        assert result is False
        fetched = store.get(ex.execution_id)
        assert fetched is not None
        assert fetched.state == PlanExecutionState.COMPLETED


class TestReconcileOnBoot:
    def test_reconcile_marks_old_in_progress_as_orphaned(
        self, store: PlanExecutionStore
    ) -> None:
        old = (
            datetime.now(UTC) - timedelta(hours=1)
        ).isoformat()
        ex = _make_execution(last_heartbeat_at=old)
        store.create(ex)

        orphans = reconcile_orphan_executions_on_boot(store=store)
        assert len(orphans) == 1
        assert orphans[0].execution_id == ex.execution_id

        fetched = store.get(ex.execution_id)
        assert fetched is not None
        assert fetched.state == PlanExecutionState.ORPHANED

    def test_reconcile_no_op_when_all_fresh(self, store: PlanExecutionStore) -> None:
        ex = _make_execution()  # heartbeat = now
        store.create(ex)
        orphans = reconcile_orphan_executions_on_boot(store=store)
        assert orphans == []
        fetched = store.get(ex.execution_id)
        assert fetched is not None
        assert fetched.state == PlanExecutionState.IN_PROGRESS

    def test_reconcile_returns_orphan_metadata(self, store: PlanExecutionStore) -> None:
        old = (
            datetime.now(UTC) - timedelta(hours=2)
        ).isoformat()
        ex = _make_execution(
            state=PlanExecutionState.IN_PROGRESS,
            last_heartbeat_at=old,
            current_step_index=3,
        )
        store.create(ex)

        orphans = reconcile_orphan_executions_on_boot(store=store)
        assert len(orphans) == 1
        o = orphans[0]
        assert isinstance(o, OrphanReconciliation)
        assert o.execution_id == ex.execution_id
        assert o.current_step_index == 3
        assert o.age_seconds >= 60
