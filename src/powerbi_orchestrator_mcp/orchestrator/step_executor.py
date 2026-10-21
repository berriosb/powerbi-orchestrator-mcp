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


def _validated_write_target(pbip_path: str) -> str:
    """Bound a path an engine is about to write in to the workspace.

    The engines write wherever they are told, and the path arrives from
    the plan — which a caller can build with any target. Validating it
    here, before the engine is handed the path, is what keeps a plan
    from writing outside the workspace.
    """
    from powerbi_orchestrator_mcp.validation.path_safety import validate_write_path

    return str(validate_write_path(pbip_path, must_exist=False))


def _outcome(engine_name: str | None, **kwargs: Any) -> StepOutcome:
    """Build a step outcome that says which engine served the step.

    A step served by the orchestrator's built-in adapter is a degraded
    path: it works on the files on disk, but it is not the real engine.
    The caller is told, rather than being left to assume.
    """
    from powerbi_orchestrator_mcp.engines.registry import is_builtin_engine

    details = dict(kwargs.pop("details", None) or {})
    if engine_name:
        details["engine"] = engine_name
        details["builtin_engine"] = is_builtin_engine(engine_name)
    return StepOutcome(details=details, **kwargs)


def unsupported_action(engine_name: str, action: str, *, hint: str = "") -> StepOutcome:
    """Build the failure returned when an executor cannot run an action.

    An executor that does not implement an action must say so. Reporting
    success for work that was never performed makes a plan look applied
    when nothing happened, which is how a "validated" or "published"
    claim ends up being false.
    """
    message = (
        f"action {action!r} is not implemented by the {engine_name} executor; no work was performed"
    )
    if hint:
        message = f"{message}. {hint}"
    return StepOutcome(success=False, error_message=message, changed_files=[])


# Results produced by earlier steps of the same plan, keyed by plan id.
# The plan id is the prefix of the step id (`<plan_id>:s1`).
_PLAN_STATE: dict[str, dict[str, Any]] = {}


def plan_state(step: PlanStep) -> dict[str, Any]:
    """Return the mutable state shared by the steps of one plan."""
    plan_id = step.id.split(":", 1)[0]
    return _PLAN_STATE.setdefault(plan_id, {})


def clear_plan_state(plan_id: str) -> None:
    """Drop the state accumulated by a plan."""
    _PLAN_STATE.pop(plan_id, None)


