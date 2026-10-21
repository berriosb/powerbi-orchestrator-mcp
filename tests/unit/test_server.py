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
from powerbi_orchestrator_mcp.orchestrator.elicitation import ElicitationSchema
from powerbi_orchestrator_mcp.orchestrator.plan_models import (
    EstimatedChanges,
    PlanStep,
)
from powerbi_orchestrator_mcp.orchestrator.rollback import StepOutcome
from powerbi_orchestrator_mcp.orchestrator.server import (
    ApplyResult,
    ConnectResult,
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
# The PBIP fixture the audit-plan tests run against.
SAMPLE_PBIP = Path(__file__).parent.parent / "fixtures" / "sample.pbip"


@pytest.fixture()
def sample_pbip(tmp_path: Path) -> Path:
    """Copy the sample PBIP into the test workspace.

    The write-path containment rules only allow paths under the active
    workspace root, so the fixture has to live inside `tmp_path`.
    """
    dst = tmp_path / "sample.pbip"
    shutil.copytree(SAMPLE_PBIP, dst)
    return dst


# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reset all module-level + filesystem state for each test."""
    monkeypatch.setattr(
        "powerbi_orchestrator_mcp.orchestrator.context.SESSIONS_DIR", tmp_path / "sessions"
    )
    monkeypatch.setattr(
        "powerbi_orchestrator_mcp.orchestrator.plan_executions.EXEC_DIR", tmp_path / "executions"
    )
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

        status = CtxEngineStatus(name="te", available=False, reason_unavailable="not installed")
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

        result = await connect_target(target_type="pbip_folder", target_ref=str(pbip_dir))
        # Every engine the detector knows about should be reported as
        # unavailable. The list is the engines a tool can dispatch to.
        for engine in ("powerbi-modeling-mcp", "te", "superbi-mcp"):
            assert engine in result.engines_available
            assert result.engines_available[engine].available is False
            assert result.engines_available[engine].reason_unavailable is not None

    @pytest.mark.asyncio
    async def test_warning_when_engines_missing(
        self, pbip_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        result = await connect_target(target_type="pbip_folder", target_ref=str(pbip_dir))
        assert any("unavailable" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_warning_for_nonexistent_path(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist"
        result = await connect_target(target_type="pbip_folder", target_ref=str(missing))
        assert any("does not exist" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_persists_session(self, pbip_dir: Path) -> None:
        from powerbi_orchestrator_mcp.orchestrator.context import SessionStore

        result = await connect_target(target_type="pbip_folder", target_ref=str(pbip_dir))
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
    async def test_apply_plan_defaults_to_dry_run(self) -> None:
        """Safety default: apply_plan must not mutate without opt-in.

        An LLM-driven caller that says "apply the plan" without naming a
        dry-run flag must get simulation, not execution.
        """
        import inspect

        assert inspect.signature(apply_plan).parameters["dry_run"].default is True

        plan_res = await plan_change(
            intent="audit",
            options={"target": "x", "checks": ["a"]},
        )
        result = await apply_plan(plan_id=plan_res.plan_id)
        assert result.result == "success"
        assert all(not s.get("changed_files") for s in result.executed_steps)

    @pytest.mark.asyncio
    async def test_confirm_each_step_without_context_fails_closed(self) -> None:
        """A confirmation flag that cannot ask must NOT execute.

        Failing open here would make confirm_each_step a no-op flag that
        silently executes everything — worse than not having the flag.
        """
        plan_res = await plan_change(
            intent="audit",
            options={"target": "x", "checks": ["a"]},
        )
        result = await apply_plan(
            plan_id=plan_res.plan_id,
            dry_run=False,
            confirm_each_step=True,
            ctx=None,
        )
        assert result.result == "stopped_by_user"
        assert result.failed_step is not None
        assert "not run" in result.failed_step["error_message"]
        # No step may have actually executed.
        assert all(s.get("skipped") for s in result.executed_steps)

    @pytest.mark.asyncio
    async def test_confirm_each_step_declined_stops_and_skips_rest(self) -> None:
        plan_res = await plan_change(
            intent="audit",
            options={"target": "x", "checks": ["a"]},
        )

        class _DecliningCtx:
            async def elicit(self, **kwargs: Any) -> Any:
                return type("R", (), {"action": "decline", "data": None})()

        result = await apply_plan(
            plan_id=plan_res.plan_id,
            dry_run=False,
            confirm_each_step=True,
            ctx=_DecliningCtx(),  # type: ignore[arg-type]
        )
        assert result.result == "stopped_by_user"
        assert result.failed_step is not None
        assert "declined by user" in result.failed_step["error_message"]
        # Every step reported as skipped; none executed.
        assert result.executed_steps
        assert all(s.get("skipped") for s in result.executed_steps)

    @pytest.mark.asyncio
    async def test_confirm_each_step_accepted_runs_all_steps(self, sample_pbip: Path) -> None:
        plan_res = await plan_change(
            intent="audit",
            options={"target": str(sample_pbip), "checks": ["dax_lint"]},
        )

        class _ApprovingCtx:
            def __init__(self) -> None:
                self.prompts: list[str] = []

            async def elicit(self, **kwargs: Any) -> Any:
                self.prompts.append(kwargs.get("message", ""))
                data = ElicitationSchema(response="yes")
                return type("R", (), {"action": "accept", "data": data})()

        ctx = _ApprovingCtx()
        result = await apply_plan(
            plan_id=plan_res.plan_id,
            dry_run=False,
            confirm_each_step=True,
            ctx=ctx,  # type: ignore[arg-type]
        )
        assert result.result == "success"
        assert len(ctx.prompts) == 3, "one prompt per plan step"
        assert all(not s.get("skipped") for s in result.executed_steps)

    @pytest.mark.asyncio
    async def test_confirm_each_step_non_yes_answer_is_not_approval(self) -> None:
        """A vague answer must not be read as consent."""
        plan_res = await plan_change(
            intent="audit",
            options={"target": "x", "checks": ["a"]},
        )

        class _VagueCtx:
            async def elicit(self, **kwargs: Any) -> Any:
                data = ElicitationSchema(response="maybe?")
                return type("R", (), {"action": "accept", "data": data})()

        result = await apply_plan(
            plan_id=plan_res.plan_id,
            dry_run=False,
            confirm_each_step=True,
            ctx=_VagueCtx(),  # type: ignore[arg-type]
        )
        assert result.result == "stopped_by_user"
        assert result.failed_step is not None
        assert "not an explicit yes" in result.failed_step["error_message"]

    @pytest.mark.asyncio
    async def test_unknown_plan_id_returns_failed(self) -> None:
        result = await apply_plan(plan_id="plan_does_not_exist")
        assert isinstance(result, ApplyResult)
        assert result.result == "failed"
        assert result.failed_step is not None
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
        get_default_registry().unregister("validation")
        plan_res = await plan_change(
            intent="audit",
            options={"target": "x", "checks": ["a"]},
        )
        result = await apply_plan(plan_id=plan_res.plan_id, dry_run=False)
        assert result.result == "failed"

    @pytest.mark.asyncio
    async def test_apply_plan_with_default_executors_succeeds(self, sample_pbip: Path) -> None:
        """The audit plan runs the real checks over a real PBIP."""
        plan_res = await plan_change(
            intent="audit",
            options={"target": str(sample_pbip), "checks": ["dax_lint"]},
        )
        result = await apply_plan(plan_id=plan_res.plan_id, dry_run=False)
        assert result.result == "success"
        assert len(result.executed_steps) == 3
        assert all(s["success"] for s in result.executed_steps)

    @pytest.mark.asyncio
    async def test_audit_plan_fails_honestly_on_a_missing_target(self) -> None:
        """A target that does not exist must fail, not report a green audit."""
        plan_res = await plan_change(
            intent="audit",
            options={"target": "x", "checks": ["dax_lint"]},
        )
        result = await apply_plan(plan_id=plan_res.plan_id, dry_run=False)
        assert result.result == "failed"
        failed = result.failed_step
        assert failed is not None
        assert failed["action"] == "gather_metadata"
        assert "does not exist" in (failed["error_message"] or "")

    @pytest.mark.asyncio
    async def test_audit_plan_rejects_an_unknown_check(self, sample_pbip: Path) -> None:
        """An unknown check name must not be silently ignored."""
        plan_res = await plan_change(
            intent="audit",
            options={"target": str(sample_pbip), "checks": ["not_a_check"]},
        )
        result = await apply_plan(plan_id=plan_res.plan_id, dry_run=False)
        assert result.result == "failed"
        failed = result.failed_step
        assert failed is not None
        assert "unknown audit check" in (failed["error_message"] or "")


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

    @pytest.mark.asyncio
    async def test_failed_rollback_is_not_reported_as_rolled_back(self) -> None:
        """A rollback that fails must not be reported as a clean revert.

        The safe_rename plan reverts the failed modeling step with a
        second `column.update`. When that revert fails too, the
        workspace may not be in its original state, and the result has
        to say so instead of claiming "rolled_back".
        """
        # s2 (forward) fails, then the revert of s2 fails as well.
        modeling = ScriptedEngineExecutor([False, False])
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

        assert result.result != "rolled_back"
        assert result.result == "rollback_incomplete"
        # The failed rollback is reported as failed, not as "ok".
        failed_entries = [r for r in result.rollback_steps_executed if r.get("status") == "failed"]
        assert failed_entries, (
            f"expected a failed rollback entry, got {result.rollback_steps_executed}"
        )
        assert failed_entries[0].get("error_message")

    @pytest.mark.asyncio
    async def test_partial_rollback_is_reported_as_partial(self) -> None:
        """When only some rollback steps succeed, the result says partial.

        s2 (modeling) succeeds, s3 (report) fails. The rollback then
        reverts s3 first and s2 second: the first revert succeeds, the
        second one fails, so the workspace is only partly back to where
        it started.
        """
        # modeling: s2 forward succeeds, then its revert fails.
        modeling = ScriptedEngineExecutor([True, False])
        modeling.engine_name = "modeling"
        # report: s3 forward fails, then its revert succeeds.
        report = ScriptedEngineExecutor([False, True])
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

        assert result.result == "rollback_partial"
        statuses = {r.get("status") for r in result.rollback_steps_executed}
        assert "failed" in statuses
        assert "ok" in statuses


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


class TestSessionIsolation:
    def test_session_var_defaults_none(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.server import (
            _reset_server_state,
            get_active_session_id,
        )

        _reset_server_state()
        assert get_active_session_id() is None

    @pytest.mark.asyncio
    async def test_connect_target_sets_active_session_id(self, pbip_dir: Path) -> None:
        from powerbi_orchestrator_mcp.orchestrator.server import (
            _reset_server_state,
            connect_target,
            get_active_session_id,
        )

        _reset_server_state()
        res = await connect_target(target_type="pbip_folder", target_ref=str(pbip_dir))
        assert get_active_session_id() == res.session_id


class TestMcpInstructions:
    def test_instructions_present_and_informative(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.server import mcp

        assert mcp.instructions is not None
        assert "execute_dax_query" in mcp.instructions
        assert "audit_model_and_report" in mcp.instructions
        assert "powerbi_health" in mcp.instructions


class TestDaxRegressionHonesty:
    @staticmethod
    def _queries() -> str:
        import json

        return json.dumps([{"name": "q", "query": "EVALUATE S"}])

    @pytest.mark.asyncio
    async def test_no_executor_does_not_report_a_green_gate(self, tmp_path: Path) -> None:
        from powerbi_orchestrator_mcp.orchestrator.server import run_dax_regression

        baseline = tmp_path / "baseline.json"
        await run_dax_regression(str(baseline), self._queries())

        res = await run_dax_regression(str(baseline))

        assert res["executed"] is False
        assert res["passed"] is False
        assert res["total_queries"] == 0
        assert "no_query_executor" in res["message"]

    @pytest.mark.asyncio
    async def test_executor_runs_queries_against_fabric(self, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from powerbi_orchestrator_mcp.orchestrator.server import run_dax_regression

        baseline = tmp_path / "baseline.json"
        await run_dax_regression(str(baseline), self._queries())

        client = MagicMock()
        client.execute_queries = AsyncMock(
            return_value={
                "results": [{"tables": [{"columns": [{"name": "Total"}], "rows": [[42]]}]}]
            }
        )

        res = await run_dax_regression(
            str(baseline),
            workspace_id="ws-1",
            dataset_id="ds-1",
            fabric_client=client,
        )

        assert res["executed"] is True
        assert res["total_queries"] == 1
        client.execute_queries.assert_awaited_once()

    def test_fabric_executor_maps_columns_to_dicts(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from powerbi_orchestrator_mcp.orchestrator.server import _fabric_dax_executor

        client = MagicMock()
        client.execute_queries = AsyncMock(
            return_value={
                "results": [
                    {
                        "tables": [
                            {
                                "columns": [{"name": "Region"}, {"name": "Total"}],
                                "rows": [["North", 10], ["South", 20]],
                            }
                        ]
                    }
                ]
            }
        )

        rows = asyncio.run(_fabric_dax_executor(client, "ws", "ds")("EVALUATE X"))

        assert rows == [
            {"Region": "North", "Total": 10},
            {"Region": "South", "Total": 20},
        ]

    def test_run_dax_regression_has_no_injectable_executor_arg(self) -> None:
        import inspect

        from powerbi_orchestrator_mcp.orchestrator.server import run_dax_regression

        assert "query_executor" not in inspect.signature(run_dax_regression).parameters


class TestNoSecretsInMcpSurface:
    def test_no_mcp_tool_accepts_client_secret(self) -> None:
        """A credential must never be a tool argument: it would travel in the
        JSON-RPC payload into the model context and client logs."""
        import inspect

        from powerbi_orchestrator_mcp.orchestrator import server as server_mod

        offenders = [
            name
            for name, obj in vars(server_mod).items()
            if inspect.isfunction(obj)
            and not name.startswith("_")
            and "client_secret" in inspect.signature(obj).parameters
        ]

        assert offenders == []

    def test_cloud_write_tools_default_to_dry_run(self) -> None:
        import inspect

        from powerbi_orchestrator_mcp.orchestrator import server as server_mod

        for name in ("deploy_to_workspace", "run_refresh"):
            params = inspect.signature(getattr(server_mod, name)).parameters
            assert params["dry_run"].default is True, name


class TestJsonArgParsing:
    def test_dict_input_returned_as_is(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.server import _parse_json_arg

        data = {"foo": "bar", "num": 42}
        assert _parse_json_arg(data) == data

    def test_list_input_returned_as_is(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.server import _parse_json_arg

        data = [{"a": 1}, {"b": 2}]
        assert _parse_json_arg(data) == data

    def test_valid_json_string_parsed(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.server import _parse_json_arg

        assert _parse_json_arg('{"key": "val"}') == {"key": "val"}
        assert _parse_json_arg("[1, 2, 3]") == [1, 2, 3]

    def test_invalid_json_string_returns_default(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.server import _parse_json_arg

        assert _parse_json_arg("{bad json", default=[]) == []
        assert _parse_json_arg("not json", default={}) == {}

    def test_none_and_empty_returns_default(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.server import _parse_json_arg

        assert _parse_json_arg(None, default="fallback") == "fallback"
        assert _parse_json_arg("   ", default=[]) == []


class TestExecuteDaxQuery:
    @pytest.mark.asyncio
    async def test_execute_dax_query_wraps_in_evaluate(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from powerbi_orchestrator_mcp.orchestrator.server import execute_dax_query

        mock_client = MagicMock()
        mock_client.execute_queries = AsyncMock(return_value={"results": [{"tables": []}]})

        res = await execute_dax_query(
            workspace_id="ws-123",
            dataset_id="ds-456",
            dax_query="TOPN(5, Sales)",
            fabric_client=mock_client,
        )

        assert res == {"results": [{"tables": []}]}
        mock_client.execute_queries.assert_awaited_once_with(
            workspace_id="ws-123",
            dataset_id="ds-456",
            queries=[{"query": "EVALUATE TOPN(5, Sales)"}],
            impersonated_user_name=None,
        )

    @pytest.mark.asyncio
    async def test_execute_dax_query_preserves_evaluate_and_upn(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from powerbi_orchestrator_mcp.orchestrator.server import execute_dax_query

        mock_client = MagicMock()
        mock_client.execute_queries = AsyncMock(
            return_value={"results": [{"tables": [{"rows": [1]}]}]}
        )

        res = await execute_dax_query(
            workspace_id="ws-123",
            dataset_id="ds-456",
            dax_query="EVALUATE ROW('Count', 1)",
            impersonated_user_name="user@company.com",
            fabric_client=mock_client,
        )

        assert res == {"results": [{"tables": [{"rows": [1]}]}]}
        mock_client.execute_queries.assert_awaited_once_with(
            workspace_id="ws-123",
            dataset_id="ds-456",
            queries=[{"query": "EVALUATE ROW('Count', 1)"}],
            impersonated_user_name="user@company.com",
        )

    @pytest.mark.asyncio
    async def test_caps_rows_to_max_rows(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from powerbi_orchestrator_mcp.orchestrator.server import execute_dax_query

        mock_client = MagicMock()
        mock_client.execute_queries = AsyncMock(
            return_value={"results": [{"tables": [{"rows": list(range(50))}]}]}
        )

        res = await execute_dax_query(
            workspace_id="ws-123",
            dataset_id="ds-456",
            dax_query="EVALUATE Customers",
            max_rows=5,
            fabric_client=mock_client,
        )

        assert len(res["results"][0]["tables"][0]["rows"]) == 5
        assert res["rows_truncated"] is True
        assert res["rows_limit"] == 5

    @pytest.mark.asyncio
    async def test_max_rows_ceiling_is_enforced(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.server import (
            DAX_ROW_LIMIT_CEILING,
            _cap_dax_rows,
        )

        payload = {"results": [{"tables": [{"rows": [1, 2, 3]}]}]}
        _out, truncated = _cap_dax_rows(payload, DAX_ROW_LIMIT_CEILING * 100)

        assert truncated is False
        assert _out["results"][0]["tables"][0]["rows"] == [1, 2, 3]

    def test_cap_dax_rows_does_not_mutate_input(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.server import _cap_dax_rows

        payload = {"results": [{"tables": [{"rows": [1, 2, 3, 4]}]}]}
        _cap_dax_rows(payload, 2)

        assert payload["results"][0]["tables"][0]["rows"] == [1, 2, 3, 4]
