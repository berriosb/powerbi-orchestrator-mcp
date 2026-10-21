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

import asyncio
import contextvars
import json
import os
import re
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp import __version__
from powerbi_orchestrator_mcp.cloud.auth import (
    AuthConfig,
    FabricCredential,
    resolve_auth_mode,
)
from powerbi_orchestrator_mcp.cloud.fabric_client import FabricClient

if TYPE_CHECKING:
    from powerbi_orchestrator_mcp.orchestrator.transport import HttpConfig
from powerbi_orchestrator_mcp.orchestrator.audit import AuditLog
from powerbi_orchestrator_mcp.orchestrator.audit_guard import audited
from powerbi_orchestrator_mcp.orchestrator.authorization import requires_scope
from powerbi_orchestrator_mcp.orchestrator.context import (
    EngineStatus,
    SessionContext,
    SessionStore,
    Target,
)
from powerbi_orchestrator_mcp.orchestrator.elicitation import (
    ElicitationChoice,
    ElicitationRequest,
)
from powerbi_orchestrator_mcp.orchestrator.elicitation import (
    elicit as _elicit,
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
)
from powerbi_orchestrator_mcp.orchestrator.plan_executions import (
    reconcile_orphan_executions_on_boot as reconcile_orphan_executions_on_boot,
)
from powerbi_orchestrator_mcp.orchestrator.plan_models import (
    EstimatedChanges,
    Plan,
    PlanOptions,
    PlanStep,
    PlanValidationError,
    RiskThresholdExceededError,
    UnknownTemplateError,
)
from powerbi_orchestrator_mcp.orchestrator.plan_store import PlanStore
from powerbi_orchestrator_mcp.orchestrator.planner import PlanBuilder
from powerbi_orchestrator_mcp.orchestrator.rollback import (
    NoRollbackAvailableError,
    RollbackEngine,
    RollbackResult,
    StepExecutor,
)
from powerbi_orchestrator_mcp.orchestrator.step_executor import (
    get_default_registry,
)
from powerbi_orchestrator_mcp.tools.abbreviate_names import (
    abbreviate_names as _abbreviate,
)
from powerbi_orchestrator_mcp.tools.abstract_measures import (
    abstract_measures as _abstract,
)
from powerbi_orchestrator_mcp.tools.add_measure_with_validation import (
    add_measure_with_validation as _add_measure,
)
from powerbi_orchestrator_mcp.tools.apply_theme_and_accessibility_rules import (
    apply_theme_and_accessibility_rules as _apply_theme,
)
from powerbi_orchestrator_mcp.tools.audit_model_and_report import (
    audit_model_and_report as _audit,
)
from powerbi_orchestrator_mcp.tools.audit_report_ux_and_storytelling import (
    audit_report_ux_and_storytelling as _audit_ux,
)
from powerbi_orchestrator_mcp.tools.commit_workspace_to_git import (
    commit_workspace_to_git as _commit_ws,
)
from powerbi_orchestrator_mcp.tools.create_report_from_dataset import (
    create_report_from_dataset as _create_report,
)
from powerbi_orchestrator_mcp.tools.create_semantic_model_from_schema import (
    create_semantic_model_from_schema as _create_model,
)
from powerbi_orchestrator_mcp.tools.deploy_to_workspace import (
    deploy_to_workspace as _deploy,
)
from powerbi_orchestrator_mcp.tools.design_report_page_from_requirements import (
    design_report_page_from_requirements as _design_page,
)
from powerbi_orchestrator_mcp.tools.diff_models import diff_models as _diff
from powerbi_orchestrator_mcp.tools.edit_report_visual import (
    edit_report_visual as _edit_visual,
)
from powerbi_orchestrator_mcp.tools.generate_data_dictionary import (
    generate_data_dictionary as _data_dict,
)
from powerbi_orchestrator_mcp.tools.optimize_report_performance import (
    optimize_report_performance as _optimize_perf,
)
from powerbi_orchestrator_mcp.tools.powerbi_health import (
    powerbi_health as _powerbi_health,
)
from powerbi_orchestrator_mcp.tools.pre_deploy_check import (
    pre_deploy_check as _pre_deploy,
)
from powerbi_orchestrator_mcp.tools.promote_in_pipeline import (
    promote_in_pipeline as _promote,
)
from powerbi_orchestrator_mcp.tools.refactor_to_calculation_groups import (
    refactor_to_calculation_groups as _refactor,
)
from powerbi_orchestrator_mcp.tools.run_dax_regression import (
    run_dax_regression as _dax_regress,
)
from powerbi_orchestrator_mcp.tools.run_refresh import run_refresh as _refresh
from powerbi_orchestrator_mcp.tools.screenshot_report_pages import (
    screenshot_report_pages as _screenshot,
)
from powerbi_orchestrator_mcp.tools.select_visuals_for_kpis import (
    select_visuals_for_kpis as _select_visuals,
)
from powerbi_orchestrator_mcp.tools.set_sensitivity_labels import (
    set_sensitivity_labels as _set_labels,
)
from powerbi_orchestrator_mcp.tools.setup_rls_and_roles import (
    setup_rls_and_roles as _setup_rls,
)
from powerbi_orchestrator_mcp.tools.simplify_dax_measure import (
    simplify_dax_measure as _simplify_dax,
)
from powerbi_orchestrator_mcp.tools.sync_git_to_workspace import (
    sync_git_to_workspace as _sync_git,
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


MCP_INSTRUCTIONS = """Power BI & Microsoft Fabric Orchestrator MCP Server.

You are an expert Power BI and Microsoft Fabric solutions architect and data analyst assistant.
Use this server's tools to inspect, audit, query, author, and deploy Power BI and Fabric assets.

Core Workflows & Tool Selection:
1. Environment Health & Diagnostics:
   - Call `powerbi_health` first if you need to inspect available external engines (TMDL tools, DAX compilers, Tabular Editor) or provide setup guidance.

2. Auditing & Quality Gates:
   - Use `audit_model_and_report` for full project audits (Tabular BPA, DAX linting, WCAG 2.1 accessibility, naming conventions).
   - Use `optimize_report_performance` to evaluate report page rendering times and identify slow visual containers.
   - Use `audit_report_ux_and_storytelling` to critique dashboard UX, visual hierarchy, and cognitive load.
   - Use `pre_deploy_check` to evaluate audit findings against gate profiles before publishing.

3. Live Data & DAX Testing:
   - Use `execute_dax_query` to query live semantic models in Power BI Service / Fabric. Inspect table rows, test measures, or verify Row-Level Security via `impersonated_user_name`.
   - Use `run_dax_regression` to compare measure query outputs against a golden baseline.

4. Model Authoring & Changes:
   - Use `add_measure_with_validation` to write measures with automated syntax and best-practice linting.
   - Use `create_semantic_model_from_schema` to generate complete TMDL models and PBIP directories from YAML or JSON.
   - Use `setup_rls_and_roles` to configure and test Row-Level Security roles.
   - Use `refactor_to_calculation_groups` to consolidate repetitive time-intelligence measures.
   - Use `plan_change` (with `intent='safe_rename'`) and `apply_plan` for safe column/measure renames across model and visuals.

5. Visuals & Report Design:
   - Use `select_visuals_for_kpis` to find the most effective visual types for given business metrics and target audiences.
   - Use `design_report_page_from_requirements` to automatically generate a PBIR page layout from a natural language brief.
   - Use `create_report_from_dataset` to scaffold a starter report for a model.
   - Use `edit_report_visual` for surgical edits on individual chart containers.
   - Use `apply_theme_and_accessibility_rules` to apply colorblind-safe themes (e.g. Okabe-Ito) and backfill alt text.
   - Use `screenshot_report_pages` to capture visual wireframes or image snapshots.

6. Deployment & Governance:
   - Use `deploy_to_workspace` to run gates, publish PBIP, and configure scheduled refresh in Fabric.
   - Use `run_refresh` to trigger or poll dataset refreshes.
   - Use `promote_in_pipeline` for Fabric Deployment Pipelines (Dev -> Test -> Prod).
   - Use `commit_workspace_to_git` and `sync_git_to_workspace` for Git source-control integration.
   - Use `set_sensitivity_labels` for Microsoft Purview information protection.
   - Use `generate_data_dictionary` to export dataset documentation and Mermaid ER diagrams.

Rules & Guidelines:
- Parameters accepting JSON (findings_json, dax_measures_json, spec_json, kpis_json, fields_json) accept both serialized JSON strings and native JSON lists/dictionaries.
- Always prefer non-destructive dry-run checks (`dry_run=True`) when available before applying structural modifications.
"""

mcp = FastMCP("powerbi-orchestrator-mcp", instructions=MCP_INSTRUCTIONS)
mcp._mcp_server.version = __version__


# ---------------------------------------------------------------------------
# Module-level state (MVP in-memory plan + active session)
# ---------------------------------------------------------------------------

# The in-memory plan cache is an optimisation, not the source of truth:
# the plan store is. An evicted plan is still reachable through the
# store, so the cache can be bounded — which it has to be, because a
# long-running server would otherwise hold every plan it ever made,
# each with its full YAML and step list.
PLAN_CACHE_MAX_ENTRIES = 256

_plans: OrderedDict[str, Plan] = OrderedDict()
_plan_owners: dict[str, str] = {}
_plan_store = PlanStore()


def _cache_plan(plan: Plan, owner: str) -> None:
    """Put a plan in the cache, evicting the least recently used one."""
    _plans[plan.id] = plan
    _plan_owners[plan.id] = owner
    _plans.move_to_end(plan.id)
    while len(_plans) > PLAN_CACHE_MAX_ENTRIES:
        evicted, _ = _plans.popitem(last=False)
        _plan_owners.pop(evicted, None)


def _owner_now() -> str:
    from powerbi_orchestrator_mcp.orchestrator.authorization import (
        LOCAL_IDENTITY,
        current_identity,
    )

    return current_identity() or LOCAL_IDENTITY


def _owned_plan(plan_id: str) -> Plan | None:
    """Fetch a plan only if the current principal owns it.

    Both the in-memory cache and the store are filtered, so a plan created
    by one principal cannot be executed by another that knows its id.
    """
    from powerbi_orchestrator_mcp.orchestrator.authorization import LOCAL_IDENTITY

    owner = _owner_now()
    cached = _plans.get(plan_id)
    if cached is not None and _plan_owners.get(plan_id, LOCAL_IDENTITY) == owner:
        _plans.move_to_end(plan_id)
        return cached
    # The cache is bounded and the store is the source of truth, so a
    # plan that was evicted is still reachable here.
    return _plan_store.get(plan_id, owner=owner)


_session_state: dict[str, str | None] = {"active_id": None}
_active_session_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "active_session_id", default=None
)


