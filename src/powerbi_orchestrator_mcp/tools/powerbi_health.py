"""powerbi_health MCP tool — diagnostics for the user (v1.9.0).

Returns a snapshot of:
- Engine detection results (which subprocess engines are available).
- Plan + execution store stats (counts, last activity).
- Audit log stats (size, oldest entry).
- Server version + uptime.
- Optional: configuration hints for missing deps.

Intended for the LLM to surface "the orchestrator says it can't find
``powerbi-modeling-mcp`` — here's how to install it" instead of failing
the user's workflow.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp import __version__
from powerbi_orchestrator_mcp.orchestrator.audit import AUDIT_DB, count_entries
from powerbi_orchestrator_mcp.orchestrator.engine_detector import (
    INTEGRATED_ENGINES,
    detect_all_engines,
)
from powerbi_orchestrator_mcp.orchestrator.plan_executions import EXEC_DB
from powerbi_orchestrator_mcp.orchestrator.plan_store import PLAN_DB, PlanStore

SERVER_STARTED_AT = datetime.now(UTC)


class HealthCheck(BaseModel):
    """Single health-check entry."""

    name: str
    ok: bool
    detail: str | None = None


class PowerbiHealth(BaseModel):
    """Input schema (no params required)."""

    include_engine_details: bool = Field(
        default=True,
        description="Include per-engine version + reason-unavailable details.",
    )


class PowerbiHealthResult(BaseModel):
    """Output schema."""

    server_version: str
    uptime_seconds: float
    checks: list[HealthCheck] = Field(default_factory=list)
    engines: dict[str, dict[str, Any]] = Field(default_factory=dict)
    plan_store_count: int = 0
    plan_store_path: str = ""
    execution_count: int = 0
    execution_path: str = ""
    audit_entries: int = 0
    audit_path: str = ""
    warnings: list[str] = Field(default_factory=list)
    remediation: list[str] = Field(default_factory=list)


async def powerbi_health(
    include_engine_details: bool = True,
) -> dict[str, Any]:
    """Return a diagnostic snapshot of the orchestrator.

    The orchestrator itself is always "running" (we just answered a tool
    call). The interesting parts are:
    - Which engines are detected (powerbi-modeling-mcp, te, dscmd, ...).
    - How many plans / executions are persisted.
    - Whether the audit log exists and is being written to.
    """
    now = datetime.now(UTC)
    uptime = (now - SERVER_STARTED_AT).total_seconds()

    engines = await detect_all_engines()
    checks: list[HealthCheck] = []
    warnings: list[str] = []
    remediation: list[str] = []

    # Per-engine checks.
    engine_dict: dict[str, dict[str, Any]] = {}
    for name, status in engines.items():
        ok = status.available
        integrated = name in INTEGRATED_ENGINES
        detail = status.reason_unavailable
        if not integrated:
            # The engine is present on the machine but no tool
            # dispatches to it. Saying "unavailable" would be wrong —
            # it is available, it is just not reachable.
            detail = "detected but not integrated: no orchestrator tool dispatches to it"
            if status.reason_unavailable:
                detail = f"{detail} ({status.reason_unavailable})"
        checks.append(
            HealthCheck(
                name=f"engine.{name}",
                ok=ok,
                detail=None if ok and integrated else detail,
            )
        )
        engine_dict[name] = {
            "available": ok,
            "version": status.version,
            "integrated": integrated,
            "reason_unavailable": status.reason_unavailable,
            "note": None
            if integrated
            else ("detected but not integrated: no orchestrator tool dispatches to it"),
        }
        if not integrated:
            continue
        if not ok:
            warnings.append(f"engine {name!r} unavailable")
            if name == "powerbi-modeling-mcp":
                remediation.append(
                    "Install powerbi-modeling-mcp: `npx -y @microsoft/powerbi-modeling-mcp`"
                )
            elif name == "te":
                remediation.append(
                    "Install Tabular Editor: "
                    "https://github.com/TabularEditor/TabularEditor/releases"
                )
            elif name == "superbi-mcp":
                remediation.append("Install superbi-mcp: `npx -y superbi-mcp` (Windows-only, FSL)")

    # Plan store.
    plan_store = PlanStore()
    plan_count = plan_store.count()
    checks.append(
        HealthCheck(
            name="plan_store",
            ok=True,
            detail=f"{plan_count} plans in {PLAN_DB}",
        )
    )

    from powerbi_orchestrator_mcp.plugins import PLUGINS_ENV_VAR, load_plugins, plugins_enabled

    plugin_registry = load_plugins()
    if not plugins_enabled():
        detail = (
            "disabled by default; set "
            f"{PLUGINS_ENV_VAR}=1 to load plugins from the plugin directory"
        )
        if plugin_registry.errors:
            detail += f" ({plugin_registry.errors[0].error})"
            checks.append(HealthCheck(name="plugins", ok=False, detail=detail))
        else:
            checks.append(HealthCheck(name="plugins", ok=True, detail=detail))
    else:
        ok = not plugin_registry.has_errors()
        loaded = len(plugin_registry.loaded)
        detail = (
            f"{loaded} plugin(s) loaded, {len(plugin_registry.errors)} error(s)"
            if ok
            else "; ".join(err.error for err in plugin_registry.errors)
        )
        checks.append(HealthCheck(name="plugins", ok=ok, detail=detail))

    # Execution store.
    try:
        # Cheap: select COUNT(*) via direct SQL (no Pydantic roundtrip).
        import sqlite3

        conn = sqlite3.connect(str(EXEC_DB))
        try:
            row = conn.execute("SELECT COUNT(*) FROM plan_executions").fetchone()
            exec_count = int(row[0]) if row else 0
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        exec_count = 0
        warnings.append(f"execution store unavailable: {exc}")
    checks.append(
        HealthCheck(
            name="execution_store",
            ok=True,
            detail=f"{exec_count} executions in {EXEC_DB}",
        )
    )

    # Audit log.
    audit_count = count_entries()
    audit_ok = AUDIT_DB.exists()
    if not audit_ok:
        warnings.append("audit log does not exist yet (no apply_plan called)")
    checks.append(
        HealthCheck(
            name="audit_log",
            ok=audit_ok or audit_count > 0,
            detail=f"{audit_count} entries in {AUDIT_DB}",
        )
    )

    result = PowerbiHealthResult(
        server_version=__version__,
        uptime_seconds=round(uptime, 2),
        checks=checks,
        engines=engine_dict if include_engine_details else {},
        plan_store_count=plan_count,
        plan_store_path=str(PLAN_DB),
        execution_count=exec_count,
        execution_path=str(EXEC_DB),
        audit_entries=audit_count,
        audit_path=str(AUDIT_DB),
        warnings=warnings,
        remediation=remediation,
    )
    return result.model_dump(mode="json")


__all__ = ["PowerbiHealth", "PowerbiHealthResult", "powerbi_health"]
