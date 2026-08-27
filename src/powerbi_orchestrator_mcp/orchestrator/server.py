"""FastMCP entrypoint - registers all tools and runs the server."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Output schemas (spec sections 3.1-3.3)
# ---------------------------------------------------------------------------


class EngineStatus(BaseModel):
    """Status of a single engine detected during connect_target."""

    available: bool
    version: str | None = None
    reason_unavailable: str | None = None


class ConnectResult(BaseModel):
    """Output schema for connect_target (spec section 3.1)."""

    session_id: str
    engines_available: dict[str, EngineStatus]
    warnings: list[str] = Field(default_factory=list)


class EstimatedChanges(BaseModel):
    """Estimated impact of a plan."""

    files_affected: int = 0
    measures_affected: int = 0
    visuals_affected: int = 0
    rollback_complexity: str = "trivial"


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
    artifacts_changed: list[str] = Field(default_factory=list)
    rollback_handle: str | None = None


# ---------------------------------------------------------------------------
# FastMCP instance
# ---------------------------------------------------------------------------

mcp = FastMCP("powerbi-orchestrator-mcp")


# ---------------------------------------------------------------------------
# Tool stubs (spec sections 3.1-3.3) - will be replaced in task 1.10 / 1.11
# ---------------------------------------------------------------------------


@mcp.tool()
async def connect_target(
    target_type: str,  # noqa: ARG001
    target_ref: str,  # noqa: ARG001
    auth_mode: str = "interactive",  # noqa: ARG001
    tenant_id: str | None = None,  # noqa: ARG001
) -> ConnectResult:
    """Connect to a Power BI target and detect available engines.

    Args:
        target_type: Type of target (pbi_desktop, fabric_workspace, pbip_folder, pbix_file).
        target_ref: Reference to the target (path, workspace_id, etc.).
        auth_mode: Authentication mode (interactive, service_principal).
        tenant_id: Azure tenant ID (optional).

    Returns:
        ConnectResult with session_id, engines_available, and warnings.
    """
    return ConnectResult(
        session_id="stub-session-001",
        engines_available={},
        warnings=["stub implementation - no engines detected"],
    )


@mcp.tool()
async def plan_change(
    intent: str,  # noqa: ARG001
    options: dict[str, Any] | None = None,  # noqa: ARG001
) -> PlanResult:
    """Create a versionable plan from a natural language intent or template name.

    Args:
        intent: NL description or template name (e.g. 'safe_rename').
        options: Optional plan options (auto_rollback, max_impact_threshold, dry_run_first).

    Returns:
        PlanResult with plan_id, plan_yaml, steps, risk_score, and estimated_changes.
    """
    return PlanResult(
        plan_id="stub-plan-001",
        plan_yaml="# stub plan\nsteps: []\n",
        steps=[],
        risk_score=0.0,
        estimated_changes=EstimatedChanges(),
    )


@mcp.tool()
async def apply_plan(
    plan_id: str,  # noqa: ARG001
    dry_run: bool = False,  # noqa: ARG001
    confirm_each_step: bool = False,  # noqa: ARG001
) -> ApplyResult:
    """Execute a plan with automatic rollback on failure.

    Args:
        plan_id: ID of the plan to execute.
        dry_run: If True, execute without making changes.
        confirm_each_step: If True, elicit confirmation for each step.

    Returns:
        ApplyResult with execution status and details.
    """
    return ApplyResult(
        result="success",
        executed_steps=[],
        artifacts_changed=[],
        rollback_handle=None,
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server over stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
