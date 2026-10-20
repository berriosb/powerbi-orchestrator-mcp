"""run_refresh tool — wraps RefreshOrchestrator (SPEC §6.1 tool #7)."""

from __future__ import annotations

from pydantic import BaseModel

from powerbi_orchestrator_mcp.cloud.auth import AuthConfig, FabricCredential
from powerbi_orchestrator_mcp.cloud.fabric_client import FabricClient
from powerbi_orchestrator_mcp.cloud.refresh import RefreshOrchestrator, RefreshResult


class RunRefresh(BaseModel):
    """Input schema for ``run_refresh`` tool (SPEC §6.1 #7)."""

    workspace_id: str
    dataset_id: str
    refresh_type: str = "full"
    commit_mode: str | None = None
    tables: list[str] | None = None
    wait: bool = True
    timeout_s: int = 1800
    auth_mode: str | None = None
    tenant_id: str | None = None
    client_id: str | None = None
    dry_run: bool = True


async def run_refresh(
    workspace_id: str,
    dataset_id: str,
    *,
    refresh_type: str = "full",
    commit_mode: str | None = None,
    tables: list[str] | None = None,
    wait: bool = True,
    timeout_s: int = 1800,
    auth_mode: str | None = None,
    tenant_id: str | None = None,
    client_id: str | None = None,
    dry_run: bool = True,
) -> RefreshResult:
    """Trigger (and optionally wait for) a dataset refresh.

    ``dry_run`` defaults to True: no refresh is triggered and the result
    reports ``status="DryRun"``. Real execution requires
    ``dry_run=False``.

    Returns RefreshResult per spec §3 (status, duration, errors,
    rollback_performed).
    """
    if dry_run:
        return RefreshResult(
            refresh_id="dry-run",
            status="DryRun",
            duration_ms=0,
            errors=[
                {
                    "code": "dry_run",
                    "message": (
                        f"dry_run: no refresh was triggered on dataset {dataset_id}; "
                        "pass dry_run=False to refresh for real"
                    ),
                }
            ],
            rollback_performed=False,
        )

    config = AuthConfig.from_env_or_args(
        mode=auth_mode,
        tenant_id=tenant_id,
        client_id=client_id,
    )
    cred = FabricCredential(config)
    client = FabricClient(cred)
    try:
        orchestrator = RefreshOrchestrator(client)
        return await orchestrator.run(
            workspace_id,
            dataset_id,
            refresh_type=refresh_type,
            commit_mode=commit_mode,
            tables=tables,
            wait=wait,
            timeout_s=timeout_s,
        )
    finally:
        await client.aclose()
