"""Tests for orchestrator.rollback — RollbackEngine (spec §2.5)."""

from __future__ import annotations

from typing import Any

import pytest

from powerbi_orchestrator_mcp.orchestrator.plan_models import PlanStep
from powerbi_orchestrator_mcp.orchestrator.rollback import (
    FailedRollback,
    NoRollbackAvailableError,
    RollbackEngine,
    RollbackError,
    RollbackResult,
    StepExecutor,
    StepOutcome,
    raise_if_partial,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class ScriptedExecutor:
    """StepExecutor mock that returns scripted outcomes in order.

    Each entry is either ``("success",)``, ``("fail", "msg", ["p1", "p2"])``,
    or a StepOutcome directly.
    """

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.calls: list[PlanStep] = []

    async def execute_step(self, step: PlanStep) -> StepOutcome:
        self.calls.append(step)
        if not self._script:
            return StepOutcome(success=True)
        entry = self._script.pop(0)
        if isinstance(entry, StepOutcome):
            return entry
        if entry[0] == "success":
            return StepOutcome(success=True)
        # fail
        msg = entry[1] if len(entry) > 1 else "fail"
        paths = list(entry[2]) if len(entry) > 2 else []
        return StepOutcome(success=False, error_message=msg, changed_files=paths)


def _step(
    step_id: str,
    rollback_id: str | None = None,
    action: str = "x",
) -> PlanStep:
    """Build a PlanStep with optional rollback."""
    rb: PlanStep | None = None
    if rollback_id is not None:
        rb = PlanStep(id=rollback_id, engine="modeling", action=f"{action}-revert")
    return PlanStep(id=step_id, engine="modeling", action=action, rollback_step=rb)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExecutePlanSuccess:
    async def test_single_step_with_rollback(self) -> None:
        executor = ScriptedExecutor([("success",)])
        engine = RollbackEngine(executor)
        step = _step("s1", "s1-rb")
        result = await engine.execute_plan([], step)
        assert result.status == "rolled_back"
        assert result.rolled_back_steps == ["s1"]

    async def test_rollback_runs_in_reverse(self) -> None:
        executor = ScriptedExecutor([("success",), ("success",), ("success",)])
        engine = RollbackEngine(executor)
        steps = [_step("s1", "s1-rb"), _step("s2", "s2-rb"), _step("s3", "s3-rb")]
        failed = _step("s4", "s4-rb")
        result = await engine.execute_plan(steps, failed)
        # Order: failed step first, then s3, s2, s1.
        assert [c.id for c in executor.calls] == ["s4-rb", "s3-rb", "s2-rb", "s1-rb"]
        assert result.status == "rolled_back"
        assert result.rolled_back_steps == ["s4", "s3", "s2", "s1"]

    async def test_no_executed_steps_just_failed_step(self) -> None:
        executor = ScriptedExecutor([("success",)])
        engine = RollbackEngine(executor)
        failed = _step("s1", "s1-rb")
        result = await engine.execute_plan([], failed)
        assert result.status == "rolled_back"
        assert result.rolled_back_steps == ["s1"]


class TestExecutePlanPartial:
    async def test_one_rollback_fails_others_continue(self) -> None:
        executor = ScriptedExecutor(
            [
                ("success",),
                ("fail", "engine crashed", ["/tmp/sales.pbip"]),
                ("success",),
            ]
        )
        engine = RollbackEngine(executor)
        steps = [_step("s1", "s1-rb"), _step("s2", "s2-rb")]
        failed = _step("s3", "s3-rb")
        result = await engine.execute_plan(steps, failed)
        assert result.status == "partial"
        assert result.rolled_back_steps == ["s3", "s1"]  # s2-rb failed
        assert len(result.failed_rollbacks) == 1
        assert result.failed_rollbacks[0].step_id == "s2"
        assert "/tmp/sales.pbip" in result.failed_rollbacks[0].paths_affected

    async def test_all_rollbacks_fail(self) -> None:
        executor = ScriptedExecutor([("fail", "x"), ("fail", "y")])
        engine = RollbackEngine(executor)
        # Contract: failed_step is NOT in executed_steps (it's the step
        # that just failed and hasn't been added to the executed list yet).
        steps = [_step("s1", "s1-rb")]
        failed = _step("s2", "s2-rb")
        result = await engine.execute_plan(steps, failed)
        assert result.status == "no_rollback"
        assert result.rolled_back_steps == []
        assert len(result.failed_rollbacks) == 2

    async def test_dedupes_when_failed_step_also_in_executed(self) -> None:
        """Defensive: if the caller mistakenly includes failed_step in
        executed_steps, the engine should not double-process it."""
        executor = ScriptedExecutor([("success",), ("success",)])
        engine = RollbackEngine(executor)
        s1 = _step("s1", "s1-rb")
        s2 = _step("s2", "s2-rb")
        result = await engine.execute_plan([s1, s2], s2)
        assert result.status == "rolled_back"
        # s2 rolled back once, s1 once — NOT s2 twice.
        assert result.rolled_back_steps == ["s2", "s1"]
        assert [c.id for c in executor.calls] == ["s2-rb", "s1-rb"]


class TestExecutePlanErrors:
    async def test_no_rollback_available_raises(self) -> None:
        executor = ScriptedExecutor([])
        engine = RollbackEngine(executor)
        # All steps have no rollback_step.
        s1 = _step("s1", None)
        s2 = _step("s2", None)
        with pytest.raises(NoRollbackAvailableError):
            await engine.execute_plan([s1], s2)

    async def test_step_without_rollback_is_skipped(self) -> None:
        """Steps in executed_steps without rollback_step are silently skipped."""
        executor = ScriptedExecutor([("success",)])
        engine = RollbackEngine(executor)
        s1 = _step("s1", None)  # no rollback
        s2 = _step("s2", "s2-rb")
        failed = _step("s3", "s3-rb")
        result = await engine.execute_plan([s1, s2], failed)
        assert result.status == "rolled_back"
        # Only s3 and s2 rolled back (s1 has no rollback).
        assert result.rolled_back_steps == ["s3", "s2"]
        assert [c.id for c in executor.calls] == ["s3-rb", "s2-rb"]


class TestExecuteSingleStep:
    async def test_invokes_rollback(self) -> None:
        executor = ScriptedExecutor([("success",)])
        engine = RollbackEngine(executor)
        step = _step("s1", "s1-rb")
        outcome = await engine.execute_single_step(step)
        assert outcome.success is True
        assert executor.calls[0].id == "s1-rb"

    async def test_no_rollback_step_fails(self) -> None:
        executor = ScriptedExecutor([])
        engine = RollbackEngine(executor)
        step = _step("s1", None)
        outcome = await engine.execute_single_step(step)
        assert outcome.success is False
        assert "no rollback_step" in (outcome.error_message or "")


class TestRaiseIfPartial:
    def test_rolled_back_no_raise(self) -> None:
        result = RollbackResult(
            status="rolled_back", rolled_back_steps=["s1"], failed_rollbacks=[]
        )
        raise_if_partial(result)  # does nothing

    def test_partial_raises_with_paths(self) -> None:
        result = RollbackResult(
            status="partial",
            rolled_back_steps=["s1"],
            failed_rollbacks=[
                FailedRollback(
                    step_id="s2",
                    error_message="crashed",
                    paths_affected=["/tmp/sales.pbip", "/tmp/cache"],
                )
            ],
        )
        with pytest.raises(RollbackError) as exc_info:
            raise_if_partial(result)
        err = exc_info.value
        assert "s2" in str(err)
        assert "/tmp/sales.pbip" in str(err)
        assert "/tmp/cache" in str(err)

    def test_no_rollback_raises(self) -> None:
        result = RollbackResult(
            status="no_rollback",
            rolled_back_steps=[],
            failed_rollbacks=[
                FailedRollback(step_id="s1", error_message="x", paths_affected=[])
            ],
        )
        with pytest.raises(RollbackError):
            raise_if_partial(result)


class TestExecutorProtocol:
    """The StepExecutor Protocol is satisfied by any class with execute_step."""

    def test_scripted_executor_satisfies_protocol(self) -> None:
        # Static type check (mypy enforces this at build time); runtime check
        # verifies the method exists with the right signature.
        executor = ScriptedExecutor([])
        assert hasattr(executor, "execute_step")
        assert callable(executor.execute_step)


# ---------------------------------------------------------------------------
# Cross-engine rollback via dispatcher (post-2026-08-26 fix)
# ---------------------------------------------------------------------------


class _PerEngineExecutor:
    """Mock that behaves like a different executor per engine.

    Records which engine each call was for; returns success unless the
    step's engine is in ``fail_for``.
    """

    engine_name = "_per_engine"

    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.fail_for = fail_for or set()
        self.calls: list[PlanStep] = []

    async def execute_step(self, step: PlanStep) -> StepOutcome:
        self.calls.append(step)
        if step.engine in self.fail_for:
            return StepOutcome(
                success=False,
                error_message=f"scripted fail for engine {step.engine}",
            )
        return StepOutcome(success=True)


class TestCrossEngineDispatcher:
    """RollbackEngine accepts a dispatcher callable for cross-engine plans."""

    async def test_dispatcher_routes_per_engine(self) -> None:
        # Build 3 rollback steps across 2 engines.
        s_modeling = _step("s1", "s1-rb", action="modeling_op")
        s_report_a = _step("s2", "s2-rb", action="report_op_a")
        # Hack the engine field to simulate cross-engine.
        s_report_a.engine = "report"
        s_report_b = _step("s3", "s3-rb", action="report_op_b")
        s_report_b.engine = "report"

        captured: dict[str, list[PlanStep]] = {}

        def _dispatcher(step: PlanStep) -> StepExecutor:
            class _RecorderExec:
                engine_name = step.engine

                async def execute_step(self, s: PlanStep) -> StepOutcome:
                    captured.setdefault(s.engine, []).append(s)
                    return StepOutcome(success=True)

            return _RecorderExec()

        engine = RollbackEngine(_dispatcher)
        # Build the queue: [s3-rb, s2-rb (reverse), s1-rb]
        # RollbackEngine internally calls execute_step on the rollback
        # step (rb), which has the same engine as the original.
        # Build a fake plan that puts all 3 in executed + 1 failed.
        plan_steps = [s_modeling, s_report_a, s_report_b]
        # We need a failed step too, to trigger the queue.
        failed_step = _step("failed", "failed-rb", action="noop")
        failed_step.engine = "cloud"

        # Override rb engines to match originals.
        s_modeling.rollback_step.engine = "modeling"
        s_report_a.rollback_step.engine = "report"
        s_report_b.rollback_step.engine = "report"
        failed_step.rollback_step.engine = "cloud"

        result = await engine.execute_plan(
            executed_steps=plan_steps, failed_step=failed_step
        )
        # 4 rollbacks: failed-rb (cloud) + s3-rb (report) + s2-rb (report)
        # + s1-rb (modeling).
        assert result.status == "rolled_back"
        assert len(result.rolled_back_steps) == 4
        # The dispatcher was called once per rollback step.
        assert "cloud" in captured
        assert "modeling" in captured
        assert "report" in captured

    async def test_dispatcher_partial_failure(self) -> None:
        s1 = _step("s1", "s1-rb")
        s1.rollback_step.engine = "modeling"
        s2 = _step("s2", "s2-rb")
        s2.rollback_step.engine = "report"
        failed_step = _step("failed", "failed-rb")
        failed_step.rollback_step.engine = "cloud"

        # Dispatcher returns an executor that fails for "report".
        def _dispatcher(step: PlanStep) -> StepExecutor:
            return _PerEngineExecutor(fail_for={"report"})

        engine = RollbackEngine(_dispatcher)
        result = await engine.execute_plan(
            executed_steps=[s1, s2], failed_step=failed_step
        )
        assert result.status == "partial"
        # s1 (modeling) rollback succeeded; failed_step (cloud) rollback
        # succeeded; s2 (report) rollback failed.
        assert "s1" in result.rolled_back_steps
        assert "failed" in result.rolled_back_steps
        # s2 is in failed_rollbacks.
        assert len(result.failed_rollbacks) == 1
        assert result.failed_rollbacks[0].step_id == "s2"

    async def test_executor_form_still_works(self) -> None:
        """Backward compatibility: single-executor form still works."""
        executor = _PerEngineExecutor()
        engine = RollbackEngine(executor)
        s1 = _step("s1", "s1-rb")
        s1.rollback_step.engine = "modeling"
        failed_step = _step("failed", "failed-rb")
        failed_step.rollback_step.engine = "cloud"

        result = await engine.execute_plan(
            executed_steps=[s1], failed_step=failed_step
        )
        assert result.status == "rolled_back"
        # All rollback calls went through the single executor.
        assert len(executor.calls) == 2

    async def test_executor_or_dispatcher_typing(self) -> None:
        """Both forms are accepted by the constructor type."""
        executor_obj: StepExecutor = _PerEngineExecutor()
        engine1 = RollbackEngine(executor_obj)
        engine2 = RollbackEngine(lambda _: executor_obj)
        # Just verify construction succeeds — runtime behavior tested above.
        assert engine1 is not None
        assert engine2 is not None
