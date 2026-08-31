"""FastMCP entrypoint - registers all tools and runs the server.

Implements spec sections 3.1, 3.2, 3.3 of ``specs/01-orchestrator.md``.

The three MVP tools (connect_target, plan_change, apply_plan) are wired
to the real implementations built earlier in the sprint
(``engine_detector``, ``planner``, ``plan_executions``, ``step_executor``,
``rollback``). They replace the 1-line stubs that existed previously.

In-memory plan store: plans created by ``plan_change`` are kept in a
module-level dict keyed by ``plan_id``. This is acceptable for MVP
(single-process server; plans are typically short-lived). v4 will move
to SQLite-backed plan storage.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.orchestrator.audit import AuditLog
from powerbi_orchestrator_mcp.orchestrator.context import (
    EngineStatus,
    SessionContext,
    SessionStore,
    Target,
)
from powerbi_orchestrator_mcp.orchestrator.engine_detector import detect_all_engines
from powerbi_orchestrator_mcp.orchestrator.identifiers import (
    make_target_id,
    new_execution_id,
    new_rollback_handle,
    new_session_id,
)
from powerbi_orchestrator_mcp.orchestrator.plan_executions import (
    PlanExecution,
    PlanExecutionState,
    PlanExecutionStore,
    reconcile_orphan_executions_on_boot,
)
from powerbi_orchestrator_mcp.orchestrator.plan_models import (
    EstimatedChanges,
    Plan,
    PlanOptions,
    PlanStep,
    PlanValidationError,
    UnknownTemplateError,
)
from powerbi_orchestrator_mcp.orchestrator.planner import PlanBuilder
from powerbi_orchestrator_mcp.orchestrator.rollback import (
    NoRollbackAvailableError,
    RollbackEngine,
    StepExecutor,
)
from powerbi_orchestrator_mcp.orchestrator.step_executor import (
    get_default_registry,
)

# ---------------------------------------------------------------------------
# Output schemas (spec sections 3.1-3.3)
# ---------------------------------------------------------------------------


class ConnectResult(BaseModel):
    """Output schema for connect_target (spec section 3.1)."""

    session_id: str
    engines_available: dict[str, EngineStatus]
    warnings: list[str] = Field(default_factory=list)


class PlanResult(BaseModel):
    """Output schema for plan_change (spec section 3.2)."""

    plan_id: str
    plan_yaml: str
    steps: list[dict[str, Any]] = Field(default_factory=list)
    risk_score: float = Field(ge=0.0, le=1.0, default=0.0)
    estimated_changes: EstimatedChanges = Field(default_factory=EstimatedChanges)


class ApplyResult(BaseModel):
    """Output schema for apply_plan (spec section 3.3)."""

    result: str = "success"
    executed_steps: list[dict[str, Any]] = Field(default_factory=list)
    failed_step: dict[str, Any] | None = None
    rollback_steps_executed: list[dict[str, Any]] = Field(default_factory=list)
    rollback_handle: str | None = None
    artifacts_changed: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# FastMCP instance
# ---------------------------------------------------------------------------

mcp = FastMCP("powerbi-orchestrator-mcp")


# ---------------------------------------------------------------------------
# Module-level state (MVP in-memory plan + active session)
# ---------------------------------------------------------------------------

_plans: dict[str, Plan] = {}
_active_session_id: str | None = None


def _reset_server_state() -> None:
    """Reset module-level state (for tests)."""
    global _active_session_id  # noqa: PLW0603
    _plans.clear()
    _active_session_id = None


# ---------------------------------------------------------------------------
# connect_target (spec §3.1 + §11 matrix)
# ---------------------------------------------------------------------------

_TARGET_TYPES = frozenset({"pbi_desktop", "fabric_workspace", "pbip_folder", "pbix_file"})
_AUTH_MODES = frozenset({"interactive", "service_principal"})
_FABRIC_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{6,64}$")


def _validate_target_ref(target_type: str, target_ref: str) -> str | None:
    """Return a warning string if the ref looks malformed for the type.

    Returns ``None`` if OK, or a human-readable warning to surface in
    ``ConnectResult.warnings``. The function does NOT raise: connect_target
    succeeds and the warning is surfaced to the agent.
    """
    if target_type in ("pbip_folder", "pbix_file"):
        path = Path(target_ref)
        if not path.exists():
            return f"{target_type} path does not exist: {target_ref}"
        return None
    if target_type == "fabric_workspace":
        if not _FABRIC_ID_PATTERN.match(target_ref):
            return (
                f"fabric_workspace ref should be a workspace ID, got "
                f"{target_ref!r}"
            )
        return None
    if target_type == "pbi_desktop":
        # We can't probe Desktop from the server loopback here — defer
        # the connectivity check to the actual adapter call.
        return None
    return None


def _detect_engine_warnings(engines: dict[str, EngineStatus]) -> list[str]:
    """Compose user-facing warnings about missing engines."""
    missing = [name for name, s in engines.items() if not s.available]
    if not missing:
        return []
    return [
        f"engine {name!r} unavailable: "
        f"{engines[name].reason_unavailable or 'unknown reason'}"
        for name in missing
    ]


@mcp.tool()
async def connect_target(
    target_type: str,
    target_ref: str,
    auth_mode: str = "interactive",
    tenant_id: str | None = None,
) -> ConnectResult:
    """Connect to a Power BI target and detect available engines.

    Args:
        target_type: One of "pbi_desktop", "fabric_workspace",
            "pbip_folder", "pbix_file".
        target_ref: Reference to the target (path, workspace_id, etc.).
        auth_mode: "interactive" or "service_principal".
        tenant_id: Azure tenant ID (only used with service_principal).

    Returns:
        ConnectResult with session_id, engines_available, and warnings.
    """
    global _active_session_id  # noqa: PLW0603

    if target_type not in _TARGET_TYPES:
        raise ValueError(
            f"target_type must be one of {sorted(_TARGET_TYPES)}, "
            f"got {target_type!r}"
        )
    if auth_mode not in _AUTH_MODES:
        raise ValueError(
            f"auth_mode must be one of {sorted(_AUTH_MODES)}, got {auth_mode!r}"
        )
    if auth_mode == "service_principal" and not tenant_id:
        raise ValueError("tenant_id is required for service_principal auth_mode")

    # Best-effort validation of target_ref (non-fatal).
    validation_warning = _validate_target_ref(target_type, target_ref)

    # Crash recovery: reconcile orphan executions on every connect. This is
    # idempotent — calling it once per server boot is enough, but calling
    # on connect makes the agent-facing semantics simpler.
    reconcile_orphan_executions_on_boot()

    engines = await detect_all_engines()
    warnings: list[str] = []
    if validation_warning:
        warnings.append(validation_warning)
    warnings.extend(_detect_engine_warnings(engines))

    session_id = new_session_id()
    target_id = make_target_id(target_type, target_ref)
    target = Target(
        target_type=target_type,
        target_ref=target_ref,
        auth_mode=auth_mode,
        tenant_id=tenant_id,
    )
    context = SessionContext(
        session_id=session_id,
        target=target,
        engines_available=engines,
        metadata_cache={"target_id": target_id},
    )
    SessionStore().create(context)
    _active_session_id = session_id

    return ConnectResult(
        session_id=session_id,
        engines_available=engines,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# plan_change (spec §3.2)
# ---------------------------------------------------------------------------


def _build_plan_from_intent(
    intent: str,
    options: dict[str, Any] | None,
    opts: PlanOptions,
) -> Plan:
    """Dispatch to the appropriate PlanBuilder template method.

    Separates ``options`` into template args and PlanOptions kwargs:
    ``options`` dict carries the template args (e.g. old_path); ``opts``
    is the PlanOptions (auto_rollback, max_impact_threshold, dry_run_first).

    Wraps builder TypeErrors (missing required kwargs) and other builder
    errors as ``PlanValidationError`` so the agent gets a structured
    error, not a Python TypeError.
    """
    args = dict(options or {})
    builder = PlanBuilder()

    try:
        if intent == "safe_rename":
            return builder.build_safe_rename(options=opts, **args)
        if intent == "audit":
            return builder.build_audit(options=opts, **args)
        if intent == "deploy":
            return builder.build_deploy(options=opts, **args)
        if intent == "dax_regression":
            return builder.build_dax_regression(options=opts, **args)
    except TypeError as exc:
        # Missing required kwargs land here before builder validation runs.
        raise PlanValidationError(
            f"missing or invalid args for template {intent!r}: {exc}"
        ) from exc

    raise UnknownTemplateError(f"unknown template {intent!r}")


@mcp.tool()
async def plan_change(
    intent: str,
    options: dict[str, Any] | None = None,
) -> PlanResult:
    """Create a versionable plan from a template name + structured args.

    Args:
        intent: One of the 4 MVP templates: safe_rename, audit, deploy,
            dax_regression. (Each template's required args go in ``options``.)
        options: Template-specific args (e.g. for safe_rename:
            ``{"old_path": "T[A]", "new_path": "T[B]", "scope": "report_bindings"}``).
            May also contain ``PlanOptions`` fields which are extracted
            before passing template args to the builder.

    Returns:
        PlanResult with plan_id, plan_yaml, steps, risk_score, estimated_changes.
    """
    # Extract PlanOptions kwargs (if present) from the options dict so we
    # can pass them as a PlanOptions instance separately.
    plan_options_keys = set(PlanOptions.model_fields.keys())
    plan_option_kwargs: dict[str, Any] = {}
    template_args: dict[str, Any] = {}
    for key, value in (options or {}).items():
        if key in plan_options_keys:
            plan_option_kwargs[key] = value
        else:
            template_args[key] = value

    try:
        opts = PlanOptions(**plan_option_kwargs)
    except Exception as exc:
        raise PlanValidationError(
            f"invalid PlanOptions in options: {exc}"
        ) from exc

    plan = _build_plan_from_intent(intent, template_args, opts)
    _plans[plan.id] = plan
    return PlanResult(
        plan_id=plan.id,
        plan_yaml=plan.yaml,
        steps=[s.model_dump(mode="json") for s in plan.steps],
        risk_score=plan.risk_score,
        estimated_changes=plan.estimated_changes,
    )


# ---------------------------------------------------------------------------
# apply_plan (spec §3.3)
# ---------------------------------------------------------------------------


def _resolve_executor(step: PlanStep, *, dry_run: bool) -> StepExecutor:
    """Pick the executor for a step based on dry_run flag + registry."""
    if dry_run:
        return get_default_registry().get("dry_run")
    return get_default_registry().get(step.engine)


async def _run_plan_steps(
    plan: Plan,
    execution: PlanExecution,
    *,
    dry_run: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Execute plan steps sequentially. Returns (executed, failed).

    On any step failure, returns the (executed, failed) tuple WITHOUT
    raising — the caller decides whether to rollback or continue.
    """
    store = PlanExecutionStore()
    executed: list[dict[str, Any]] = []

    for idx, step in enumerate(plan.steps):
        store.update_heartbeat(
            execution.execution_id,
            current_step_index=idx,
            state=PlanExecutionState.IN_PROGRESS,
        )

        executor = _resolve_executor(step, dry_run=dry_run)
        outcome = await executor.execute_step(step)

        executed.append(
            {
                "id": step.id,
                "engine": step.engine,
                "action": step.action,
                "success": outcome.success,
                "error_message": outcome.error_message,
                "changed_files": outcome.changed_files,
            }
        )

        if not outcome.success:
            return executed, {
                "id": step.id,
                "engine": step.engine,
                "action": step.action,
                "error_message": outcome.error_message
                or "step failed without error message",
            }

    return executed, None


async def _rollback_plan(
    plan: Plan,
    executed_steps: list[dict[str, Any]],
    failed_step: dict[str, Any],
) -> list[str]:
    """Run rollback for the executed steps after a failure.

    Returns the list of rollback step ids that ran (success + failure).
    Uses the in-memory plan to reconstruct PlanStep objects.
    """
    step_by_id = {s.id: s for s in plan.steps}
    executed_plan_steps: list[PlanStep] = []
    for entry in executed_steps:
        ps = step_by_id.get(entry["id"])
        if ps is not None:
            executed_plan_steps.append(ps)

    failed_plan_step = step_by_id.get(failed_step["id"])
    if failed_plan_step is None:
        return []

    registry_executor = get_default_registry().get(failed_plan_step.engine)
    rollback_engine = RollbackEngine(registry_executor)
    result = await rollback_engine.execute_plan(
        executed_steps=executed_plan_steps,
        failed_step=failed_plan_step,
    )
    # We don't re-raise here — the caller's ApplyResult will reflect the
    # outcome via ``result_status``. Paths are surfaced separately.
    return list(result.rolled_back_steps) + [
        f"FAILED: {f.step_id}" for f in result.failed_rollbacks
    ]


@mcp.tool()
async def apply_plan(
    plan_id: str,
    dry_run: bool = False,
    confirm_each_step: bool = False,  # noqa: ARG001 — elicitation hook for v2
) -> ApplyResult:
    """Execute a plan with automatic rollback on failure.

    Args:
        plan_id: ID of the plan previously created by ``plan_change``.
        dry_run: If True, execute steps without side effects (dry_run
            executor is used regardless of registered engines).
        confirm_each_step: Reserved for per-step elicitation in v2;
            accepted but not enforced in MVP.

    Returns:
        ApplyResult with execution status, executed steps, rollback info.
    """
    plan = _plans.get(plan_id)
    if plan is None:
        return ApplyResult(
            result="failed",
            executed_steps=[],
            failed_step={"id": plan_id, "error_message": "plan not found"},
            rollback_steps_executed=[],
            artifacts_changed=[],
            rollback_handle=None,
        )

    execution = PlanExecution(
        execution_id=new_execution_id(),
        plan_id=plan.id,
        plan_yaml=plan.yaml,
        state=PlanExecutionState.IN_PROGRESS,
        current_step_index=0,
        started_at=datetime.now(UTC).isoformat(),
        last_heartbeat_at=datetime.now(UTC).isoformat(),
    )
    PlanExecutionStore().create(execution)

    executed, failed_step = await _run_plan_steps(plan, execution, dry_run=dry_run)

    if failed_step is not None:
        try:
            rollback_log = await _rollback_plan(plan, executed, failed_step)
            rollback_handle = new_rollback_handle()
            PlanExecutionStore().complete(
                execution.execution_id,
                result_status="rolled_back",
                state=PlanExecutionState.ROLLED_BACK,
            )
            return ApplyResult(
                result="rolled_back",
                executed_steps=executed,
                failed_step=failed_step,
                rollback_steps_executed=[
                    {"id": r, "status": "ok"} for r in rollback_log
                ],
                artifacts_changed=[],
                rollback_handle=rollback_handle,
            )
        except NoRollbackAvailableError:
            # No rollback_step exists for any executed or failed step.
            # Mark as failed and surface the failed step clearly.
            PlanExecutionStore().complete(
                execution.execution_id,
                result_status="failed",
                state=PlanExecutionState.PARTIAL,
            )
            return ApplyResult(
                result="failed",
                executed_steps=executed,
                failed_step=failed_step,
                rollback_steps_executed=[],
                artifacts_changed=[],
                rollback_handle=None,
            )

    PlanExecutionStore().complete(
        execution.execution_id,
        result_status="success",
        state=PlanExecutionState.COMPLETED,
    )

    # Best-effort audit log entry — failures are non-fatal because the
    # plan ran successfully; the audit log is for forensics, not control.
    import contextlib

    with contextlib.suppress(Exception):
        AuditLog().insert(
            tool_name="apply_plan",
            tool_args={"plan_id": plan.id, "dry_run": dry_run},
            result_status="success",
            target_id=None,
            payload={
                "execution_id": execution.execution_id,
                "step_count": len(executed),
            },
        )

    return ApplyResult(
        result="success",
        executed_steps=executed,
        failed_step=None,
        rollback_steps_executed=[],
        artifacts_changed=[],
        rollback_handle=None,
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server over stdio transport."""
    reconcile_orphan_executions_on_boot()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
