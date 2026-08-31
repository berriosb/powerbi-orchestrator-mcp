"""End-to-end integration test: safe_rename with mock modeling + report engines.

Wires together:
- PlanBuilder.build_safe_rename (planner)
- PythonReportEngine (real — no external deps)
- PowerBiModelingMcpEngine with mock_responses (simulated subprocess)
- StepExecutorRegistry (orchestrator's executor dispatch)
- RollbackEngine (rollback on failure)

This is a true integration test — multiple modules cooperating end-to-end.
The modeling engine is mocked (no real subprocess); the report engine
runs for real on a temp PBIP folder.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from powerbi_orchestrator_mcp.engines.modeling_mcp import PowerBiModelingMcpEngine
from powerbi_orchestrator_mcp.engines.report_python import PythonReportEngine
from powerbi_orchestrator_mcp.orchestrator.context import Target
from powerbi_orchestrator_mcp.orchestrator.plan_models import Plan, PlanOptions, PlanStep
from powerbi_orchestrator_mcp.orchestrator.planner import PlanBuilder
from powerbi_orchestrator_mcp.orchestrator.rollback import (
    RollbackEngine,
    StepOutcome,
)
from powerbi_orchestrator_mcp.orchestrator.step_executor import (
    StepExecutor,
    get_default_registry,
    reset_default_registry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """Reset step_executor registry between tests."""
    reset_default_registry()


@pytest.fixture()
def pbip_dir(tmp_path: Path) -> Path:
    """Create a minimal PBIP folder with a page that references a column."""
    pbip_root = tmp_path / "sample.pbip"
    pbip_root.mkdir()
    (pbip_root / "sample.pbip").write_text(
        json.dumps({"version": "1.0"}), encoding="utf-8"
    )
    report_dir = pbip_root / "sample.Report"
    report_dir.mkdir()
    (report_dir / "report.json").write_text("{}", encoding="utf-8")

    page_dir = report_dir / "pages" / "Overview"
    page_dir.mkdir(parents=True)
    (page_dir / "page.json").write_text(
        json.dumps(
            {
                "$type": "page",
                "name": "Overview",
                "visualContainers": [
                    {
                        "$type": "visualContainer",
                        "id": "v1",
                        "visual": {
                            "$type": "card",
                            "query": {"queryRef": "Customer[ID]"},
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return pbip_root


def _register_engines(
    modeling_responses: dict[str, Any] | None = None,
) -> tuple[PowerBiModelingMcpEngine, PythonReportEngine]:
    """Register the modeling (mocked) + report (real) engines.

    Returns (modeling, report) for direct inspection.
    """
    # Real Python report engine (no subprocess).
    report = PythonReportEngine()

    # Mocked modeling engine (no subprocess). We monkey-patch _start so
    # the engine doesn't try to spawn a real subprocess.
    modeling = PowerBiModelingMcpEngine(
        binary="/bin/echo",
        version="0.1.9",
        mock_responses=modeling_responses or {},
    )

    async def fake_start(*_args: object, **_kwargs: object) -> None:
        """No-op: simulate 'engine started successfully'."""
        return None

    modeling._start = fake_start  # type: ignore[method-assign]  # noqa: SLF001

    # Register StepExecutors for the apply_plan registry.
    class _ModelingExecutor(StepExecutor):
        engine_name = "modeling"

        def __init__(self, eng: PowerBiModelingMcpEngine) -> None:
            self._eng = eng

        async def execute_step(self, step: PlanStep) -> StepOutcome:
            target = Target(
                target_type="pbip_folder",
                target_ref=str(step.args.get("pbip_path", ".")),
                auth_mode="interactive",
            )
            try:
                conn = await self._eng.connect(target)
            except Exception as exc:
                return StepOutcome(success=False, error_message=str(exc))

            try:
                # Dispatch based on action.
                action = step.action
                if action == "column.update":
                    # Planner emits {old_path: "T[C]", new_name: "..."}.
                    # PowerBiModelingMcpEngine.update_column wants
                    # (table, column, changes). Translate.
                    old_path: str = step.args.get("old_path", "")
                    new_name: str | None = step.args.get("new_name")
                    if "[" in old_path and old_path.endswith("]"):
                        table = old_path.split("[")[0]
                        column = old_path.rsplit("[", 1)[1][:-1]
                    else:
                        table = old_path.rsplit(".", 1)[0]
                        column = old_path.split(".")[-1]
                    changes = step.args.get("changes", {})
                    if new_name is not None:
                        changes = {**changes, "new_name": new_name}
                    result = await self._eng.update_column(
                        conn, table=table, column=column, changes=changes
                    )
                    return StepOutcome(
                        success=result.success,
                        error_message=result.error_message,
                        changed_files=result.changed_files,
                    )
                if action == "snapshot":

                    handle = await self._eng.snapshot(conn, step.args["label"])
                    return StepOutcome(
                        success=True,
                        changed_files=[str(handle.path)],
                    )
                # Generic pass-through for other modeling ops.
                return StepOutcome(success=True)
            except Exception as exc:
                return StepOutcome(success=False, error_message=str(exc))

    class _ReportExecutor(StepExecutor):
        engine_name = "report"

        def __init__(self, eng: PythonReportEngine) -> None:
            self._eng = eng

        async def execute_step(self, step: PlanStep) -> StepOutcome:
            from powerbi_orchestrator_mcp.engines.base import ConnectionHandle

            pbip_path = Path(step.args.get("pbip_path", "."))
            conn = ConnectionHandle(
                engine="python_report",
                target_type="pbip_folder",
                target_ref=str(pbip_path),
                session_token="x",
            )
            try:
                action = step.action
                if action == "propagate_rename":
                    result = await self._eng.propagate_rename(
                        conn,
                        step.args["old_path"],
                        step.args["new_path"],
                        step.args.get("scope", "report_bindings"),
                    )
                elif action == "snapshot":
                    # For MVP, the report doesn't need its own snapshot —
                    # we re-use the modeling snapshot. Return success.
                    return StepOutcome(success=True)
                else:
                    result = await self._eng.validate_pbir(conn)
                    from powerbi_orchestrator_mcp.engines.base import (
                        OperationResult,
                    )

                    result = OperationResult(success=result.valid)
                return StepOutcome(
                    success=result.success,
                    error_message=result.error_message,
                    changed_files=result.changed_files,
                )
            except Exception as exc:
                return StepOutcome(success=False, error_message=str(exc))

    class _ValidationExecutor(StepExecutor):
        """No-op validation executor (real validation is Week 2)."""

        engine_name = "validation"

        async def execute_step(self, step: PlanStep) -> StepOutcome:
            return StepOutcome(success=True)

    class _RouterExecutor(StepExecutor):
        """Dispatches to the right per-engine executor based on step.engine.

        Used for cross-engine rollback: RollbackEngine calls execute_step
        once per rollback step and we route to the correct engine.
        """

        engine_name = "_router"

        async def execute_step(self, step: PlanStep) -> StepOutcome:
            reg = get_default_registry()
            executor = reg.get(step.engine)
            if isinstance(executor, _RouterExecutor):
                # Don't recurse.
                return StepOutcome(
                    success=False,
                    error_message=f"no executor for {step.engine}",
                )
            return await executor.execute_step(step)

    get_default_registry().register(_ModelingExecutor(modeling))
    get_default_registry().register(_ReportExecutor(report))
    get_default_registry().register(_ValidationExecutor())
    get_default_registry().register(_RouterExecutor())
    return modeling, report


# ---------------------------------------------------------------------------
# Test fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def plan_builder() -> PlanBuilder:
    return PlanBuilder()


@pytest.fixture()
def target_pbip(pbip_dir: Path) -> Target:
    return Target(
        target_type="pbip_folder",
        target_ref=str(pbip_dir),
        auth_mode="interactive",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSafeRenameEndToEnd:
    async def test_full_safe_rename_works(
        self,
        plan_builder: PlanBuilder,
        target_pbip: Target,
        pbip_dir: Path,
    ) -> None:
        """Build a plan, mock modeling, run report propagate, verify PBIR changed."""
        modeling_responses = {
            "column_operations/update": {
                "changed_files": ["sample.Dataset/definition.tmdl"]
            },
            "database_operations/export_tmdl": {
                "path": "/tmp/snap.tmdl"
            },
        }
        modeling, report = _register_engines(modeling_responses)

        # Build the plan (relaxed threshold so 0.3 risk_score passes).
        plan = plan_builder.build_safe_rename(
            old_path="Customer[ID]",
            new_path="Customer[CustomerKey]",
            scope="report_bindings",
            options=PlanOptions(max_impact_threshold=80),
        )

        # Execute the 4 steps in order: snapshot, column.update, propagate_rename, validate.
        registry = get_default_registry()
        executed: list[StepOutcome] = []
        for step in plan.steps:
            # Patch step.args with pbip_path for both executors.
            step.args.setdefault("pbip_path", str(pbip_dir))
            executor = registry.get(step.engine)
            assert not isinstance(executor, type(None)) or hasattr(executor, "execute_step")
            outcome = await executor.execute_step(step)
            executed.append(outcome)
            if not outcome.success:
                break  # simulate apply_plan's short-circuit on failure

        # All 4 steps succeeded.
        assert all(o.success for o in executed), [o.error_message for o in executed]

        # The PBIR file should now reference CustomerKey instead of Customer.ID.
        page_json = (
            pbip_dir / "sample.Report" / "pages" / "Overview" / "page.json"
        )
        data = json.loads(page_json.read_text())
        page_str = json.dumps(data)
        assert "CustomerKey" in page_str
        assert "Customer.ID" not in page_str

        # The report's changed_files should be tracked.
        propagate_outcome = executed[2]  # propagate_rename is step 3
        assert propagate_outcome.changed_files
        assert any("Overview" in f for f in propagate_outcome.changed_files)

        # The modeling mock recorded the call with the new column name.
        last_modeling_call = modeling.dispatch_calls[-1]
        assert last_modeling_call[0] == "column_operations/update"
        assert "CustomerKey" in json.dumps(last_modeling_call[1])

    async def test_rollback_restores_state_on_failure(
        self,
        plan_builder: PlanBuilder,
        target_pbip: Target,
        pbip_dir: Path,
    ) -> None:
        """If propagate_rename succeeds but a later step fails, rollback."""
        # Modeling succeeds; snapshot succeeds.
        modeling_responses = {
            "column_operations/update": {
                "changed_files": ["sample.Dataset/definition.tmdl"]
            },
            "database_operations/export_tmdl": {"path": "/tmp/snap.tmdl"},
        }
        _register_engines(modeling_responses)

        # Manually construct a 3-step plan where step 3 fails.
        plan_id = "plan_test_rollback"
        s1 = PlanStep(
            id=f"{plan_id}:s1",
            engine="modeling",
            action="column.update",
            args={
                "table": "Customer",
                "column": "ID",
                "changes": {"new_name": "CustomerKey"},
                "pbip_path": str(pbip_dir),
            },
            rollback_step=PlanStep(
                id=f"{plan_id}:s1-rb",
                engine="modeling",
                action="column.update",
                args={
                    "table": "Customer",
                    "column": "CustomerKey",
                    "changes": {"new_name": "ID"},
                    "pbip_path": str(pbip_dir),
                },
            ),
        )
        s2 = PlanStep(
            id=f"{plan_id}:s2",
            engine="report",
            action="propagate_rename",
            args={
                "old_path": "Customer[ID]",
                "new_path": "Customer[CustomerKey]",
                "scope": "report_bindings",
                "pbip_path": str(pbip_dir),
            },
            rollback_step=PlanStep(
                id=f"{plan_id}:s2-rb",
                engine="report",
                action="propagate_rename",
                args={
                    "old_path": "Customer[CustomerKey]",
                    "new_path": "Customer[ID]",
                    "scope": "report_bindings",
                    "pbip_path": str(pbip_dir),
                },
            ),
        )
        s3 = PlanStep(
            id=f"{plan_id}:s3",
            engine="cloud",  # No cloud executor registered → MissingEngineExecutor → fail
            action="do_something_unsupported",
            args={"pbip_path": str(pbip_dir)},
        )

        plan = Plan(
            id=plan_id,
            steps=[s1, s2, s3],
            rollback_steps=[s1.rollback_step, s2.rollback_step],
            risk_score=0.3,
        )

        # Execute via the registry.
        registry = get_default_registry()
        executed: list[tuple[PlanStep, StepOutcome]] = []
        failed: PlanStep | None = None
        for step in plan.steps:
            executor = registry.get(step.engine)
            outcome = await executor.execute_step(step)
            executed.append((step, outcome))
            if not outcome.success:
                failed = step
                break

        # Step 3 should have failed (no executor registered for that engine).
        assert failed is not None
        assert failed.id.endswith(":s3")

        # Run rollback for executed steps before the failure.
        # Use the router executor so cross-engine rollbacks dispatch
        # to the correct per-engine executor.
        router = registry.get("_router")
        rollback_engine = RollbackEngine(router)
        rb_result = await rollback_engine.execute_plan(
            executed_steps=[s for s, _ in executed if s.id != failed.id],
            failed_step=failed,
        )

        # Rollback succeeds (both modeling + report rollbacks ran).
        assert rb_result.status == "rolled_back"
        assert len(rb_result.failed_rollbacks) == 0

        # The PBIR file should be back to "Customer[ID]" (original).
        page_json = (
            pbip_dir / "sample.Report" / "pages" / "Overview" / "page.json"
        )
        data = json.loads(page_json.read_text())
        page_str = json.dumps(data)
        assert "Customer[ID]" in page_str
        assert "CustomerKey" not in page_str
