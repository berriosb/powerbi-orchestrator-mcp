"""Tests for orchestrator.server — real connect_target/plan_change/apply_plan.

These tests exercise the FastMCP tool functions end-to-end, including:
- engine detection (mocked via PATH manipulation)
- session persistence via SessionStore (mocked via tmp_path monkeypatch)
- plan execution via the StepExecutor registry (default + scripted)
- plan execution state tracking via PlanExecutionStore (mocked via tmp_path)

Run with: ``pytest tests/unit/test_server.py``
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from powerbi_orchestrator_mcp.orchestrator import server as srv
from powerbi_orchestrator_mcp.orchestrator.plan_models import PlanStep
from powerbi_orchestrator_mcp.orchestrator.rollback import StepOutcome
from powerbi_orchestrator_mcp.orchestrator.server import (
    ApplyResult,
    ConnectResult,
    EstimatedChanges,
    PlanResult,
    apply_plan,
    connect_target,
    mcp,
    plan_change,
)
from powerbi_orchestrator_mcp.orchestrator.step_executor import (
    StepExecutorRegistry,
    get_default_registry,
    reset_default_registry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reset all module-level + filesystem state for each test."""
    monkeypatch.setattr("powerbi_orchestrator_mcp.orchestrator.context.SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr("powerbi_orchestrator_mcp.orchestrator.plan_executions.EXEC_DIR", tmp_path / "executions")
    monkeypatch.setattr("powerbi_orchestrator_mcp.orchestrator.audit.AUDIT_DIR", tmp_path / "audit")
    srv._reset_server_state()
    reset_default_registry()


@pytest.fixture()
def pbip_dir(tmp_path: Path) -> Path:
    """Create a minimal PBIP folder for connect_target validation."""
    p = tmp_path / "sales.pbip"
    p.mkdir()
    return p


@pytest.fixture()
def isolated_registry() -> StepExecutorRegistry:
    """A fresh registry pre-loaded with only DryRunExecutor."""
    return get_default_registry()


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestModels:
    """Pydantic output schemas."""

    def test_engine_status_defaults(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.context import EngineStatus as CtxEngineStatus

        status = CtxEngineStatus(name="te", available=True)
        assert status.available is True
        assert status.version is None
        assert status.reason_unavailable is None

    def test_engine_status_unavailable(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.context import EngineStatus as CtxEngineStatus

        status = CtxEngineStatus(
            name="te", available=False, reason_unavailable="not installed"
        )
        assert status.available is False
        assert status.reason_unavailable == "not installed"

    def test_connect_result_defaults(self) -> None:
        result = ConnectResult(session_id="s1", engines_available={})
        assert result.session_id == "s1"
        assert result.engines_available == {}
        assert result.warnings == []

    def test_estimated_changes_defaults(self) -> None:
        ec = EstimatedChanges()
        assert ec.files_affected == 0
        assert ec.rollback_complexity == "trivial"

    def test_plan_result_risk_score_bounds(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PlanResult(plan_id="p1", plan_yaml="y", risk_score=1.5)
        with pytest.raises(ValidationError):
            PlanResult(plan_id="p1", plan_yaml="y", risk_score=-0.1)

    def test_apply_result_defaults(self) -> None:
        result = ApplyResult()
        assert result.result == "success"
        assert result.executed_steps == []
        assert result.failed_step is None


class TestFastMCP:
    """FastMCP instance + tool registration."""

    def test_mcp_instance_exists(self) -> None:
        assert mcp is not None
        assert mcp.name == "powerbi-orchestrator-mcp"

    def test_tools_registered(self) -> None:
        import asyncio

        async def _list() -> set[str]:
            tools = await mcp.list_tools()  # type: ignore[attr-defined]
            return {tool.name for tool in tools}

        tool_names = asyncio.run(_list())
        assert "connect_target" in tool_names
        assert "plan_change" in tool_names
        assert "apply_plan" in tool_names


# ---------------------------------------------------------------------------
# connect_target tests
# ---------------------------------------------------------------------------


class TestConnectTarget:
    @pytest.mark.asyncio
    async def test_invalid_target_type_raises(self) -> None:
        with pytest.raises(ValueError, match="target_type must be one of"):
            await connect_target(target_type="garbage", target_ref="x")

    @pytest.mark.asyncio
    async def test_invalid_auth_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="auth_mode must be one of"):
            await connect_target(
                target_type="pbip_folder",
                target_ref="./x.pbip",
                auth_mode="magic",
            )

    @pytest.mark.asyncio
    async def test_service_principal_requires_tenant(self) -> None:
        with pytest.raises(ValueError, match="tenant_id is required"):
            await connect_target(
                target_type="fabric_workspace",
                target_ref="ws-abc",
                auth_mode="service_principal",
            )

    @pytest.mark.asyncio
    async def test_returns_uuid_session_id(self, pbip_dir: Path) -> None:
        result = await connect_target(
            target_type="pbip_folder",
            target_ref=str(pbip_dir),
        )
        assert isinstance(result, ConnectResult)
        assert len(result.session_id) == 32  # uuid hex

    @pytest.mark.asyncio
    async def test_detects_all_known_engines(
        self, pbip_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force all engines to be unavailable by removing their PATH entries.
        monkeypatch.setattr(shutil, "which", lambda _name: None)

        result = await connect_target(
            target_type="pbip_folder", target_ref=str(pbip_dir)
        )
        # All 5 engines should be reported as unavailable.
        for engine in ("powerbi-modeling-mcp", "te", "dscmd", "pbip-validator", "superbi-mcp"):
            assert engine in result.engines_available
            assert result.engines_available[engine].available is False
            assert result.engines_available[engine].reason_unavailable is not None

    @pytest.mark.asyncio
    async def test_warning_when_engines_missing(self, pbip_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        result = await connect_target(
            target_type="pbip_folder", target_ref=str(pbip_dir)
        )
        assert any("unavailable" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_warning_for_nonexistent_path(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist"
        result = await connect_target(
            target_type="pbip_folder", target_ref=str(missing)
        )
        assert any("does not exist" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_persists_session(self, pbip_dir: Path) -> None:
        from powerbi_orchestrator_mcp.orchestrator.context import SessionStore

        result = await connect_target(
            target_type="pbip_folder", target_ref=str(pbip_dir)
        )
        store = SessionStore()
        session = store.get(result.session_id)
        assert session is not None
        assert session.target is not None
        assert session.target.target_type == "pbip_folder"


# ---------------------------------------------------------------------------
# plan_change tests
# ---------------------------------------------------------------------------


class TestPlanChange:
    @pytest.mark.asyncio
    async def test_unknown_template_raises(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.plan_models import (
            UnknownTemplateError,
        )

        with pytest.raises(UnknownTemplateError):
            await plan_change(intent="nonexistent_template")

    @pytest.mark.asyncio
    async def test_safe_rename_dispatch(self) -> None:
        result = await plan_change(
            intent="safe_rename",
            options={
                "old_path": "T[A]",
                "new_path": "T[B]",
                "scope": "report_bindings",
            },
        )
        assert isinstance(result, PlanResult)
        assert result.plan_id.startswith("plan_")
        assert "steps:" in result.plan_yaml
        assert len(result.steps) == 4

    @pytest.mark.asyncio
    async def test_audit_dispatch(self) -> None:
        result = await plan_change(
            intent="audit",
            options={"target": "pbip:./x", "checks": ["bpa", "wcag"]},
        )
        assert result.plan_id.startswith("plan_")
        assert len(result.steps) == 3

    @pytest.mark.asyncio
    async def test_missing_required_args_raises(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.plan_models import (
            PlanValidationError,
        )

        with pytest.raises(PlanValidationError):
            await plan_change(intent="safe_rename", options={"old_path": "T[A]"})

    @pytest.mark.asyncio
    async def test_plan_options_extracted_from_options(self) -> None:
        """``options`` dict can contain both template args AND PlanOptions."""
        result = await plan_change(
            intent="audit",
            options={
                "target": "pbip:./x",
                "checks": ["bpa"],
                "max_impact_threshold": 80,
                "dry_run_first": False,
            },
        )
        assert result.plan_id.startswith("plan_")

    @pytest.mark.asyncio
    async def test_invalid_plan_options_raises(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.plan_models import (
            PlanValidationError,
        )

        with pytest.raises(PlanValidationError):
            await plan_change(
                intent="audit",
                options={
                    "target": "x",
                    "checks": ["a"],
                    "max_impact_threshold": "not-an-int",  # not coercible
                },
            )


# ---------------------------------------------------------------------------
# apply_plan tests
# ---------------------------------------------------------------------------


class TestApplyPlan:
    @pytest.mark.asyncio
    async def test_unknown_plan_id_returns_failed(self) -> None:
        result = await apply_plan(plan_id="plan_does_not_exist")
        assert isinstance(result, ApplyResult)
        assert result.result == "failed"
        assert "plan not found" in result.failed_step["error_message"]

    @pytest.mark.asyncio
    async def test_dry_run_succeeds(self) -> None:
        # Create a plan via plan_change.
        plan_res = await plan_change(
            intent="audit",
            options={"target": "x", "checks": ["a"]},
        )
        # Dry-run executes via DryRunExecutor (no real engines registered).
        result = await apply_plan(plan_id=plan_res.plan_id, dry_run=True)
        assert result.result == "success"
        assert len(result.executed_steps) == 3
        assert all(s["success"] for s in result.executed_steps)

    @pytest.mark.asyncio
    async def test_dry_run_with_no_engines_succeeds(self) -> None:
        """Even when no real engines are registered, dry_run works via DryRunExecutor."""
        plan_res = await plan_change(
            intent="audit",
            options={"target": "x", "checks": ["a"]},
        )
        result = await apply_plan(plan_id=plan_res.plan_id, dry_run=False)
        # The audit plan has all `validation` steps; no executor registered.
        # Each step fails with MissingEngineExecutor → rollback kicks in
        # but no rollback_step exists, so we get "failed" status.
        assert result.result == "failed"


class ScriptedEngineExecutor:
    """Test executor that returns scripted outcomes."""

    engine_name = "modeling"

    def __init__(self, script: list[bool]) -> None:
        self._script = list(script)
        self.calls: list[PlanStep] = []

    async def execute_step(self, step: PlanStep) -> StepOutcome:
        self.calls.append(step)
        if not self._script:
            return StepOutcome(success=True)
        ok = self._script.pop(0)
        return StepOutcome(success=ok, error_message=None if ok else "scripted fail")


class TestApplyPlanRollback:
    @pytest.mark.asyncio
    async def test_real_executor_success(self) -> None:
        """Register modeling+report+validation executors that always succeed."""
        modeling = ScriptedEngineExecutor([True, True])
        modeling.engine_name = "modeling"
        report = ScriptedEngineExecutor([True])
        report.engine_name = "report"
        validation = ScriptedEngineExecutor([True])
        validation.engine_name = "validation"
        get_default_registry().register(modeling)
        get_default_registry().register(report)
        get_default_registry().register(validation)

        plan_res = await plan_change(
            intent="safe_rename",
            options={"old_path": "T[A]", "new_path": "T[B]"},
        )
        result = await apply_plan(plan_id=plan_res.plan_id, dry_run=False)
        assert result.result == "success"
        assert all(s["success"] for s in result.executed_steps)

    @pytest.mark.asyncio
    async def test_step_failure_triggers_rollback(self) -> None:
        """Modeling step fails → rollback of modeling step succeeds → 'rolled_back'.

        safe_rename has 4 steps in order:
          s1 create_snapshot (validation)         — no rollback_step
          s2 column.update (modeling)             — HAS rollback_step
          s3 propagate_rename (report)            — HAS rollback_step
          s4 pbip_validate_full (validation)      — no rollback_step

        When s2 (modeling) fails, the rollback engine queues:
          - s2's rollback (modeling-revert) — runs first
          - s1 has no rollback_step → skipped
        Result: rollback succeeds → "rolled_back".
        """
        modeling = ScriptedEngineExecutor([False])
        modeling.engine_name = "modeling"
        report = ScriptedEngineExecutor([True])
        report.engine_name = "report"
        validation = ScriptedEngineExecutor([True, True])
        validation.engine_name = "validation"
        get_default_registry().register(modeling)
        get_default_registry().register(report)
        get_default_registry().register(validation)

        plan_res = await plan_change(
            intent="safe_rename",
            options={"old_path": "T[A]", "new_path": "T[B]"},
        )
        result = await apply_plan(plan_id=plan_res.plan_id, dry_run=False)
        assert result.result == "rolled_back"
        assert result.failed_step is not None
        assert "modeling" in result.failed_step["engine"]
        # The modeling step's rollback ran successfully.
        assert any("ok" in r.get("status", "") for r in result.rollback_steps_executed)


# ---------------------------------------------------------------------------
# State machine integration
# ---------------------------------------------------------------------------


class TestCrashRecoveryOnConnect:
    @pytest.mark.asyncio
    async def test_connect_triggers_reconcile(
        self, pbip_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """reconcile_orphan_executions_on_boot is called inside connect_target."""

        called = {"count": 0}
        original = srv.reconcile_orphan_executions_on_boot

        def spy() -> Any:
            called["count"] += 1
            return original()

        monkeypatch.setattr(srv, "reconcile_orphan_executions_on_boot", spy)
        await connect_target(target_type="pbip_folder", target_ref=str(pbip_dir))
        assert called["count"] == 1


class TestEngineDetectorHelpers:
    def test_get_known_engines(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.engine_detector import (
            get_known_engines,
        )

        engines = get_known_engines()
        assert "powerbi-modeling-mcp" in engines
        assert "te" in engines

    def test_get_engine_timeout_s(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.engine_detector import (
            get_engine_timeout_s,
        )

        assert get_engine_timeout_s("te") == 60

    def test_get_engine_timeout_s_unknown_raises(self) -> None:
        from powerbi_orchestrator_mcp.engines.timeouts import UnknownEngineError
        from powerbi_orchestrator_mcp.orchestrator.engine_detector import (
            get_engine_timeout_s,
        )

        with pytest.raises(UnknownEngineError):
            get_engine_timeout_s("nonexistent")
