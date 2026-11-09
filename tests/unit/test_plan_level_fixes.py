"""Tests for the plan-level fixes that were still open after v1.19.0.

- The snapshot label was built from the plan id without normalising it,
  so it violated the project's own identifier contract — and whether it
  did depended on the random suffix, which is what made the failure
  intermittent.
- The orphan cutoff was shorter than the longest plausible step, so a
  live execution was reported as abandoned.
"""

from __future__ import annotations

import re

import pytest

from powerbi_orchestrator_mcp.orchestrator.identifiers import (
    SNAPSHOT_LABEL_PATTERN,
    make_snapshot_label,
)
from powerbi_orchestrator_mcp.orchestrator.plan_executions import (
    ORPHAN_HEARTBEAT_CUTOFF_S,
)
from powerbi_orchestrator_mcp.orchestrator.planner import PlanBuilder


class TestSnapshotLabel:
    def test_the_label_satisfies_the_project_contract(self) -> None:
        plan = PlanBuilder().build_safe_rename(
            old_path="Customer[ID]",
            new_path="Customer[CustomerKey]",
            target="model.pbip",
        )
        label = str(plan.steps[0].args["label"])

        assert SNAPSHOT_LABEL_PATTERN.match(label)
        assert make_snapshot_label(label) == label

    def test_the_label_is_valid_for_every_suffix(self) -> None:
        """The suffix is random, so the label must not depend on it.

        The nanoid alphabet contains hyphens, so a suffix can end with
        one — which made the label invalid some of the time.
        """
        for suffix in ("abcdefgh", "abc-defg", "abc-def-", "a-b-c-d-e"):
            plan_id = f"plan_2026-10-20_{suffix}"
            raw = re.sub(r"-+", "-", f"pre-rename-{plan_id}".lower().replace("_", "-"))
            label = make_snapshot_label(raw.strip("-"))

            assert SNAPSHOT_LABEL_PATTERN.match(label), label
            assert not label.startswith("-")
            assert not label.endswith("-")


class TestOrphanCutoff:
    def test_the_cutoff_outlasts_the_longest_engine_timeout(self) -> None:
        """A live execution must not be reported as abandoned.

        The engine timeouts are the longest a step can legitimately run:
        300 s for Tabular Editor, 180 s for superbi-mcp, 120 s for the
        modeling MCP server.
        """
        from powerbi_orchestrator_mcp.engines.timeouts import DEFAULT_TIMEOUTS

        longest = max(cfg.max_s for cfg in DEFAULT_TIMEOUTS.values())
        assert longest < ORPHAN_HEARTBEAT_CUTOFF_S

    def test_the_cutoff_is_not_a_minute(self) -> None:
        """60 seconds was shorter than a publish or a DAX query."""
        assert ORPHAN_HEARTBEAT_CUTOFF_S >= 1800


@pytest.mark.parametrize("suffix", ["abcdefgh", "abc-defg", "abc-def-"])
def test_the_planner_accepts_any_suffix(suffix: str) -> None:
    import powerbi_orchestrator_mcp.orchestrator.identifiers as identifiers_mod

    original = identifiers_mod.new_plan_id
    identifiers_mod.new_plan_id = lambda *a, **k: f"plan_2026-10-20_{suffix}"
    try:
        plan = PlanBuilder().build_safe_rename(
            old_path="Customer[ID]",
            new_path="Customer[CustomerKey]",
            target="model.pbip",
        )
        label = str(plan.steps[0].args["label"])
        assert SNAPSHOT_LABEL_PATTERN.match(label)
    finally:
        identifiers_mod.new_plan_id = original
    assert identifiers_mod.new_plan_id()


class TestRollbackAndPlanOptions:
    @pytest.mark.asyncio
    async def test_failed_step_without_rollback_is_not_marked_non_revertible(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.plan_models import PlanStep
        from powerbi_orchestrator_mcp.orchestrator.rollback import RollbackEngine, StepOutcome

        class _OkExec:
            engine_name = "modeling"

            async def execute_step(self, step: PlanStep) -> StepOutcome:
                return StepOutcome(success=True)

        s1 = PlanStep(
            id="s1",
            engine="modeling",
            action="a1",
            rollback_step=PlanStep(id="s1-rb", engine="modeling", action="a1-rb"),
        )
        s2_failed = PlanStep(id="s2", engine="validation", action="validate")

        engine = RollbackEngine(_OkExec())
        # Even if a caller mistakenly includes s2_failed in executed_steps,
        # s2_failed must not be listed in non_revertible_steps.
        res = await engine.execute_plan(executed_steps=[s1, s2_failed], failed_step=s2_failed)
        assert res.status == "rolled_back"
        assert res.rolled_back_steps == ["s1"]
        assert "s2" not in res.non_revertible_steps

    @pytest.mark.asyncio
    async def test_auto_rollback_false_skips_rollback_and_reports_artifacts_changed(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.plan_models import PlanStep
        from powerbi_orchestrator_mcp.orchestrator.rollback import StepOutcome
        from powerbi_orchestrator_mcp.orchestrator.server import apply_plan, plan_change
        from powerbi_orchestrator_mcp.orchestrator.step_executor import (
            get_default_registry,
            reset_default_registry,
        )

        class _MockValidation:
            engine_name = "validation"

            async def execute_step(self, step: PlanStep) -> StepOutcome:
                return StepOutcome(success=True, changed_files=["snap.json"])

        class _FailingModeling:
            engine_name = "modeling"

            def __init__(self) -> None:
                self.calls: list[str] = []

            async def execute_step(self, step: PlanStep) -> StepOutcome:
                self.calls.append(step.id)
                return StepOutcome(success=False, error_message="boom")

        mod_exec = _FailingModeling()
        try:
            get_default_registry().register(_MockValidation())
            get_default_registry().register(mod_exec)

            plan_res = await plan_change(
                intent="safe_rename",
                options={"old_path": "T[A]", "new_path": "T[B]", "auto_rollback": False},
            )
            res = await apply_plan(plan_id=plan_res.plan_id, dry_run=False)
            assert res.result == "failed"
            assert res.rollback_steps_executed == []
            assert res.artifacts_changed == ["snap.json"]
            # Only forward step ran; revert step was never called.
            assert len(mod_exec.calls) == 1
        finally:
            reset_default_registry()


class TestPlanStoreOwnershipAndScoping:
    def test_put_rejects_cross_owner_overwrite_and_count_scopes_to_owner(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        from pathlib import Path

        from powerbi_orchestrator_mcp.orchestrator.plan_store import PlanStore

        db_path = (
            Path(str(tmp_path)) / "plans.db"
            if not isinstance(tmp_path, Path)
            else tmp_path / "plans.db"
        )
        store = PlanStore(db_path=db_path)
        plan = PlanBuilder().build_safe_rename(old_path="T[A]", new_path="T[B]")

        store.put(plan, owner="alice")
        with pytest.raises(PermissionError, match="belongs to another owner"):
            store.put(plan, owner="bob")

        # Local count (default LOCAL_IDENTITY) does not leak alice's plan count.
        assert store.count() == 0
        assert store.count(owner="alice") == 1
