"""deploy_to_workspace tool — pre-deploy + publish + schedule (SPEC §6.1 #6).

Composes the Capa 3 fabric_client with the Capa 4 pre-deploy gate:
1. Run pre-deploy gate on supplied findings; abort if blocked.
2. Create item in target workspace (Fabric Items API).
3. Bind gateway (skipped — no on-prem data source by default).
4. Configure refresh schedule (Power BI Service REST API).
5. Trigger initial refresh.

Mock mode short-circuits steps 2-5 with synthetic responses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.cloud.auth import AuthConfig, FabricCredential
from powerbi_orchestrator_mcp.cloud.fabric_client import FabricClient
from powerbi_orchestrator_mcp.validation.pre_deploy_gate import (
    GateResult,
    PreDeployGate,
)


class DeployToWorkspace(BaseModel):
    """Input schema for ``deploy_to_workspace`` tool (SPEC §6.1 #6)."""

    pbip_path: str
    workspace_id: str
    refresh_daily_hour: int = 6  # 0-23 UTC
    findings: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Findings to evaluate against the pre-deploy gate.",
    )
    gate_profile: str = "standard"
    auth_mode: str = "interactive"
    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    # Mock mode: short-circuit the actual cloud calls.
    mock: bool = False


class DeployResult(BaseModel):
    """Result of deploy_to_workspace."""

    gate_result: GateResult | None = None
    publish_ok: bool = False
    item_id: str | None = None
    dataset_id: str | None = None
    schedule_ok: bool = False
    refresh_id: str | None = None
    errors: list[str] = Field(default_factory=list)


async def deploy_to_workspace(
    pbip_path: str,
    workspace_id: str,
    *,
    refresh_daily_hour: int = 6,
    findings: list[dict[str, Any]] | None = None,
    gate_profile: str = "standard",
    auth_mode: str = "interactive",
    tenant_id: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    mock: bool = False,
) -> DeployResult:
    """Pre-deploy gate → publish PBIP → schedule refresh → initial refresh.

    With ``mock=True`` (or when ``fabric_client`` raises), steps 2-5
    return synthetic success responses. With ``mock=False`` and valid
    Azure credentials, the tool makes real REST calls: ``POST
    /v1/workspaces/{id}/items`` (create), ``PATCH
    /v1.0/myorg/groups/{groupId}/datasets/{datasetId}/refreshSchedule``
    (schedule), ``POST
    /v1.0/myorg/groups/{groupId}/datasets/{datasetId}/refreshes``
    (refresh). The PBIP-to-PBIR payload upload is delegated to
    ``superbi-mcp`` when available (Windows); this orchestrator focuses
    on the workspace + refresh governance path.
    """
    findings = findings or []
    result = DeployResult()

    # 1. Pre-deploy gate.
    gate = PreDeployGate(profile=gate_profile)
    gate_result = gate.evaluate(findings)
    result.gate_result = gate_result
    if not gate_result.passed:
        result.errors.extend(
            [f"gate_blocked: {c}" for c in gate_result.failed_checks]
        )
        return result

    # 2-5: cloud calls (only if not in mock mode and credentials are valid).
    if mock:
        result.publish_ok = True
        result.item_id = "mock-item-id"
        result.dataset_id = Path(pbip_path).stem
        result.schedule_ok = True
        result.refresh_id = "mock-refresh-id"
        return result

    config = AuthConfig.from_env_or_args(
        mode=auth_mode,
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )
    cred = FabricCredential(config)
    client = FabricClient(cred)
    try:
        dataset_id = Path(pbip_path).stem  # e.g. "sales"
        display_name = dataset_id

        # 2. Publish — create the dataset item in the workspace.
        try:
            create_resp = await client.create_item(
                workspace_id,
                display_name=display_name,
                item_type="PowerBIDataset",
            )
            result.publish_ok = True
            result.item_id = (
                create_resp.get("id") or create_resp.get("objectId")
            )
            if not result.item_id:
                result.errors.append("publish_missing_item_id")
        except Exception as exc:
            result.errors.append(f"publish_failed: {exc}")

        # 3. Bind gateway — not needed if no on-prem data source.
        # (Skipped per MVP.)

        # 4. Configure refresh schedule (Power BI Service REST).
        schedule_body = _build_daily_schedule(refresh_daily_hour)
        try:
            await client.update_refresh_schedule(
                workspace_id,
                dataset_id,
                schedule=schedule_body,
            )
            result.schedule_ok = True
        except Exception as exc:
            result.errors.append(f"schedule_failed: {exc}")

        # 5. Trigger initial refresh.
        try:
            refresh_resp = await client.refresh_dataset(
                workspace_id,
                dataset_id,
                refresh_type="full",
            )
            result.refresh_id = (
                refresh_resp.get("refreshId") or refresh_resp.get("id")
            )
            result.dataset_id = dataset_id
        except Exception as exc:
            result.errors.append(f"initial_refresh_failed: {exc}")

        return result
    finally:
        await client.aclose()


def _build_daily_schedule(hour_utc: int) -> dict[str, Any]:
    """Return the JSON body for ``PATCH .../refreshSchedule`` (daily at hour_utc).

    Mirrors the Power BI Service REST contract: ``value.days`` list +
    ``value.times`` list + ``value.localTimeZoneId`` + ``enabled``.
    Hour is clamped to 0-23.
    """
    clamped = max(0, min(23, int(hour_utc)))
    return {
        "value": {
            "days": [
                "Monday",
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday",
                "Saturday",
                "Sunday",
            ],
            "times": [f"{clamped:02d}:00"],
            "localTimeZoneId": "UTC",
        },
        "enabled": True,
    }