def get_active_session_id() -> str | None:
    session_id = _active_session_var.get()
    if session_id is not None:
        return session_id
    from powerbi_orchestrator_mcp.orchestrator.transport import current_caller_claims

    if current_caller_claims() is not None or os.environ.get("PBI_TRANSPORT") == "http":
        return None
    return _session_state["active_id"]


def set_active_session_id(session_id: str | None) -> None:
    _active_session_var.set(session_id)
    from powerbi_orchestrator_mcp.orchestrator.transport import current_caller_claims

    if current_caller_claims() is None and os.environ.get("PBI_TRANSPORT") != "http":
        _session_state["active_id"] = session_id


def _reset_server_state() -> None:
    _plans.clear()
    _plan_owners.clear()
    _session_state["active_id"] = None
    _active_session_var.set(None)


def _parse_json_arg(val: Any, default: Any = None) -> Any:
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        val_str = val.strip()
        if not val_str:
            return default
        try:
            return json.loads(val_str)
        except json.JSONDecodeError:
            return default
    return default


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
        from powerbi_orchestrator_mcp.validation.path_safety import (
            FORBIDDEN_SYSTEM_PATHS,
        )

        resolved = Path(target_ref).resolve()
        resolved_str = str(resolved)
        for forbidden in FORBIDDEN_SYSTEM_PATHS:
            if resolved_str == forbidden or resolved_str.startswith(forbidden + "/"):
                return f"{target_type} refers to forbidden system path: {target_ref}"
        if not resolved.exists():
            return f"{target_type} path does not exist: {target_ref}"
        return None
    if target_type == "fabric_workspace":
        if not _FABRIC_ID_PATTERN.match(target_ref):
            return f"fabric_workspace ref should be a workspace ID, got {target_ref!r}"
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
        f"engine {name!r} unavailable: {engines[name].reason_unavailable or 'unknown reason'}"
        for name in missing
    ]


@mcp.tool()
@requires_scope("connect_target")
@audited("connect_target")
async def connect_target(
    target_type: str,
    target_ref: str,
    auth_mode: str | None = None,
    tenant_id: str | None = None,
) -> ConnectResult:
    """Connect to a Power BI target and initialize an orchestrator session.

    Use this tool when the user asks to:
    - Connect to a Power BI Desktop session, Fabric workspace, PBIP folder, or PBIX file.
    - Start an interactive session to inspect or modify Power BI assets.

    Args:
        target_type: One of "pbi_desktop", "fabric_workspace", "pbip_folder", "pbix_file".
        target_ref: Reference path (for PBIP/PBIX) or workspace UUID (for Fabric).
        auth_mode: "interactive" (browser login) or "service_principal". When
            omitted, ``PBI_AUTH_MODE`` is used, falling back to "interactive".
        tenant_id: Azure AD tenant ID (required when auth_mode is service_principal).

    Returns:
        ConnectResult with session_id, engines_available status, and any diagnostic warnings.
    """
    auth_mode = resolve_auth_mode(auth_mode)
    if target_type not in _TARGET_TYPES:
        raise ValueError(f"target_type must be one of {sorted(_TARGET_TYPES)}, got {target_type!r}")
    if auth_mode not in _AUTH_MODES:
        raise ValueError(f"auth_mode must be one of {sorted(_AUTH_MODES)}, got {auth_mode!r}")
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
    set_active_session_id(session_id)

    return ConnectResult(
        session_id=session_id,
        engines_available=engines,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# plan_change (spec §3.2)
# ---------------------------------------------------------------------------


def _active_session_target() -> str | None:
    """The target of the active session, when it is a local PBIP folder.

    Only a PBIP folder is a usable default for the plan templates: a
    Fabric workspace id is not a path, and defaulting a `pbip_path` to
    one would produce a plan that could never be applied.
    """
    active_sid = get_active_session_id()
    if not active_sid:
        return None
    ctx = SessionStore().get(active_sid)
    if ctx is None or ctx.target is None:
        return None
    if ctx.target.target_type != "pbip_folder":
        return None
    return ctx.target.target_ref or None


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
    session_target = _active_session_target()

    # The active session's target is a useful default, but only for the
    # templates that actually take one. `deploy` takes a pbip_path and
    # `dax_regression` a baseline_path; handing them an unexpected
    # `target` made both templates unusable.
    if intent in ("safe_rename", "audit"):
        if "target" not in args:
            if "pbip_path" in args:
                args["target"] = args["pbip_path"]
            elif session_target:
                args["target"] = session_target
    elif intent == "deploy" and "pbip_path" not in args and session_target:
        args["pbip_path"] = session_target

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
    except PlanValidationError:
        raise
    except RiskThresholdExceededError as exc:
        # The plan was refused for exceeding the caller's impact
        # threshold. The message says which option to raise.
        raise PlanValidationError(str(exc)) from exc
    except (TypeError, KeyError) as exc:
        # Missing required kwargs land here before builder validation runs.
        raise PlanValidationError(
            f"missing or invalid args for template {intent!r}: {exc}"
        ) from exc

    raise UnknownTemplateError(f"unknown template {intent!r}")


@mcp.tool()
@requires_scope("plan_change")
async def plan_change(
    intent: str,
    options: dict[str, Any] | None = None,
) -> PlanResult:
    """Create a versionable, multi-step execution plan from an intent template.

    Use this tool when the user asks to:
    - Plan a complex or risky operation requiring atomic execution or rollback capability.
    - Safely rename a table or column across both model and visual bindings (intent="safe_rename").
    - Plan an audit, deployment, or DAX regression run.

    Args:
        intent: One of the supported templates ("safe_rename", "audit", "deploy", "dax_regression").
        options: Template-specific arguments (e.g. for safe_rename: old_path, new_path, scope).
            May also contain PlanOptions fields (auto_rollback, max_impact_threshold, dry_run_first).

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
        raise PlanValidationError(f"invalid PlanOptions in options: {exc}") from exc

    plan = _build_plan_from_intent(intent, template_args, opts)
    owner = _owner_now()
    _cache_plan(plan, owner)
    _plan_store.put(plan, owner=owner)
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
    registry = get_default_registry()
    if not registry.has(step.engine):
        from powerbi_orchestrator_mcp.orchestrator.step_executor import (
            MissingEngineExecutor,
        )

        return MissingEngineExecutor(step.engine)
    if dry_run:
        return registry.get("dry_run")
    return registry.get(step.engine)


_CONFIRM_YES = {"y", "yes", "s", "si", "sí", "1", "true", "ok", "okay", "go", "run"}


async def _confirm_step(
    ctx: Context[Any, Any, Any] | None,
    step: PlanStep,
    *,
    index: int,
    total: int,
    dry_run: bool,
) -> tuple[bool, str]:
    """Ask the user to approve one plan step. Returns ``(approved, reason)``.

    Fails closed: if there is no MCP context, the client cannot elicit, or
    the prompt errors, the step is NOT run. A confirmation flag that silently
    executes anyway would be worse than no flag at all.
    """
    if ctx is None:
        return False, (
            "confirm_each_step=True requires an interactive MCP client with "
            "elicitation support; no context available, so the step was not run"
        )
    mode = "DRY RUN" if dry_run else "REAL EXECUTION"
    question = (
        f"[{index + 1}/{total}] {mode} — run step '{step.id}'?\n"
        f"  engine={step.engine} action={step.action}"
    )
    try:
        response = await _elicit(
            ctx,
            ElicitationRequest(
                question=question,
                choices=[
                    ElicitationChoice(label="Yes, run this step"),
                    ElicitationChoice(label="No, stop here"),
                ],
            ),
            bypass_rate_limit=True,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"confirmation prompt failed ({exc}); step was not run"

    if not response.accepted:
        return False, f"declined by user at step {index + 1}/{total}; step was not run"
    answer = str(response.values.get("response", "")).strip().lower()
    if answer in _CONFIRM_YES:
        return True, ""
    return False, (
        f"answer {answer!r} is not an explicit yes; step was not run (expected y/yes/si/ok)"
    )


async def _run_plan_steps(
    plan: Plan,
    execution: PlanExecution,
    *,
    dry_run: bool,
    ctx: Context[Any, Any, Any] | None = None,
    confirm_each_step: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Execute plan steps sequentially. Returns (executed, failed).

    On any step failure, returns the (executed, failed) tuple WITHOUT
    raising — the caller decides whether to rollback or continue.

    When ``confirm_each_step`` is set, each step is confirmed by the user
    before it runs. A decline (or an unavailable client) stops execution
    and reports the remaining steps as not run — it never silently
    continues, and it never silently skips the confirmation.
    """
    store = PlanExecutionStore()
    executed: list[dict[str, Any]] = []
    total = len(plan.steps)

    for idx, step in enumerate(plan.steps):
        if confirm_each_step:
            approved, reason = await _confirm_step(
                ctx, step, index=idx, total=total, dry_run=dry_run
            )
            if not approved:
                executed.append(
                    {
                        "id": step.id,
                        "engine": step.engine,
                        "action": step.action,
                        "success": False,
                        "error_message": reason,
                        "changed_files": [],
                        "skipped": True,
                    }
                )
                # Record the steps that will never run, so the caller sees
                # the full picture rather than a short executed_steps list.
                for remaining in plan.steps[idx + 1 :]:
                    executed.append(
                        {
                            "id": remaining.id,
                            "engine": remaining.engine,
                            "action": remaining.action,
                            "success": False,
                            "error_message": "not run: execution stopped by user",
                            "changed_files": [],
                            "skipped": True,
                        }
                    )
                return executed, {
                    "id": step.id,
                    "engine": step.engine,
                    "action": step.action,
                    "error_message": reason,
                    "stopped_by_user": True,
                }

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
                "details": outcome.details,
            }
        )

        if not outcome.success:
            return executed, {
                "id": step.id,
                "engine": step.engine,
                "action": step.action,
                "error_message": outcome.error_message or "step failed without error message",
            }

    return executed, None


