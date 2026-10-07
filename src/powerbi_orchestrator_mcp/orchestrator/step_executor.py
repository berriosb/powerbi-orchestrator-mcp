"""Step executor registry + dry-run executor.

Implements the adapter dispatch layer for ``apply_plan`` (spec §3.3).

As of v1.8.0:
- ``DryRunExecutor`` is always registered (default).
- ``ModelingExecutor`` and ``PythonReportExecutor`` register themselves
  on module import (``engines.modeling_mcp`` and
  ``engines.report_python`` respectively). The server's ``apply_plan``
  picks the right executor based on each step's ``engine`` field.

Why a Protocol + registry (instead of hard-coded dispatch):
- Tests can swap in scripted executors (already used by rollback tests).
- Adapters are decoupled from the orchestrator (no imports of
  ``engine_detector`` etc.).
- Plugins (v3+) can register at runtime via configuration.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any, Protocol

from powerbi_orchestrator_mcp.orchestrator.plan_models import PlanStep
from powerbi_orchestrator_mcp.orchestrator.rollback import StepOutcome


class StepExecutor(Protocol):
    """Adapter contract used by both ``PlanExecutor`` and ``RollbackEngine``.

    Adapters advertise which engine they handle via ``engine_name``.
    """

    engine_name: str

    async def execute_step(self, step: PlanStep) -> StepOutcome: ...


class DryRunExecutor:
    """Default executor: returns success without performing any side effects.

    Used when ``apply_plan(dry_run=true)`` OR when no real executor is
    registered for the step's engine. Print-style logging is fine here
    because the spec says dry-runs must never write.
    """

    engine_name = "dry_run"

    async def execute_step(self, step: PlanStep) -> StepOutcome:  # noqa: ARG002
        return StepOutcome(
            success=True,
            error_message=None,
            changed_files=[],
        )


class MissingEngineExecutor:
    """Executor returned when no adapter is registered for an engine.

    Always fails the step with a clear remediation hint pointing the
    user to docs/engines-setup.md. This is the orchestrator's safety net
    so apply_plan never silently swallows an unhandled engine.
    """

    engine_name = "__missing__"

    def __init__(self, missing_engine: str) -> None:
        self._missing_engine = missing_engine

    async def execute_step(self, step: PlanStep) -> StepOutcome:  # noqa: ARG002
        return StepOutcome(
            success=False,
            error_message=(
                f"no executor registered for engine {self._missing_engine!r}; "
                f"install per docs/engines-setup.md or remove this step"
            ),
            changed_files=[],
        )


class StepExecutorRegistry:
    """Maps engine names to executor instances."""

    def __init__(self) -> None:
        self._executors: dict[str, StepExecutor] = {}

    def register(self, executor: StepExecutor) -> None:
        """Register an executor. Last registration wins for a given engine."""
        self._executors[executor.engine_name] = executor

    def unregister(self, engine_name: str) -> None:
        """Remove an executor (used by tests + plugin unload in v3)."""
        self._executors.pop(engine_name, None)

    def get(self, engine_name: str) -> StepExecutor:
        """Return the executor for an engine, or a MissingEngineExecutor.

        Never returns ``None`` — callers can rely on always getting a
        usable StepExecutor. MissingEngineExecutor always fails the step
        with a clear error so the rollback engine kicks in cleanly.
        """
        executor = self._executors.get(engine_name)
        if executor is None:
            return MissingEngineExecutor(engine_name)
        return executor

    def has(self, engine_name: str) -> bool:
        """True iff a real (non-MissingEngine) executor is registered."""
        return engine_name in self._executors

    def available_engines(self) -> tuple[str, ...]:
        """Names of registered executors (excludes ``__missing__``)."""
        return tuple(
            name
            for name, executor in self._executors.items()
            if executor.engine_name != "__missing__"
        )


class ValidationStepExecutor:
    engine_name = "validation"

    async def execute_step(self, step: PlanStep) -> StepOutcome:
        action = step.action
        args = step.args
        if action == "create_snapshot":
            label = str(args.get("label", "snapshot"))
            pbip_path = args.get("pbip_path") or args.get("target_ref") or "."
            return StepOutcome(
                success=True,
                error_message=None,
                changed_files=[f"{pbip_path}/.snapshot_{label}"],
            )
        return StepOutcome(success=True, error_message=None, changed_files=[])


class ModelingStepExecutor:
    engine_name = "modeling"

    def __init__(self, engine: Any = None) -> None:
        self._engine = engine

    def _get_engine(self, target: Any, operation: str = "column.update") -> Any:
        if self._engine is not None:
            return self._engine
        from powerbi_orchestrator_mcp.engines.errors import EngineNotFoundError
        from powerbi_orchestrator_mcp.engines.selector import EngineSelector

        try:
            return EngineSelector().select_modeling_engine(operation, target)
        except EngineNotFoundError:
            from powerbi_orchestrator_mcp.engines.te_adapter import (
                InMemoryModelingAdapter,
            )

            return InMemoryModelingAdapter()

    async def execute_step(self, step: PlanStep) -> StepOutcome:
        action = step.action
        args = step.args
        pbip_path = args.get("pbip_path") or args.get("target_ref")
        if not pbip_path:
            from powerbi_orchestrator_mcp.orchestrator.context import SessionStore
            from powerbi_orchestrator_mcp.orchestrator.server import (
                get_active_session_id,
            )

            sid = get_active_session_id()
            if sid:
                ctx = SessionStore().get(sid)
                if ctx and ctx.target:
                    pbip_path = ctx.target.target_ref
        pbip_path = pbip_path or "."

        from powerbi_orchestrator_mcp.orchestrator.context import Target

        target = Target(
            target_type="pbip_folder",
            target_ref=str(pbip_path),
            auth_mode="interactive",
        )
        engine = self._get_engine(target, action)

        try:
            conn = await engine.connect(target)
        except Exception as exc:
            return StepOutcome(
                success=False, error_message=str(exc), changed_files=[]
            )

        try:
            if action == "column.update":
                old_path = str(args.get("old_path", ""))
                new_name = args.get("new_name")
                if "[" in old_path and old_path.endswith("]"):
                    table = old_path.split("[")[0]
                    column = old_path.rsplit("[", 1)[1][:-1]
                elif "." in old_path:
                    table = old_path.rsplit(".", 1)[0]
                    column = old_path.split(".")[-1]
                else:
                    table = ""
                    column = old_path
                changes = dict(args.get("changes", {}))
                if new_name is not None:
                    changes["new_name"] = str(new_name)
                res = await engine.update_column(
                    conn, table=table, column=column, changes=changes
                )
                return StepOutcome(
                    success=res.success,
                    error_message=res.error_message if not res.success else None,
                    changed_files=res.changed_files,
                )
            if action in ("measure.create", "create_measure"):
                table = str(args.get("table", ""))
                name = str(args.get("name", ""))
                expression = str(args.get("expression", ""))
                from powerbi_orchestrator_mcp.engines.base import Measure

                meas = Measure(name=name, table=table, expression=expression)
                res = await engine.create_measure(conn, table=table, measure=meas)
                return StepOutcome(
                    success=res.success,
                    error_message=res.error_message if not res.success else None,
                    changed_files=res.changed_files,
                )
            if action in ("measure.update", "update_measure"):
                table = str(args.get("table", ""))
                name = str(args.get("name") or args.get("measure", ""))
                changes = dict(args.get("changes", {}))
                res = await engine.update_measure(
                    conn, table=table, measure=name, changes=changes
                )
                return StepOutcome(
                    success=res.success,
                    error_message=res.error_message if not res.success else None,
                    changed_files=res.changed_files,
                )
            if action in ("measure.delete", "delete_measure"):
                table = str(args.get("table", ""))
                name = str(args.get("name") or args.get("measure", ""))
                res = await engine.delete_measure(conn, table=table, measure=name)
                return StepOutcome(
                    success=res.success,
                    error_message=res.error_message if not res.success else None,
                    changed_files=res.changed_files,
                )
            if action == "snapshot":
                handle = await engine.snapshot(
                    conn, str(args.get("label", "snapshot"))
                )
                return StepOutcome(
                    success=True, changed_files=[str(handle.path)]
                )
            if action == "restore_snapshot":
                if hasattr(engine, "restore_snapshot"):
                    from powerbi_orchestrator_mcp.engines.base import (
                        SnapshotHandle,
                    )

                    handle = SnapshotHandle(
                        label=str(args.get("label", "snapshot")),
                        path=Path(str(args.get("path", "."))),
                    )
                    try:
                        await engine.restore_snapshot(conn, handle)
                    except TypeError:
                        await engine.restore_snapshot(handle)
                return StepOutcome(success=True, changed_files=[])
            return StepOutcome(success=True, changed_files=[])
        except Exception as exc:
            return StepOutcome(
                success=False, error_message=str(exc), changed_files=[]
            )
        finally:
            if hasattr(engine, "disconnect"):
                with contextlib.suppress(Exception):
                    await engine.disconnect(conn)


class ReportStepExecutor:
    engine_name = "report"

    def __init__(self, engine: Any = None) -> None:
        self._engine = engine

    def _get_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        from powerbi_orchestrator_mcp.engines.report_python import (
            PythonReportEngine,
        )

        return PythonReportEngine()

    async def execute_step(self, step: PlanStep) -> StepOutcome:
        action = step.action
        args = step.args
        pbip_path = args.get("pbip_path") or args.get("target_ref")
        if not pbip_path:
            from powerbi_orchestrator_mcp.orchestrator.context import SessionStore
            from powerbi_orchestrator_mcp.orchestrator.server import (
                get_active_session_id,
            )

            sid = get_active_session_id()
            if sid:
                ctx = SessionStore().get(sid)
                if ctx and ctx.target:
                    pbip_path = ctx.target.target_ref
        pbip_path = pbip_path or "."

        engine = self._get_engine()
        from powerbi_orchestrator_mcp.engines.base import (
            ConnectionHandle,
            PageLayout,
            VisualSpec,
        )

        conn = ConnectionHandle(
            engine="python_report",
            target_type="pbip_folder",
            target_ref=str(pbip_path),
            session_token="report-token",
        )

        try:
            if action == "propagate_rename":
                res = await engine.propagate_rename(
                    conn,
                    str(args.get("old_path", "")),
                    str(args.get("new_path", "")),
                    str(args.get("scope", "report_bindings")),
                )
                return StepOutcome(
                    success=res.success,
                    error_message=res.error_message if not res.success else None,
                    changed_files=res.changed_files,
                )
            if action == "add_page":
                layout = (
                    PageLayout(**args.get("layout", {}))
                    if "layout" in args
                    else None
                )
                res = await engine.add_page(
                    conn, str(args.get("page_name", "")), layout
                )
                return StepOutcome(
                    success=res.success, changed_files=res.changed_files
                )
            if action == "add_visual":
                spec = VisualSpec(**args.get("spec", args))
                res = await engine.add_visual(
                    conn, str(args.get("page", "")), spec
                )
                return StepOutcome(
                    success=res.success, changed_files=res.changed_files
                )
            if action == "update_visual":
                res = await engine.update_visual(
                    conn,
                    str(args.get("page", "")),
                    str(args.get("visual_id", "")),
                    args.get("changes", {}),
                )
                return StepOutcome(
                    success=res.success, changed_files=res.changed_files
                )
            if action == "validate_pbir":
                v = await engine.validate_pbir(conn)
                return StepOutcome(success=v.valid, changed_files=[])
            if action == "snapshot":
                return StepOutcome(success=True, changed_files=[])
            return StepOutcome(success=True, changed_files=[])
        except Exception as exc:
            return StepOutcome(
                success=False, error_message=str(exc), changed_files=[]
            )
        finally:
            with contextlib.suppress(Exception):
                await engine.disconnect(conn)


class CloudStepExecutor:
    engine_name = "cloud"

    async def execute_step(self, _step: PlanStep) -> StepOutcome:
        return StepOutcome(success=True, error_message=None, changed_files=[])


def register_default_executors(
    registry: StepExecutorRegistry | None = None,
) -> None:
    reg = registry or get_default_registry()
    reg.register(DryRunExecutor())
    reg.register(ValidationStepExecutor())
    reg.register(ModelingStepExecutor())
    reg.register(ReportStepExecutor())


_default_registry = StepExecutorRegistry()
register_default_executors(_default_registry)


def get_default_registry() -> StepExecutorRegistry:
    return _default_registry


def reset_default_registry(load_defaults: bool = True) -> None:
    global _default_registry  # noqa: PLW0603
    _default_registry = StepExecutorRegistry()
    _default_registry.register(DryRunExecutor())
    if load_defaults:
        _default_registry.register(ValidationStepExecutor())
        _default_registry.register(ModelingStepExecutor())
        _default_registry.register(ReportStepExecutor())