def _audit_check_from_names(names: list[str]) -> Any:
    """Translate the plan's check names into an ``AuditCheck``.

    A name that maps to no known check is an error rather than a silent
    no-op: a check that was asked for and quietly ignored is the same
    false green this module exists to remove.
    """
    from powerbi_orchestrator_mcp.tools.audit_model_and_report import AuditCheck

    known = {
        "bpa": "bpa",
        "dax_lint": "dax_lint",
        "daxlint": "dax_lint",
        "accessibility": "accessibility",
        "wcag": "accessibility",
        "star_schema": "star_schema",
        "starshema": "star_schema",
        "naming": "naming",
    }
    flags = dict.fromkeys(AuditCheck.model_fields, False)
    if not names:
        return AuditCheck()
    for raw in names:
        key = known.get(raw.strip().lower())
        if key is None:
            raise ValueError(
                f"unknown audit check {raw!r}; known checks: "
                f"{', '.join(sorted(set(known.values())))}"
            )
        flags[key] = True
    return AuditCheck(**flags)


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
    """Executes the validation-engine steps of a plan.

    Only ``create_snapshot`` is implemented. Every other validation
    action the planner can emit (``pbip_validate_full``,
    ``gather_metadata``, ``run_checks_parallel``, ``aggregate_score``,
    ``pre_deploy_check``, ``load_baseline``, ``diff_baseline``) has no
    executor behind it, so it is reported as a failure: claiming that a
    check ran when it did not is worse than reporting that it did not.
    """

    engine_name = "validation"

    async def execute_step(self, step: PlanStep) -> StepOutcome:
        action = step.action
        args = step.args
        if action == "create_snapshot":
            label = str(args.get("label", "snapshot"))
            pbip_path = args.get("pbip_path") or args.get("target_ref") or "."
            # The snapshot reads the whole model, so the path is
            # bounded to the workspace like any other read.
            from powerbi_orchestrator_mcp.validation.path_safety import (
                validate_safe_pbip_path,
            )

            try:
                pbip_path = str(validate_safe_pbip_path(str(pbip_path), must_exist=False))
            except ValueError as exc:
                return StepOutcome(
                    success=False,
                    error_message=str(exc),
                    changed_files=[],
                )
            handle = await self._snapshot(str(pbip_path), label)
            if handle is None:
                return StepOutcome(
                    success=False,
                    error_message=(
                        f"could not snapshot {pbip_path!r}: no modeling engine "
                        "is available to read the model files"
                    ),
                    changed_files=[],
                )
            return StepOutcome(success=True, error_message=None, changed_files=[str(handle.path)])
        if action == "pbip_validate_full":
            return await self._validate_pbip(args)
        if action == "gather_metadata":
            return self._gather_metadata(step, args)
        if action == "run_checks_parallel":
            return await self._run_checks(step, args)
        if action == "aggregate_score":
            return self._aggregate_score(step)
        return unsupported_action(
            self.engine_name,
            action,
            hint="run the dedicated validation tools instead of the plan path",
        )

    @staticmethod
    def _gather_metadata(step: PlanStep, args: dict[str, Any]) -> StepOutcome:
        """Read the PBIP and record what is inside it."""
        from powerbi_orchestrator_mcp.validation.model_metadata import (
            MetadataError,
            gather_pbip_metadata,
        )

        target = str(args.get("target") or args.get("pbip_path") or args.get("target_ref") or "")
        if not target:
            return StepOutcome(
                success=False,
                error_message="gather_metadata requires a 'target' argument",
                changed_files=[],
            )
        try:
            meta = gather_pbip_metadata(target)
        except MetadataError as exc:
            return StepOutcome(success=False, error_message=str(exc), changed_files=[])
        state = plan_state(step)
        state["metadata"] = meta
        state["pbip_path"] = target
        return StepOutcome(
            success=True,
            error_message=None,
            changed_files=[],
            details={
                "tables": len(meta.tables),
                "columns": sum(len(c) for c in meta.columns.values()),
                "measures": len(meta.measures),
                "pages": len(meta.pages),
                "visuals": meta.visuals_count,
                "sources": meta.sources,
            },
        )

    @staticmethod
    async def _run_checks(step: PlanStep, args: dict[str, Any]) -> StepOutcome:
        """Run the requested checks over the gathered metadata."""
        from powerbi_orchestrator_mcp.tools.audit_model_and_report import (
            audit_model_and_report,
        )

        state = plan_state(step)
        meta = state.get("metadata")
        pbip_path = state.get("pbip_path") or str(args.get("pbip_path") or "")
        if meta is None:
            return StepOutcome(
                success=False,
                error_message=("run_checks_parallel requires gather_metadata to have run first"),
                changed_files=[],
            )
        requested = [str(c) for c in (args.get("checks") or [])]
        try:
            audit_check = _audit_check_from_names(requested)
        except ValueError as exc:
            return StepOutcome(success=False, error_message=str(exc), changed_files=[])

        result = await audit_model_and_report(
            pbip_path,
            dax_measures=dict(meta.measures),
            checks=audit_check,
        )
        state["audit"] = result
        return StepOutcome(
            success=True,
            error_message=None,
            changed_files=[],
            details={
                "overall_score": result.overall_score,
                "score_is_partial": result.score_is_partial,
                "skipped_checks": result.skipped_checks,
                "findings_count": len(result.findings),
                "warnings": result.warnings,
            },
        )

    @staticmethod
    def _aggregate_score(step: PlanStep) -> StepOutcome:
        """Report the aggregated audit score."""
        state = plan_state(step)
        result = state.get("audit")
        if result is None:
            return StepOutcome(
                success=False,
                error_message=("aggregate_score requires run_checks_parallel to have run first"),
                changed_files=[],
            )
        details = {
            "overall_score": result.overall_score,
            "score_is_partial": result.score_is_partial,
            "skipped_checks": result.skipped_checks,
            "findings_count": len(result.findings),
            "bpa_score": result.bpa_score,
            "wcag_score": result.wcag_score,
            "dax_lint_findings_count": result.dax_lint_findings_count,
        }
        # A partial score must not be presented as a clean bill of health.
        if result.score_is_partial:
            return StepOutcome(
                success=False,
                error_message=(
                    "the audit score is partial: "
                    f"{', '.join(result.skipped_checks)} did not run. "
                    f"score over the checks that did run: {result.overall_score}"
                ),
                changed_files=[],
                details=details,
            )
        return StepOutcome(success=True, error_message=None, changed_files=[], details=details)

    @staticmethod
    async def _validate_pbip(args: dict[str, Any]) -> StepOutcome:
        """Validate the PBIP on disk and report what the validator found.

        This is the closing step of the rename plan: it must be a real
        validation of the files that were just written, not an
        unconditional success.
        """
        from powerbi_orchestrator_mcp.engines.base import ConnectionHandle
        from powerbi_orchestrator_mcp.engines.report_python import PythonReportEngine

        pbip_path = str(args.get("pbip_path") or args.get("target_ref") or ".")
        engine = PythonReportEngine()
        conn = ConnectionHandle(
            engine="python_report",
            target_type="pbip_folder",
            target_ref=pbip_path,
            session_token="validate-token",
        )
        try:
            result = await engine.validate_pbir(conn)
        except Exception as exc:
            return StepOutcome(
                success=False,
                error_message=(
                    f"PBIP validation could not run for {pbip_path!r}: {type(exc).__name__}: {exc}"
                ),
                changed_files=[],
            )
        if result.valid:
            return StepOutcome(success=True, error_message=None, changed_files=[])
        errors = [f for f in result.findings if f.get("severity") == "error"]
        detail = "; ".join(
            f"{f.get('rule_id', 'error')}: {f.get('message', '')}" for f in errors[:5]
        )
        return StepOutcome(
            success=False,
            error_message=(
                f"PBIP validation failed for {pbip_path!r} with {len(errors)} error(s): {detail}"
            ),
            changed_files=[],
        )

    @staticmethod
    async def _snapshot(pbip_path: str, label: str) -> Any | None:
        """Take a real snapshot and return its handle.

        The snapshot file must really exist: the plan's rollback handle
        points at it, and a handle to a file that was never written is
        not a rollback.
        """
        from powerbi_orchestrator_mcp.engines.base import ConnectionHandle
        from powerbi_orchestrator_mcp.engines.te_adapter import InMemoryModelingAdapter

        conn = ConnectionHandle(
            engine="python_modeling",
            target_type="pbip_folder",
            target_ref=pbip_path,
            session_token="snapshot-token",
        )
        adapter = InMemoryModelingAdapter()
        try:
            return await adapter.snapshot(conn, label)
        except Exception:
            return None