async def _rollback_plan(
    plan: Plan,
    executed_steps: list[dict[str, Any]],
    failed_step: dict[str, Any],
) -> RollbackResult:
    """Run rollback for the executed steps after a failure.

    Returns the full ``RollbackResult``: the caller needs the status,
    because "everything was reverted" and "some of it could not be
    reverted" call for different answers.
    Uses the in-memory plan to reconstruct PlanStep objects. Passes a
    dispatcher to RollbackEngine so cross-engine rollback (e.g. safe_rename's
    modeling + report) routes each step to its correct engine.
    """
    step_by_id = {s.id: s for s in plan.steps}
    executed_plan_steps: list[PlanStep] = []
    for entry in executed_steps:
        ps = step_by_id.get(entry["id"])
        if ps is not None:
            executed_plan_steps.append(ps)

    failed_plan_step = step_by_id.get(failed_step["id"])
    if failed_plan_step is None:
        return RollbackResult(status="no_rollback", rolled_back_steps=[], failed_rollbacks=[])

    # Cross-engine dispatcher: routes each rollback step to its engine.
    registry = get_default_registry()

    def _dispatcher(step: PlanStep) -> StepExecutor:
        return registry.get(step.engine)

    rollback_engine = RollbackEngine(_dispatcher)
    return await rollback_engine.execute_plan(
        executed_steps=executed_plan_steps,
        failed_step=failed_plan_step,
    )


