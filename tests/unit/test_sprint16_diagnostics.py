"""Sprint 16: tests for the new diagnostics + persistence code.

Covers:
- ``powerbi_health`` tool (engine detection + counts + remediation hints).
- ``PlanStore`` (SQLite-backed plans; survives restarts).
- ``count_entries`` on the audit log.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.orchestrator.audit import AuditLog, count_entries
from powerbi_orchestrator_mcp.orchestrator.plan_models import Plan, PlanStep
from powerbi_orchestrator_mcp.orchestrator.plan_store import PLAN_DB, PlanStore
from powerbi_orchestrator_mcp.tools.powerbi_health import powerbi_health


def _make_plan(plan_id: str = "test-plan-1") -> Plan:
    return Plan(
        id=plan_id,
        yaml="id: test-plan-1\nsteps: []\n",
        steps=[
            PlanStep(
                id="s1",
                engine="report",
                action="add_page",
                args={"page_name": "Test"},
            )
        ],
        rollback_steps=[],
        risk_score=0.1,
    )


# ---------------------------------------------------------------------------
# powerbi_health
# ---------------------------------------------------------------------------


class TestPowerbiHealth:
    async def test_returns_all_sections(self, tmp_path: Path) -> None:
        result = await powerbi_health()
        for key in (
            "server_version",
            "uptime_seconds",
            "checks",
            "engines",
            "plan_store_count",
            "plan_store_path",
            "execution_count",
            "execution_path",
            "audit_entries",
            "audit_path",
        ):
            assert key in result

    async def test_engine_details_included_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _name: None)
        result = await powerbi_health()
        # When no engines are on PATH, details include reason_unavailable.
        for engine_info in result["engines"].values():
            assert "available" in engine_info
            assert "reason_unavailable" in engine_info

    async def test_engine_details_excluded_when_omitted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _name: None)
        result = await powerbi_health(include_engine_details=False)
        assert result["engines"] == {}

    async def test_remediation_hints_for_missing_engines(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _name: None)
        result = await powerbi_health()
        # Should include install hints for at least one known engine.
        assert any("npx" in r or "TabularEditor" in r for r in result["remediation"])

    async def test_audit_entries_count_present(self) -> None:
        result = await powerbi_health()
        assert isinstance(result["audit_entries"], int)
        assert result["audit_entries"] >= 0

    async def test_check_count_matches_known_engines(self) -> None:
        result = await powerbi_health()
        # 5 known engines + plan_store + execution_store + audit_log = 8.
        assert len(result["checks"]) >= 8


# ---------------------------------------------------------------------------
# PlanStore
# ---------------------------------------------------------------------------


class TestPlanStore:
    def test_put_and_get_roundtrip(self, tmp_path: Path) -> None:
        store = PlanStore(db_path=tmp_path / "plans.db")
        plan = _make_plan()
        store.put(plan)
        loaded = store.get(plan.id)
        assert loaded is not None
        assert loaded.id == plan.id
        assert loaded.yaml == plan.yaml
        assert len(loaded.steps) == 1
        assert loaded.steps[0].action == "add_page"

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        store = PlanStore(db_path=tmp_path / "plans.db")
        assert store.get("nonexistent") is None

    def test_count_zero_initially(self, tmp_path: Path) -> None:
        store = PlanStore(db_path=tmp_path / "plans.db")
        assert store.count() == 0

    def test_count_after_put(self, tmp_path: Path) -> None:
        store = PlanStore(db_path=tmp_path / "plans.db")
        store.put(_make_plan("p1"))
        store.put(_make_plan("p2"))
        assert store.count() == 2

    def test_delete_returns_true_when_present(
        self, tmp_path: Path
    ) -> None:
        store = PlanStore(db_path=tmp_path / "plans.db")
        store.put(_make_plan())
        assert store.delete("test-plan-1") is True
        assert store.count() == 0

    def test_delete_returns_false_when_absent(
        self, tmp_path: Path
    ) -> None:
        store = PlanStore(db_path=tmp_path / "plans.db")
        assert store.delete("ghost") is False

    def test_put_overwrites_existing(self, tmp_path: Path) -> None:
        store = PlanStore(db_path=tmp_path / "plans.db")
        store.put(_make_plan())
        modified = _make_plan()
        modified.yaml = "id: test-plan-1\nsteps: []\n# updated"
        store.put(modified)
        loaded = store.get("test-plan-1")
        assert loaded is not None
        assert "updated" in loaded.yaml

    def test_list_all_returns_in_reverse_chrono(
        self, tmp_path: Path
    ) -> None:
        store = PlanStore(db_path=tmp_path / "plans.db")
        store.put(_make_plan("p1"))
        store.put(_make_plan("p2"))
        store.put(_make_plan("p3"))
        all_plans = store.list_all()
        assert [p.id for p in all_plans] == ["p3", "p2", "p1"]

    def test_persistence_across_instances(
        self, tmp_path: Path
    ) -> None:
        db = tmp_path / "plans.db"
        s1 = PlanStore(db_path=db)
        s1.put(_make_plan())
        # New instance reading from the same DB sees the plan.
        s2 = PlanStore(db_path=db)
        loaded = s2.get("test-plan-1")
        assert loaded is not None


# ---------------------------------------------------------------------------
# audit count_entries
# ---------------------------------------------------------------------------


class TestCountAuditEntries:
    def test_zero_when_db_missing(self, tmp_path: Path) -> None:
        assert count_entries(db_path=tmp_path / "missing.db") == 0

    def test_zero_on_empty_db(self, tmp_path: Path) -> None:
        # Create an empty DB by inserting and removing one row.
        log = AuditLog(db_path=tmp_path / "audit.db")
        log.insert(
            tool_name="test",
            tool_args={"x": 1},
            result_status="success",
        )
        # Remove it.
        import sqlite3

        with sqlite3.connect(str(tmp_path / "audit.db")) as conn:
            conn.execute("DELETE FROM audit_log")
            conn.commit()
        assert count_entries(db_path=tmp_path / "audit.db") == 0

    def test_counts_inserts(self, tmp_path: Path) -> None:
        log = AuditLog(db_path=tmp_path / "audit.db")
        log.insert(tool_name="t1", tool_args={"x": 1}, result_status="ok")
        log.insert(tool_name="t2", tool_args={"x": 2}, result_status="ok")
        log.insert(tool_name="t3", tool_args={"x": 3}, result_status="fail")
        assert count_entries(db_path=tmp_path / "audit.db") == 3


# ---------------------------------------------------------------------------
# Default path constants (used by tools/powerbi_health)
# ---------------------------------------------------------------------------


class TestPathConstants:
    def test_audit_db_is_under_user_home(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.audit import AUDIT_DB

        assert str(AUDIT_DB).startswith(str(Path.home()))

    def test_plan_db_is_under_user_home(self) -> None:
        assert str(PLAN_DB).startswith(str(Path.home()))