class ModelingStepExecutor:
    engine_name = "modeling"

    def __init__(self, engine: Any = None) -> None:
        self._engine = engine

    def _get_engine(self, target: Any, operation: str = "column.update") -> Any:
        if self._engine is not None:
            return self._engine
        from powerbi_orchestrator_mcp.engines.registry import build_default_selector

        # The selector is populated with the adapters that are actually
        # installed, so a real engine is used when there is one. The
        # chain ends with the built-in adapter, which always works.
        return build_default_selector().select_modeling_engine(operation, target)

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

        # The engine is about to write in this directory, so the path
        # is bounded to the workspace. A plan built from a target
        # outside the root would otherwise write there.
        try:
            pbip_path = _validated_write_target(pbip_path)
        except ValueError as exc:
            return _outcome(
                getattr(self, "_engine_name_hint", "") or "engine",
                success=False,
                error_message=str(exc),
                changed_files=[],
            )

        from powerbi_orchestrator_mcp.orchestrator.context import Target

        target = Target(
            target_type="pbip_folder",
            target_ref=str(pbip_path),
            auth_mode="interactive",
        )
        engine = self._get_engine(target, action)
        engine_name = getattr(engine, "name", None) or type(engine).__name__

        try:
            conn = await engine.connect(target)
        except Exception as exc:
            return _outcome(engine_name, success=False, error_message=str(exc), changed_files=[])

        try:
            if action in ("column.update", "table.update", "update_table", "update_column"):
                old_path = str(args.get("old_path") or args.get("table", ""))
                new_name = args.get("new_name") or args.get("new_table")
                if "[" in old_path and old_path.endswith("]"):
                    table = old_path.split("[")[0]
                    column = old_path.rsplit("[", 1)[1][:-1]
                elif "." in old_path:
                    table = old_path.rsplit(".", 1)[0]
                    column = old_path.split(".")[-1]
                elif action in ("table.update", "update_table"):
                    table = old_path
                    column = ""
                else:
                    table = ""
                    column = old_path
                changes = dict(args.get("changes", {}))
                if new_name is not None:
                    changes["new_name"] = str(new_name)
                res = await engine.update_column(conn, table=table, column=column, changes=changes)
                return _outcome(
                    engine_name,
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
                return _outcome(
                    engine_name,
                    success=res.success,
                    error_message=res.error_message if not res.success else None,
                    changed_files=res.changed_files,
                )
            if action in ("measure.update", "update_measure"):
                table = str(args.get("table", ""))
                name = str(args.get("name") or args.get("measure", ""))
                changes = dict(args.get("changes", {}))
                res = await engine.update_measure(conn, table=table, measure=name, changes=changes)
                return _outcome(
                    engine_name,
                    success=res.success,
                    error_message=res.error_message if not res.success else None,
                    changed_files=res.changed_files,
                )
            if action in ("measure.delete", "delete_measure"):
                table = str(args.get("table", ""))
                name = str(args.get("name") or args.get("measure", ""))
                res = await engine.delete_measure(conn, table=table, measure=name)
                return _outcome(
                    engine_name,
                    success=res.success,
                    error_message=res.error_message if not res.success else None,
                    changed_files=res.changed_files,
                )
            if action == "snapshot":
                handle = await engine.snapshot(conn, str(args.get("label", "snapshot")))
                return _outcome(engine_name, success=True, changed_files=[str(handle.path)])
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
                return _outcome(engine_name, success=True, changed_files=[])
            return unsupported_action(self.engine_name, action)
        except Exception as exc:
            return _outcome(engine_name, success=False, error_message=str(exc), changed_files=[])
        finally:
            if hasattr(engine, "disconnect"):
                with contextlib.suppress(Exception):
                    await engine.disconnect(conn)


class ReportStepExecutor:
    engine_name = "report"

    def __init__(self, engine: Any = None) -> None:
        self._engine = engine

    def _get_engine(self, pbip_path: str) -> Any:
        if self._engine is not None:
            return self._engine
        from powerbi_orchestrator_mcp.engines.registry import build_default_selector
        from powerbi_orchestrator_mcp.orchestrator.context import Target

        target = Target(
            target_type="pbip_folder",
            target_ref=str(pbip_path),
            auth_mode="interactive",
        )
        return build_default_selector().select_report_engine("report.update", target)

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

        # The engine is about to write in this directory, so the path
        # is bounded to the workspace.
        try:
            pbip_path = _validated_write_target(pbip_path)
        except ValueError as exc:
            return _outcome(
                "report",
                success=False,
                error_message=str(exc),
                changed_files=[],
            )

        engine = self._get_engine(str(pbip_path))
        engine_name = getattr(engine, "name", None) or type(engine).__name__
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
                return _outcome(
                    engine_name,
                    success=res.success,
                    error_message=res.error_message if not res.success else None,
                    changed_files=res.changed_files,
                )
            if action == "add_page":
                layout = PageLayout(**args.get("layout", {})) if "layout" in args else None
                res = await engine.add_page(conn, str(args.get("page_name", "")), layout)
                return _outcome(engine_name, success=res.success, changed_files=res.changed_files)
            if action == "add_visual":
                spec = VisualSpec(**args.get("spec", args))
                res = await engine.add_visual(conn, str(args.get("page", "")), spec)
                return _outcome(engine_name, success=res.success, changed_files=res.changed_files)
            if action == "update_visual":
                res = await engine.update_visual(
                    conn,
                    str(args.get("page", "")),
                    str(args.get("visual_id", "")),
                    args.get("changes", {}),
                )
                return _outcome(engine_name, success=res.success, changed_files=res.changed_files)
            if action == "validate_pbir":
                v = await engine.validate_pbir(conn)
                return _outcome(engine_name, success=v.valid, changed_files=[])
            if action == "snapshot":
                return _outcome(engine_name, success=True, changed_files=[])
            return unsupported_action(self.engine_name, action)
        except Exception as exc:
            return _outcome(engine_name, success=False, error_message=str(exc), changed_files=[])
        finally:
            with contextlib.suppress(Exception):
                await engine.disconnect(conn)


class CloudStepExecutor:
    """Executes the cloud-engine steps of a plan.

    No cloud action is implemented here: publishing to Fabric, binding a
    gateway and scheduling a refresh are performed by the dedicated
    ``deploy_to_workspace`` and ``run_refresh`` tools, which enforce the
    pre-deploy gate and the safe-by-default ``dry_run`` contract.

    Reporting success here would tell the caller that an artifact was
    published to a production workspace when nothing was sent.
    """

    engine_name = "cloud"

    async def execute_step(self, step: PlanStep) -> StepOutcome:
        return unsupported_action(
            self.engine_name,
            step.action,
            hint="use the deploy_to_workspace and run_refresh tools for cloud operations",
        )


def register_default_executors(
    registry: StepExecutorRegistry | None = None,
) -> None:
    reg = registry or get_default_registry()
    reg.register(DryRunExecutor())
    reg.register(ValidationStepExecutor())
    reg.register(ModelingStepExecutor())
    reg.register(ReportStepExecutor())
    reg.register(CloudStepExecutor())


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
        _default_registry.register(CloudStepExecutor())
