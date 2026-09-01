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
    refresh_type: str = "full"  # full | automatic | data_only | calculate | clearValues
    commit_mode: str | None = None  # transactional | partialBatch
    tables: list[str] | None = None
    wait: bool = True
    timeout_s: int = 1800  # 30 min default per spec §3
    auth_mode: str = "interactive"
    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = None


async def run_refresh(
    workspace_id: str,
    dataset_id: str,
    *,
    refresh_type: str = "full",
    commit_mode: str | None = None,
    tables: list[str] | None = None,
    wait: bool = True,
    timeout_s: int = 1800,
    auth_mode: str = "interactive",
    tenant_id: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> RefreshResult:
    """Trigger (and optionally wait for) a dataset refresh.

    Returns RefreshResult per spec §3 (status, duration, errors,
    rollback_performed).
    """
    config = AuthConfig.from_env_or_args(
        mode=auth_mode,
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
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