@mcp.tool()
@requires_scope("apply_plan")
@audited("apply_plan")
async def apply_plan(
    plan_id: str,
    dry_run: bool = True,
    confirm_each_step: bool = False,
    ctx: Context[Any, Any, Any] | None = None,
) -> ApplyResult:
    """Execute an approved plan with automatic rollback on step failure.

    Use this tool when the user asks to:
    - Execute or apply an approved plan created by plan_change.
    - Run plan steps in dry-run mode before modifying actual files.
    - Approve each step individually before it runs.

    Args:
        plan_id: ID of the plan previously created by plan_change.
        dry_run: If True (DEFAULT), simulate execution without modifying files
            or cloud resources. Pass dry_run=False explicitly to apply for real.
        confirm_each_step: If True, ask the user to approve every step before
            it runs. Declining (or having no interactive client) stops
            execution and reports the remaining steps as not run.

    Returns:
        ApplyResult with execution status, executed steps, and rollback details if needed.
    """
    plan = _owned_plan(plan_id)
    if plan is None:
        return ApplyResult(
            result="failed",
            executed_steps=[],
            failed_step={"id": plan_id, "error_message": "plan not found or not owned by caller"},
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

    executed, failed_step = await _run_plan_steps(
        plan,
        execution,
        dry_run=dry_run,
        ctx=ctx,
        confirm_each_step=confirm_each_step,
    )

    if failed_step is not None:
        # A user decline is not a failure: nothing needs unwinding, and
        # running the rollback path here would either report a bogus
        # "rolled_back" or trip NoRollbackAvailableError for steps that
        # were never executed.
        if failed_step.get("stopped_by_user"):
            PlanExecutionStore().complete(
                execution.execution_id,
                result_status="stopped_by_user",
                state=PlanExecutionState.PARTIAL,
            )
            return ApplyResult(
                result="stopped_by_user",
                executed_steps=executed,
                failed_step=failed_step,
                artifacts_changed=[],
            )
        try:
            rollback_result = await _rollback_plan(plan, executed, failed_step)
            rollback_handle = new_rollback_handle()
            # Report the rollback status that actually happened. A partial
            # rollback leaves the workspace in a mixed state and has to be
            # distinguishable from a clean one.
            if rollback_result.status == "rolled_back":
                plan_status = "rolled_back"
                plan_state = PlanExecutionState.ROLLED_BACK
            elif rollback_result.status == "partial":
                plan_status = "rollback_partial"
                plan_state = PlanExecutionState.PARTIAL
            else:
                # The rollback steps themselves failed. The workspace may
                # not be in the state it was in before the plan ran.
                plan_status = "rollback_incomplete"
                plan_state = PlanExecutionState.PARTIAL
            PlanExecutionStore().complete(
                execution.execution_id,
                result_status=plan_status,
                state=plan_state,
            )
            rollback_entries = (
                [{"id": step_id, "status": "ok"} for step_id in rollback_result.rolled_back_steps]
                + [
                    {
                        "id": f.step_id,
                        "status": "failed",
                        "error_message": f.error_message,
                        "paths_affected": f.paths_affected,
                    }
                    for f in rollback_result.failed_rollbacks
                ]
                + [
                    {
                        "id": step_id,
                        "status": "not_revertible",
                        "error_message": "step has no rollback step; its changes are still in place",
                        "paths_affected": [],
                    }
                    for step_id in rollback_result.non_revertible_steps
                ]
            )
            return ApplyResult(
                result=plan_status,
                executed_steps=executed,
                failed_step=failed_step,
                rollback_steps_executed=rollback_entries,
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
# Sprint 7: 8 high-level MVP tools wired here
# ---------------------------------------------------------------------------


@mcp.tool()
@requires_scope("audit_model_and_report")
async def audit_model_and_report(
    pbip_path: str,
    bpa_ruleset: str = "default",
    dax_measures_json: Any = "{}",
    bpa: bool = True,
    dax_lint: bool = True,
    accessibility: bool = True,
    naming: bool = True,
) -> dict[str, Any]:
    """Composite quality and compliance audit on a Power BI project (PBIP).

    Use this tool when the user asks to:
    - Audit, inspect, or validate a Power BI project (.pbip) or semantic model.
    - Check Best Practice Analyzer (BPA) rules, DAX code quality, or naming conventions.
    - Validate WCAG 2.1 accessibility (contrast, missing alt text, chart readability).

    Args:
        pbip_path: Path to the root .pbip directory or folder.
        bpa_ruleset: Best practice ruleset to run ("default", "strict", "lenient").
        dax_measures_json: Optional JSON string or dictionary mapping measure names to DAX expressions.
        bpa: Whether to execute Tabular BPA checks.
        dax_lint: Whether to execute static DAX linting checks.
        accessibility: Whether to audit WCAG 2.1 accessibility on report pages.
        naming: Whether to validate column, measure, and table naming conventions.

    Returns:
        Dict with overall score (0-100), pass/fail status, and categorized findings.
    """
    dax_measures = _parse_json_arg(dax_measures_json, default={})
    if not isinstance(dax_measures, dict):
        dax_measures = {}
    from powerbi_orchestrator_mcp.tools.audit_model_and_report import (
        AuditCheck as _AC,
    )

    result = await _audit(
        pbip_path=pbip_path,
        bpa_ruleset=bpa_ruleset,
        dax_measures=dax_measures,
        checks=_AC(bpa=bpa, dax_lint=dax_lint, accessibility=accessibility, naming=naming),
    )
    return result.model_dump(mode="json")


@mcp.tool()
@requires_scope("deploy_to_workspace")
@audited("deploy_to_workspace", target_arg="workspace_id")
async def deploy_to_workspace(
    pbip_path: str,
    workspace_id: str,
    refresh_daily_hour: int = 6,
    findings_json: Any = "[]",
    gate_profile: str = "standard",
    auth_mode: str | None = None,
    tenant_id: str | None = None,
    client_id: str | None = None,
    dry_run: bool = True,
    mock: bool = False,
) -> dict[str, Any]:
    """Deploy a PBIP project to a Fabric/Power BI workspace with pre-deploy gates.

    Use this tool when the user asks to:
    - Deploy, publish, or release a Power BI project (.pbip) to Microsoft Fabric or Power BI Service.
    - Run pre-deployment quality gates before publishing.
    - Schedule automatic daily dataset refreshes upon publication.

    Args:
        pbip_path: Local filesystem path to the root .pbip directory.
        workspace_id: Target Fabric / Power BI workspace ID (UUID).
        refresh_daily_hour: Daily UTC hour (0-23) for scheduled refresh (default: 6 AM UTC).
        findings_json: Optional list or JSON string of pre-existing audit findings to evaluate.
        gate_profile: Quality gate profile ("strict", "standard", "lenient").
        auth_mode: "interactive" (browser login) or "service_principal". When omitted,
            ``PBI_AUTH_MODE`` is used, falling back to "interactive".
        tenant_id: Azure AD tenant ID.
        client_id: Azure AD client ID.
        dry_run: When True (default) evaluates the gate and reports the planned
            operations WITHOUT calling any Fabric API. Pass dry_run=False to
            actually publish.
        mock: If True, simulate deployment without calling external APIs.

    Returns:
        Dict with deployment status, gate evaluation results, published item IDs, and refresh configuration.
    """
    findings = _parse_json_arg(findings_json, default=[])
    if not isinstance(findings, list):
        findings = []
    result = await _deploy(
        pbip_path=pbip_path,
        workspace_id=workspace_id,
        refresh_daily_hour=refresh_daily_hour,
        findings=findings,
        gate_profile=gate_profile,
        auth_mode=auth_mode,
        tenant_id=tenant_id,
        client_id=client_id,
        dry_run=dry_run,
        mock=mock,
    )
    out = result.model_dump(mode="json")
    if result.gate_result is not None:
        out["gate_result"] = result.gate_result.model_dump(mode="json")
    return out


@mcp.tool()
@requires_scope("run_refresh")
@audited("run_refresh", target_arg="dataset_id")
async def run_refresh(
    workspace_id: str,
    dataset_id: str,
    refresh_type: str = "full",
    wait: bool = True,
    timeout_s: int = 1800,
    auth_mode: str | None = None,
    tenant_id: str | None = None,
    client_id: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Trigger, monitor, and optionally wait for a Power BI dataset refresh.

    Use this tool when the user asks to:
    - Refresh data in a published Power BI semantic model.
    - Check the completion status of a refresh operation.
    - Perform full, automatic, or data-only refreshes.

    Args:
        workspace_id: Fabric / Power BI workspace ID (UUID).
        dataset_id: Dataset / semantic model ID (UUID).
        refresh_type: "full", "automatic", "data_only", "calculate", or "clearValues".
        wait: Whether to poll and wait for the refresh to complete before returning.
        timeout_s: Maximum wait time in seconds (default: 1800).
        auth_mode: "interactive" or "service_principal". When omitted,
            ``PBI_AUTH_MODE`` is used, falling back to "interactive".
        tenant_id: Azure AD tenant ID.
        client_id: Azure AD client ID.
        dry_run: When True (default) no refresh is triggered and status is
            "DryRun". Pass dry_run=False to actually refresh.

    Returns:
        Dict with refresh status, duration, error details, and rollback status if applicable.
    """
    result = await _refresh(
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        refresh_type=refresh_type,
        wait=wait,
        timeout_s=timeout_s,
        auth_mode=auth_mode,
        tenant_id=tenant_id,
        client_id=client_id,
        dry_run=dry_run,
    )
    return result.model_dump(mode="json")


@mcp.tool()
@requires_scope("run_dax_regression")
@audited("run_dax_regression")
async def run_dax_regression(
    baseline_path: str,
    queries_json: Any = "[]",
    tolerance_pct: float = 0.1,
    workspace_id: str | None = None,
    dataset_id: str | None = None,
    auth_mode: str | None = None,
    tenant_id: str | None = None,
    client_id: str | None = None,
    fabric_client: Any = None,
) -> dict[str, Any]:
    """Run DAX queries against a baseline and assert regression tolerance.

    Use this tool when the user asks to:
    - Verify that measures or models return consistent results across changes.
    - Compare live DAX calculation outputs against a golden baseline file.
    - Check numerical tolerances on calculation outputs during CI/CD.

    Args:
        baseline_path: Path to the JSON baseline file containing expected results.
        queries_json: Optional list or JSON string of DAX queries to execute.
        tolerance_pct: Maximum allowed percentage difference between actual and expected numeric values (default: 0.1%).
        workspace_id: Fabric / Power BI workspace ID holding the dataset.
        dataset_id: Published semantic model / dataset ID to query.
        auth_mode: "interactive" or "service_principal".
        tenant_id: Azure AD tenant ID.
        client_id: Azure AD client ID.
        fabric_client: Optional injected FabricClient instance (for testing).

    Returns:
        Dict containing diff summary, passed/failed queries, and variance details.
        ``executed=False`` means no query ran and no verdict is available.
    """
    queries = _parse_json_arg(queries_json, default=None)

    executor: Any = None
    close_client = False
    if workspace_id and dataset_id:
        if fabric_client is not None:
            client = fabric_client
        else:
            config = AuthConfig.from_env_or_args(
                mode=auth_mode,
                tenant_id=tenant_id,
                client_id=client_id,
            )
            client = FabricClient(FabricCredential(config))
            close_client = True
        executor = _fabric_dax_executor(client, workspace_id, dataset_id)

    try:
        result = await _dax_regress(
            baseline_path=baseline_path,
            queries=queries,
            tolerance_pct=tolerance_pct,
            query_executor=executor,
        )
    finally:
        if close_client and hasattr(client, "aclose"):
            await client.aclose()
    return result.model_dump(mode="json")


def _fabric_dax_executor(client: Any, workspace_id: str, dataset_id: str) -> Any:
    """Build a query executor backed by the Fabric executeQueries endpoint."""

    async def _run(query: str) -> list[dict[str, Any]]:
        payload = await client.execute_queries(
            workspace_id=workspace_id,
            dataset_id=dataset_id,
            queries=[{"query": query}],
        )
        rows: list[dict[str, Any]] = []
        for result in payload.get("results", []) if isinstance(payload, dict) else []:
            if not isinstance(result, dict):
                continue
            for table in result.get("tables", []) or []:
                if not isinstance(table, dict):
                    continue
                columns = [c.get("name") for c in table.get("columns", []) or []]
                for row in table.get("rows", []) or []:
                    if isinstance(row, list):
                        rows.append(dict(zip(columns, row, strict=False)))
                    else:
                        rows.append({"value": row})
        return rows

    return _run


DAX_ROW_LIMIT_CEILING = 10_000
_DAX_DEFAULT_MAX_ROWS = 1_000


def _cap_dax_rows(payload: Any, max_rows: int) -> tuple[dict[str, Any], bool]:
    """Truncate tabular rows in a Power BI query response to ``max_rows``.

    Returns ``(payload, truncated)``. Row data is the channel through which
    business records reach the model context, so an unbounded result can
    exfiltrate a full customer table in one tool call.
    """
    if not isinstance(payload, dict):
        return {}, False
    limit = max(1, min(int(max_rows), DAX_ROW_LIMIT_CEILING))
    results = payload.get("results")
    if not isinstance(results, list):
        return dict(payload), False
    budget = limit
    truncated = False
    new_results: list[Any] = []
    for result in results:
        if not isinstance(result, dict):
            new_results.append(result)
            continue
        tables = result.get("tables")
        if not isinstance(tables, list):
            new_results.append(result)
            continue
        new_tables: list[Any] = []
        for table in tables:
            if not isinstance(table, dict) or not isinstance(table.get("rows"), list):
                new_tables.append(table)
                continue
            rows = table["rows"]
            kept = rows[:budget] if budget > 0 else []
            if len(kept) < len(rows):
                truncated = True
            budget -= len(kept)
            new_table = dict(table)
            new_table["rows"] = kept
            new_tables.append(new_table)
        new_result = dict(result)
        new_result["tables"] = new_tables
        new_results.append(new_result)
    out = dict(payload)
    out["results"] = new_results
    if truncated:
        out["rows_truncated"] = True
        out["rows_limit"] = limit
    return out, truncated


@mcp.tool()
@requires_scope("execute_dax_query")
@audited("execute_dax_query", target_arg="dataset_id")
async def execute_dax_query(
    workspace_id: str,
    dataset_id: str,
    dax_query: str,
    impersonated_user_name: str | None = None,
    auth_mode: str | None = None,
    tenant_id: str | None = None,
    client_id: str | None = None,
    max_rows: int = _DAX_DEFAULT_MAX_ROWS,
    fabric_client: Any = None,
) -> dict[str, Any]:
    """Execute a DAX query against a published Power BI semantic model / Fabric dataset.

    Use this tool when the user asks to:
    - Run, evaluate, or test a DAX query against a live dataset in Power BI or Fabric.
    - Inspect actual business data, measure outputs, KPI calculations, or table rows.
    - Verify Row-Level Security (RLS) filters by simulating a specific user principal name.

    Args:
        workspace_id: Fabric / Power BI workspace ID (UUID).
        dataset_id: Published semantic model / dataset ID (UUID).
        dax_query: The DAX query expression (e.g. "EVALUATE TOPN(10, 'Sales')" or "EVALUATE ROW(\"Total\", [Total Sales])").
        impersonated_user_name: Optional User Principal Name (UPN) to test RLS rules as that user.
        auth_mode: "interactive" (default, browser login) or "service_principal".
        tenant_id: Azure AD tenant ID (required for service_principal).
        client_id: Azure AD client ID (for service_principal).
        max_rows: Maximum number of rows returned to the caller (default 1000,
            hard ceiling 10000). Prevents bulk extraction of business records
            into the model context.
        fabric_client: Optional injected FabricClient instance (for testing).

    Returns:
        Dict containing query execution results with tabular rows, columns, and execution metadata.
    """
    if fabric_client is not None:
        client = fabric_client
        close_client = False
    else:
        config = AuthConfig.from_env_or_args(
            mode=auth_mode,
            tenant_id=tenant_id,
            client_id=client_id,
        )
        cred = FabricCredential(config)
        client = FabricClient(cred)
        close_client = True

    try:
        trimmed = dax_query.strip()
        formatted_query = (
            trimmed
            if (trimmed.upper().startswith("EVALUATE") or trimmed.upper().startswith("DEFINE"))
            else f"EVALUATE {trimmed}"
        )
        if impersonated_user_name:
            # Running the query as another user reads the data that
            # user's row-level security is there to hide. The query
            # travels with the orchestrator's own credential, so the
            # caller would be reading rows the caller cannot see.
            # That is not a read.
            from powerbi_orchestrator_mcp.orchestrator.authorization import (
                SCOPE_WRITE,
                require_capability_scope,
            )

            require_capability_scope(
                SCOPE_WRITE,
                reason=(
                    "impersonating another user to read a dataset "
                    "(execute_dax_query with impersonated_user_name)"
                ),
            )
        result = await client.execute_queries(
            workspace_id=workspace_id,
            dataset_id=dataset_id,
            queries=[{"query": formatted_query}],
            impersonated_user_name=impersonated_user_name,
        )
        capped, _truncated = _cap_dax_rows(result, max_rows)
        return capped
    finally:
        if close_client and hasattr(client, "aclose"):
            await client.aclose()


@mcp.tool()
@requires_scope("diff_models")
async def diff_models(
    before: str,
    after: str,
) -> dict[str, Any]:
    """Compare two semantic models and report structural differences.

    Use this tool when the user asks to:
    - Compare two versions of a Power BI model or PBIP directory.
    - See what tables, columns, measures, or relationships changed between branches or releases.

    Args:
        before: Path to the baseline PBIP directory.
        after: Path to the target PBIP directory.

    Returns:
        Dict detailing added, removed, and modified tables, columns, measures, and relationships.
    """
    result = _diff(before=before, after=after)
    return result.model_dump(mode="json")


@mcp.tool()
@requires_scope("pre_deploy_check")
async def pre_deploy_check(
    findings_json: Any = "[]",
    profile: str = "standard",
) -> dict[str, Any]:
    """Evaluate audit findings against a pre-deployment quality gate profile.

    Use this tool when the user asks to:
    - Check whether audit findings block deployment or meet quality criteria.
    - Validate findings against 'strict', 'standard', or 'lenient' governance gates.

    Args:
        findings_json: List or JSON string of audit findings to evaluate.
        profile: Gate profile to evaluate against ("strict", "standard", "lenient").

    Returns:
        Dict with gate decision (passed=True/False), blocking findings, and warnings.
    """
    findings = _parse_json_arg(findings_json, default=[])
    if not isinstance(findings, list):
        findings = []
    result = _pre_deploy(findings, profile=profile)
    return result.model_dump(mode="json")


@mcp.tool()
@requires_scope("generate_data_dictionary")
@audited("generate_data_dictionary")
async def generate_data_dictionary(
    pbip_path: str,
    output_path: str | None = None,
    inspector: Any = None,
) -> dict[str, Any]:
    """Generate Markdown documentation and Mermaid ER diagram for a semantic model.

    Use this tool when the user asks to:
    - Document a Power BI dataset or semantic model.
    - Generate a data dictionary listing all tables, columns, types, and descriptions.
    - Create a Mermaid entity-relationship (ER) diagram of the model.

    Args:
        pbip_path: Path to the .pbip directory.
        output_path: Optional file path to save the generated Markdown.
        inspector: Optional model inspector.

    Returns:
        Dict with data dictionary markdown content, table count, measure count, and output path.
    """
    result = _data_dict(
        pbip_path=pbip_path,
        output_path=output_path,
        inspector=inspector,
    )
    return result.model_dump(mode="json")


@mcp.tool()
@requires_scope("apply_theme_and_accessibility_rules")
@audited("apply_theme_and_accessibility_rules")
async def apply_theme_and_accessibility_rules(
    pbip_path: str,
    palette: str = "okabe_ito",
    auto_backfill_alt_text: bool = True,
    alt_text_template: str = "{visual_type} visualizing measure {first_measure}",
) -> dict[str, Any]:
    """Apply a colorblind-safe theme, backfill visual alt text, and re-audit WCAG.

    Use this tool when the user asks to:
    - Make a report accessible and WCAG 2.1 compliant.
    - Apply a colorblind-safe palette (e.g. Okabe-Ito, ColorBrewer).
    - Automatically generate informative alt text for visuals lacking descriptions.

    Args:
        pbip_path: Path to the .pbip directory.
        palette: Colorblind-safe palette name ("okabe_ito", "colorbrewer").
        auto_backfill_alt_text: Whether to generate missing alt text on visuals.
        alt_text_template: Format template for generated alt text.

    Returns:
        Dict with updated WCAG score, modified visual count, and theme update details.
    """
    result = _apply_theme(
        pbip_path=pbip_path,
        palette=palette,
        auto_backfill_alt_text=auto_backfill_alt_text,
        alt_text_template=alt_text_template,
    )
    return result.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Sprint 8: v1.1 tools (SPEC §6.3)
# ---------------------------------------------------------------------------


@mcp.tool()
@requires_scope("add_measure_with_validation")
@audited("add_measure_with_validation")
async def add_measure_with_validation(
    target: str,
    measure_name: str,
    table: str,
    expression: str,
    format_string: str | None = None,
    description: str | None = None,
    is_hidden: bool = False,
    fail_on_severity: str = "warning",
    dry_run: bool = True,
    runtime_check: bool = False,
    measure_writer: Any = None,
) -> dict[str, Any]:
    """Add a DAX measure to a semantic model with automated linting and validation.

    Use this tool when the user asks to:
    - Create or add a new DAX measure to a Power BI model.
    - Validate DAX syntax and best practices (preventing division by zero, unformatted measures, etc.).
    - Dry-run a measure to check for lint issues before committing to TMDL.

    Args:
        target: Target PBIP directory or TMDL path.
        measure_name: Name of the measure to create.
        table: Target table where the measure will reside.
        expression: DAX formula for the measure (e.g. "DIVIDE([Total Sales], [Units], 0)").
        format_string: Format string (e.g. "$#,##0.00", "0.0%").
        description: Measure documentation or business description.
        is_hidden: Whether the measure should be hidden in report view.
        fail_on_severity: Minimum lint severity that blocks creation ("error", "warning", "info").
        dry_run: If True (DEFAULT), validate lint rules without writing to disk.
            Pass dry_run=False explicitly to persist the measure.
        runtime_check: Whether to execute the measure against an active engine if connected.
        measure_writer: Optional custom measure writer callable. Not reachable
            from an MCP client — the server wires the modeling engine instead.

    Returns:
        Dict with success status, lint findings, and modified file paths.
    """
    result = _add_measure(
        target=target,
        measure_name=measure_name,
        table=table,
        expression=expression,
        format_string=format_string,
        description=description,
        is_hidden=is_hidden,
        fail_on_severity=fail_on_severity,
        dry_run=dry_run,
        runtime_check=runtime_check,
        measure_writer=measure_writer,
    )
    return result


@mcp.tool()
@requires_scope("simplify_dax_measure")
async def simplify_dax_measure(
    expression: str | None = None,
    pbip_path: str | None = None,
    measure: str | None = None,
    all_measures: bool = False,
    apply_lint: bool = True,
) -> dict[str, Any]:
    """Rewrite a DAX expression in its shorter, equivalent form.

    Use this tool when the user asks to:
    - Simplify, tidy or shorten a DAX measure.
    - Find the measures in a model that are written the long way.
    - Replace a hand-rolled blank check, error check or IF chain with
      the function that already does it.

    Args:
        expression: The DAX expression to simplify. Mutually exclusive
            with pbip_path.
        pbip_path: A PBIP project to read the measures from.
        measure: The measure to simplify. Requires pbip_path.
        all_measures: Simplify every measure in the project and report
            which ones could be written shorter. Requires pbip_path.
        apply_lint: Include the linter verdict before and after, so a
            rewrite that silences a finding — or creates one — is visible.

    Returns:
        Dict with the simplified expression and the rewrites that
        produced it. The equivalence is *structural*: the expression is
        never evaluated against data, so the rewrite changes the shape
        and not the meaning, and a reader can check it. An expression
        that could not be parsed comes back unchanged, with the reason.

        This tool writes nothing. To apply the result, pass the
        simplified expression to add_measure_with_validation.
    """
    return _simplify_dax(
        expression=expression,
        pbip_path=pbip_path,
        measure=measure,
        all_measures=all_measures,
        apply_lint=apply_lint,
    )


@mcp.tool()
@requires_scope("abstract_measures")
async def abstract_measures(
    pbip_path: str,
    table: str | None = None,
    min_group_size: int = 2,
) -> dict[str, Any]:
    """Find the logic that is written more than once in a semantic model.

    Use this tool when the user asks to:
    - Find duplicated or copy-pasted logic across measures.
    - Know which measures compute the same thing and could share one.
    - Find the measures that differ only in a number or a column, which
      is what a calculation group is for.

    Args:
        pbip_path: Path to the root .pbip directory.
        table: Only look at the measures of this table.
        min_group_size: How many measures have to share a shape before
            it is reported. Two is the minimum.

    Returns:
        Dict with the groups found and, for each, what the duplication
        looks like: which measures share it and where exactly they
        differ.

        It does not write anything and does not apply the abstraction. A
        Tabular measure cannot take a parameter, so the answer is a
        calculation group, a helper table or a rewrite in place — and
        which one is right depends on the model.
    """
    return _abstract(
        pbip_path=pbip_path,
        table=table,
        min_group_size=min_group_size,
    )


@mcp.tool()
@requires_scope("abbreviate_names")
async def abbreviate_names(
    pbip_path: str,
    kind: str = "both",
    max_length: int = 40,
    abbreviate_words: bool = True,
    drop_filler_words: bool = True,
) -> dict[str, Any]:
    """Propose shorter names for the measures and columns that need them.

    Use this tool when the user asks to:
    - Shorten, abbreviate or tidy up measure and column names.
    - Find the names that have grown too long to read.
    - Prepare a batch of renames before applying them.

    Args:
        pbip_path: Path to the root .pbip directory.
        kind: "measures", "columns" or "both".
        max_length: Above this length, a name is a candidate.
        abbreviate_words: Shorten the words that have a standard
            abbreviation ("Amount" to "Amt", "Quantity" to "Qty").
        drop_filler_words: Drop the words that carry no meaning ("of",
            "the", "in").

    Returns:
        Dict with the proposals and a rename map ready for
        plan_change(intent="safe_rename").

        It does not rename anything. A name that cannot be shortened
        without changing what it says is reported as needing a name by
        hand, and a proposal that would collide with an existing name
        is adjusted.
    """
    return _abbreviate(
        pbip_path=pbip_path,
        kind=kind,
        max_length=max_length,
        abbreviate_words=abbreviate_words,
        drop_filler_words=drop_filler_words,
    )


@mcp.tool()
@requires_scope("create_report_from_dataset")
@audited("create_report_from_dataset")
async def create_report_from_dataset(
    pbip_path: str,
    page_name: str = "Overview",
    visual_count: int = 2,
    theme: str = "okabe_ito",
    include_card: bool = True,
    inspector: Any = None,
) -> dict[str, Any]:
    """Scaffold a PBIR report folder and layout from an existing semantic model.

    Use this tool when the user asks to:
    - Create a new report (.Report folder) for an existing dataset.
    - Generate starter report pages with cards, charts, and an accessible theme.

    Args:
        pbip_path: Path to the .pbip directory containing the dataset.
        page_name: Name of the initial report page (default: "Overview").
        visual_count: Number of starter visuals to generate.
        theme: Theme name to apply (default: "okabe_ito").
        include_card: Whether to generate a top-line KPI card visual.
        inspector: Optional model inspector.

    Returns:
        Dict with created page path, visual IDs, and scaffolded report files.
    """
    result = _create_report(
        pbip_path=pbip_path,
        page_name=page_name,
        visual_count=visual_count,
        theme=theme,
        include_card=include_card,
        inspector=inspector,
    )
    return result


@mcp.tool()
@requires_scope("edit_report_visual")
@audited("edit_report_visual")
async def edit_report_visual(
    pbip_path: str,
    page_name: str,
    visual_id: str,
    type: str | None = None,
    fields_json: Any = None,
    format_json: Any = None,
    position_json: Any = None,
    alt_text: str | None = None,
    is_hidden: bool | None = None,
) -> dict[str, Any]:
    """Modify a visualContainer in a PBIR report page (type, fields, formatting, layout).

    Use this tool when the user asks to:
    - Edit, reformat, or resize a specific chart/visual on a report page.
    - Change visual fields, bindings, alt text, or visibility.

    Args:
        pbip_path: Path to the root .pbip directory.
        page_name: Name of the page containing the visual.
        visual_id: Unique ID of the visualContainer to edit.
        type: New visual type if changing (e.g. "barChart", "lineChart", "card").
        fields_json: Optional dict or JSON string specifying field bindings to update.
        format_json: Optional dict or JSON string specifying formatting options.
        position_json: Optional dict or JSON string with x, y, width, height layout.
        alt_text: New alt text for accessibility.
        is_hidden: Whether to hide the visualContainer.

    Returns:
        Dict with success status, changes applied, and page path.
    """
    fields_str = json.dumps(fields_json) if isinstance(fields_json, (dict, list)) else fields_json
    format_str = json.dumps(format_json) if isinstance(format_json, (dict, list)) else format_json
    position_str = (
        json.dumps(position_json) if isinstance(position_json, (dict, list)) else position_json
    )
    result = _edit_visual(
        pbip_path=pbip_path,
        page_name=page_name,
        visual_id=visual_id,
        type=type,
        fields_json=fields_str,
        format_json=format_str,
        position_json=position_str,
        alt_text=alt_text,
        is_hidden=is_hidden,
    )
    return result


# ---------------------------------------------------------------------------
# Sprint 9: v2 tools (SPEC §6.2)
# ---------------------------------------------------------------------------


@mcp.tool()
@requires_scope("refactor_to_calculation_groups")
@audited("refactor_to_calculation_groups")
async def refactor_to_calculation_groups(
    target: str,
    min_candidates: int = 3,
    reconcile_strategy: str = "strict",
    preserve_originals: bool = False,
    auto_apply: bool = False,
    *,
    inspector: Any = None,
    measure_writer: Any = None,
) -> dict[str, Any]:
    """Consolidate repetitive measures (e.g. YTD, QTD, PY) into calculation groups.

    Use this tool when the user asks to:
    - Refactor or clean up redundant DAX measures using calculation groups.
    - Reduce model complexity and standardize time intelligence calculations.

    Args:
        target: Target PBIP directory or TMDL path.
        min_candidates: Minimum measure patterns needed to trigger consolidation.
        reconcile_strategy: "strict" or "lenient".
        preserve_originals: Whether to keep original measures alongside the calculation group.
        auto_apply: If True, write calculation items immediately; if False, return proposed refactoring plan.
        inspector: Optional model inspector.
        measure_writer: Optional measure writer callable.

    Returns:
        Dict with proposed or applied calculation items, candidate measures, and impact assessment.
    """
    result = _refactor(
        target=target,
        min_candidates=min_candidates,
        reconcile_strategy=reconcile_strategy,
        preserve_originals=preserve_originals,
        auto_apply=auto_apply,
        inspector=inspector,
        measure_writer=measure_writer,
    )
    return result.model_dump(mode="json")


@mcp.tool()
@requires_scope("select_visuals_for_kpis")
async def select_visuals_for_kpis(
    kpis_json: Any,
    audience: str = "executive",
    max_results: int = 3,
    *,
    inspector: Any = None,
) -> dict[str, Any]:
    """Recommend optimal visual types and chart configurations for given KPIs.

    Use this tool when the user asks to:
    - Choose the best charts or visual types for a specific set of KPIs or metrics.
    - Tailor visual recommendations to an audience ('executive', 'analytical', 'operational').
    - Get primary and alternative chart suggestions with rationale based on data types.

    Args:
        kpis_json: List of KPIs or JSON string (each with name, semantic_type, fields, etc.).
        audience: Target persona ("executive", "analytical", "operational").
        max_results: Maximum number of alternative visual recommendations per KPI.
        inspector: Optional model inspector providing column cardinality and schema info.

    Returns:
        Dict with recommended primary visual, alternatives, and rationale for each KPI.
    """
    raw_kpis = _parse_json_arg(kpis_json, default=[])
    payload = json.dumps(raw_kpis) if not isinstance(kpis_json, str) else kpis_json
    result = _select_visuals(
        kpis_json=payload,
        audience=audience,
        max_results=max_results,
        inspector=inspector,
    )
    return result.model_dump(mode="json")


@mcp.tool()
@requires_scope("design_report_page_from_requirements")
@audited("design_report_page_from_requirements")
async def design_report_page_from_requirements(
    pbip_path: str,
    brief: str,
    page_name: str = "Overview",
    audience: str = "executive",
    palette: str = "okabe_ito",
    *,
    inspector: Any = None,
) -> dict[str, Any]:
    """Synthesize a complete PBIR report page layout from a natural language brief.

    Use this tool when the user asks to:
    - Design or generate a new Power BI report page based on business requirements.
    - Automatically select, size, position, and format visuals matching an analytical goal.
    - Apply professional color schemes and visual hierarchy to a page.

    Args:
        pbip_path: Path to the target .pbip directory.
        brief: Natural language description of what the report page should convey.
        page_name: Display name for the newly created report page (default: "Overview").
        audience: Target audience ("executive", "analytical", "operational").
        palette: Color palette name (default: "okabe_ito").
        inspector: Optional model inspector for schema context.

    Returns:
        Dict containing synthesized visual containers, positions, and page metadata.
    """
    result = _design_page(
        pbip_path=pbip_path,
        brief=brief,
        page_name=page_name,
        audience=audience,
        palette=palette,
        inspector=inspector,
    )
    return result.model_dump(mode="json")


@mcp.tool()
@requires_scope("optimize_report_performance")
async def optimize_report_performance(
    pbip_path: str,
    target_load_ms: int = 5000,
) -> dict[str, Any]:
    """Analyze PBIR report pages for visual performance bottlenecks and anti-patterns.

    Use this tool when the user asks to:
    - Diagnose slow-loading report pages or improve visual performance.
    - Identify excessive visual density, expensive custom visuals, or unoptimized filters.

    Args:
        pbip_path: Path to the .pbip directory.
        target_load_ms: Desired maximum page load latency in milliseconds (default: 5000 ms).

    Returns:
        Dict with performance score (0-100), estimated load time, and actionable recommendations.
    """
    result = _optimize_perf(
        pbip_path=pbip_path,
        target_load_ms=target_load_ms,
    )
    return result.model_dump(mode="json")


@mcp.tool()
@requires_scope("audit_report_ux_and_storytelling")
async def audit_report_ux_and_storytelling(
    pbip_path: str,
    page_name: str | None = None,
    audience_assumed: str | None = None,
    strictness: str = "standard",
) -> dict[str, Any]:
    """Evaluate report storytelling, visual hierarchy, cognitive load, and UX design.

    Use this tool when the user asks to:
    - Audit report design quality, narrative flow, or visual hierarchy.
    - Check if a report follows dashboard best practices for a specific audience.

    Args:
        pbip_path: Path to the .pbip directory.
        page_name: Optional specific page name to audit; audits all pages if omitted.
        audience_assumed: Target audience ("executive", "analytical", "operational").
        strictness: Scoring strictness ("lenient", "standard", "strict").

    Returns:
        Dict with UX score (0-100), category breakdowns (hierarchy, density, narrative), and suggestions.
    """
    result = _audit_ux(
        pbip_path=pbip_path,
        page_name=page_name,
        audience_assumed=audience_assumed,
        strictness=strictness,
    )
    return result.model_dump(mode="json")


@mcp.tool()
@requires_scope("screenshot_report_pages")
@audited("screenshot_report_pages")
async def screenshot_report_pages(
    pbip_path: str,
    pages: list[str] | None = None,
    format: str = "png",
    resolution: str = "desktop",
    output_dir: str = "./screenshots",
    wait_ms: int = 2000,
) -> dict[str, Any]:
    """Capture screenshots or structural wireframes of Power BI report pages.

    Use this tool when the user asks to:
    - Visually inspect or capture report pages for reviews or regression diffs.
    - Generate SVG wireframes or image snapshots of PBIR layouts.

    Args:
        pbip_path: Path to the .pbip directory.
        pages: Optional list of specific page names to capture.
        format: Output format ("png", "svg", "pdf").
        resolution: Target resolution ("desktop", "mobile", "tablet").
        output_dir: Directory where captured images are written.
        wait_ms: Time in ms to wait for visual rendering.

    Returns:
        Dict with output image paths, warnings, and rendering metadata.
    """
    result = await asyncio.to_thread(
        _screenshot,
        pbip_path=pbip_path,
        pages=pages,
        format=format,
        resolution=resolution,
        output_dir=output_dir,
        wait_ms=wait_ms,
    )
    return result.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Sprint 11: v2 tools — model authoring (SPEC §6.2 + 02-cloud-fabric §3)
# ---------------------------------------------------------------------------


@mcp.tool()
@requires_scope("create_semantic_model_from_schema")
@audited("create_semantic_model_from_schema")
async def create_semantic_model_from_schema(
    spec_yaml: str | None = None,
    spec_json: Any = None,
    output_pbip_path: str = "",
    dry_run: bool = True,
) -> dict[str, Any]:
    """Generate a TMDL semantic model and PBIP project from a declarative schema spec.

    Use this tool when the user asks to:
    - Create, scaffold, or generate a new Power BI semantic model from scratch.
    - Define tables, columns, data types, relationships, and hierarchies declaratively.

    Args:
        spec_yaml: YAML specification of tables, columns, types, and relationships.
        spec_json: JSON specification (either as a string or a structured object/dict).
        output_pbip_path: Target directory to write the generated .pbip project.
        dry_run: If True, validate specification without writing files to disk.

    Returns:
        Dict with tables created, relationships created, hierarchies created, and validation status.
    """
    spec_json_payload = (
        json.dumps(spec_json)
        if spec_json is not None and isinstance(spec_json, (dict, list))
        else spec_json
    )
    result = _create_model(
        spec_yaml=spec_yaml,
        spec_json=spec_json_payload,
        output_pbip_path=output_pbip_path,
        dry_run=dry_run,
    )
    return result.model_dump(mode="json")


@mcp.tool()
@requires_scope("setup_rls_and_roles")
@audited("setup_rls_and_roles")
async def setup_rls_and_roles(
    target: str,
    spec_yaml: str | None = None,
    spec_json: Any = None,
    dry_run: bool = True,
    rollback_on_test_failure: bool = True,
) -> dict[str, Any]:
    """Configure Row-Level Security (RLS) roles and validation rules in a TMDL model.

    Use this tool when the user asks to:
    - Set up, add, or configure RLS roles and DAX table filter expressions.
    - Test and validate security rules against sample queries.

    Args:
        target: Target PBIP directory or TMDL path.
        spec_yaml: YAML specification of security roles, members, and DAX filters.
        spec_json: JSON specification (either as a string or a structured object/dict).
        dry_run: If True, validate role specification without writing to disk.
        rollback_on_test_failure: Whether to revert modifications if test queries fail.

    Returns:
        Dict with roles created, test query outcomes, and rollback status if applicable.
    """
    spec_json_payload = (
        json.dumps(spec_json)
        if spec_json is not None and isinstance(spec_json, (dict, list))
        else spec_json
    )
    result = _setup_rls(
        target=target,
        spec_yaml=spec_yaml,
        spec_json=spec_json_payload,
        dry_run=dry_run,
        rollback_on_test_failure=rollback_on_test_failure,
    )
    return result.model_dump(mode="json")


@mcp.tool()
@requires_scope("promote_in_pipeline")
@audited("promote_in_pipeline")
async def promote_in_pipeline(
    pipeline_id: str,
    source_stage: str = "dev",
    target_stage: str = "test",
    items: list[str] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Promote artifacts across Microsoft Fabric Deployment Pipeline stages.

    Use this tool when the user asks to:
    - Promote or move items between Fabric deployment stages (e.g. dev to test, test to prod).
    - Run pre-promotion quality gates before moving items.

    Args:
        pipeline_id: Fabric deployment pipeline ID (UUID).
        source_stage: Source stage ("dev", "test", "prod").
        target_stage: Target stage ("test", "prod").
        items: Optional list of specific item IDs to promote. Promotes all if omitted.
        dry_run: If True (DEFAULT), validate stages and gate checks without
            triggering actual promotion. Pass dry_run=False explicitly to
            promote for real — this moves live artifacts between stages.

    Returns:
        Dict with promotion status, gate outcomes, and affected items.
    """
    result = _promote(
        pipeline_id=pipeline_id,
        source_stage=source_stage,
        target_stage=target_stage,
        items=items,
        dry_run=dry_run,
    )
    return result.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Sprint 12: v3 tools (governance + Git sync)
# ---------------------------------------------------------------------------


@mcp.tool()
@requires_scope("commit_workspace_to_git")
@audited("commit_workspace_to_git")
async def commit_workspace_to_git(
    workspace_id: str,
    output_repo_path: str,
    branch: str | None = None,
    commit_message: str | None = None,
    exclude_items: list[str] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Export and commit a Fabric workspace into a local Git repository.

    Use this tool when the user asks to:
    - Back up or version-control a Fabric workspace into Git.
    - Snapshot reports and semantic models into local PBIP files with Git commits.

    Args:
        workspace_id: Source Fabric workspace ID (UUID).
        output_repo_path: Local path to destination Git repository.
        branch: Git branch to commit into.
        commit_message: Commit message describing the snapshot.
        exclude_items: Optional list of item IDs to exclude.
        dry_run: If True, inspect items without creating git commits.

    Returns:
        Dict with committed items, commit SHA, and repository status.
    """
    result = _commit_ws(
        workspace_id=workspace_id,
        output_repo_path=output_repo_path,
        branch=branch,
        commit_message=commit_message,
        exclude_items=exclude_items,
        dry_run=dry_run,
    )
    return result.model_dump(mode="json")


@mcp.tool()
@requires_scope("sync_git_to_workspace")
@audited("sync_git_to_workspace")
async def sync_git_to_workspace(
    repo_path: str,
    workspace_id: str,
    branch_or_commit: str = "HEAD",
    conflict_resolution: str = "manual",
    dry_run: bool = True,
) -> dict[str, Any]:
    """Deploy a local Git repository with PBIP projects to a Fabric workspace.

    Use this tool when the user asks to:
    - Synchronize or publish a local Git repository or branch to a Fabric workspace.
    - Update workspace items based on version-controlled PBIP files.

    Args:
        repo_path: Path to the local Git repository.
        workspace_id: Target Fabric workspace ID (UUID).
        branch_or_commit: Git ref to sync (default: "HEAD").
        conflict_resolution: Conflict handling strategy ("manual", etc.).
        dry_run: If True, calculate changes without publishing.

    Returns:
        Dict with synchronized items, skipped items, and deployment results.
    """
    result = _sync_git(
        repo_path=repo_path,
        workspace_id=workspace_id,
        branch_or_commit=branch_or_commit,
        conflict_resolution=conflict_resolution,
        dry_run=dry_run,
    )
    return result.model_dump(mode="json")


@mcp.tool()
@requires_scope("set_sensitivity_labels")
@audited("set_sensitivity_labels")
async def set_sensitivity_labels(
    items: list[dict[str, str]],
    label_id: str,
    label_name: str,
    admin_scopes: list[str] | None = None,
    redact_names: bool = True,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Apply Microsoft Purview information protection sensitivity labels to items.

    Use this tool when the user asks to:
    - Classify or protect Power BI items (reports, semantic models, dashboards).
    - Set Purview sensitivity labels (Confidential, General, Highly Confidential).

    Args:
        items: List of dicts specifying item IDs and types (e.g. [{"id": "...", "type": "Report"}]).
        label_id: Microsoft Purview label GUID.
        label_name: Display name of the sensitivity label.
        admin_scopes: Optional list of administrative authorization scopes.
        redact_names: Whether to redact item names in returned logs for security.
        dry_run: If True, validate permissions without applying labels.

    Returns:
        Dict with updated items, failed items, and compliance status.
    """
    result = _set_labels(
        items=items,
        label_id=label_id,
        label_name=label_name,
        admin_scopes=admin_scopes,
        redact_names=redact_names,
        dry_run=dry_run,
    )
    return result.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Sprint 16: diagnostics + observability
# ---------------------------------------------------------------------------


@mcp.tool()
@requires_scope("powerbi_health")
async def powerbi_health(
    include_engine_details: bool = True,
) -> dict[str, Any]:
    """Diagnose orchestrator health, detected modeling engines, and storage readiness.

    Use this tool when the user asks to:
    - Check if the Power BI MCP orchestrator is running properly.
    - See which external tools/engines are installed (Tabular Editor, DAX optimizer, etc.).
    - Get installation or setup instructions for missing components.

    Args:
        include_engine_details: Whether to return full diagnostic info and remediation tips for each engine.

    Returns:
        Dict with system status, engine availability matrix, active store counts, and remediation advice.
    """
    return await _powerbi_health(
        include_engine_details=include_engine_details,
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _apply_transport_security(http_cfg: HttpConfig) -> None:
    """Widen the Host allowlist to match the address the server binds.

    The SDK enables DNS-rebinding protection with a loopback-only
    allowlist when the server is created, and that allowlist does not
    follow a later change of the bind host. A server bound to a
    container address would then answer 421 Misdirected Request to
    every client. The protection stays on; only the allowlist grows.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    current = mcp.settings.transport_security
    allowed_hosts = list(current.allowed_hosts) if current else []
    allowed_origins = list(current.allowed_origins) if current else []

    extra = list(http_cfg.allowed_hosts)
    if http_cfg.host not in {"127.0.0.1", "localhost", "::1"}:
        extra.append(f"{http_cfg.host}:*")
    for host in extra:
        if host not in allowed_hosts:
            allowed_hosts.append(host)
        origin = f"http://{host}"
        if origin not in allowed_origins:
            allowed_origins.append(origin)

    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def main() -> None:
    """Run the MCP server.

    Default transport is stdio (for Claude Desktop, VS Code, Cursor).
    Pass `--transport http` (or set `PBI_TRANSPORT=http`) to expose
    the server over Streamable HTTP with Entra ID authentication.

    See `specs/architecture/07-http-transport.md` for the design.
    """
    import sys

    from powerbi_orchestrator_mcp.orchestrator.transport import (
        parse_transport_args,
    )

    # Peek at argv; if --transport is absent, fast-path to stdio
    # without argparse overhead (preserves fast startup for stdio).
    #
    # PBI_TRANSPORT counts too: a container has no arguments to pass,
    # and the documentation promises the variable works. Without this,
    # the container started in stdio and published no port.
    _env_transport = os.environ.get("PBI_TRANSPORT", "").strip().lower()
    _transport_selected = (
        "--transport" in sys.argv
        or any(a.startswith("--transport=") for a in sys.argv)
        or _env_transport == "http"
    )
    if not _transport_selected:
        reconcile_orphan_executions_on_boot()
        mcp.run(transport="stdio")
        return

    transport, http_cfg = parse_transport_args()

    if transport == "stdio":
        reconcile_orphan_executions_on_boot()
        mcp.run(transport="stdio")
        return

    from powerbi_orchestrator_mcp.orchestrator.transport import (
        EntraAuthMiddleware,
        auth_middleware_factory,
    )

    if http_cfg is None:
        sys.stderr.write("error: missing HTTP configuration\n")
        raise SystemExit(2)

    _validate_request = auth_middleware_factory(http_cfg)
    mcp.settings.host = http_cfg.host
    mcp.settings.port = http_cfg.port
    mcp.settings.mount_path = http_cfg.mount_path
    # `PBI_LOG_LEVEL` is what the deployment docs tell an operator to
    # set; honour it instead of silently using the default.
    log_level = os.environ.get("PBI_LOG_LEVEL")
    if log_level:
        mcp.settings.log_level = log_level.upper()  # type: ignore[assignment]
    _apply_transport_security(http_cfg)
    reconcile_orphan_executions_on_boot()

    import anyio
    import uvicorn

    starlette_app = mcp.streamable_http_app()
    authed_app = EntraAuthMiddleware(starlette_app, _validate_request)
    config = uvicorn.Config(
        authed_app,
        host=http_cfg.host,
        port=http_cfg.port,
        log_level=mcp.settings.log_level.lower(),
    )
    server = uvicorn.Server(config)
    anyio.run(server.serve)


if __name__ == "__main__":
    main()
