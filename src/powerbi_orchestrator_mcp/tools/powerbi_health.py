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
        checks.append(
            HealthCheck(
                name=f"engine.{name}",
                ok=ok,
                detail=None
                if ok
                else (status.reason_unavailable or "unknown"),
            )
        )
        engine_dict[name] = {
            "available": ok,
            "version": status.version,
            "reason_unavailable": status.reason_unavailable,
        }
        if not ok:
            warnings.append(f"engine {name!r} unavailable")
            if name == "powerbi-modeling-mcp":
                remediation.append(
                    "Install powerbi-modeling-mcp: "
                    "`npx -y @microsoft/powerbi-modeling-mcp`"
                )
            elif name == "te":
                remediation.append(
                    "Install Tabular Editor: "
                    "https://github.com/TabularEditor/TabularEditor/releases"
                )
            elif name == "dscmd":
                remediation.append(
                    "Install DAX Studio CLI (Windows-only): "
                    "https://daxstudio.org/"
                )

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

    # Execution store.
    try:
        # Cheap: select COUNT(*) via direct SQL (no Pydantic roundtrip).
        import sqlite3

        with sqlite3.connect(str(EXEC_DB)) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM plan_executions"
            ).fetchone()
            exec_count = int(row[0]) if row else 0
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
