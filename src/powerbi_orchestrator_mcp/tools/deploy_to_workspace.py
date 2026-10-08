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

import base64
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


def _extract_tmdl_definition(pbip_path: str) -> dict[str, Any] | None:
    path = Path(pbip_path)
    if not path.exists():
        return None
    dataset_dirs: list[Path] = []
    if path.is_dir():
        dataset_dirs = [
            d
            for d in path.iterdir()
            if d.is_dir() and (d.name.endswith(".Dataset") or d.name.endswith(".SemanticModel"))
        ]
        if not dataset_dirs and (
            path.name.endswith(".Dataset") or path.name.endswith(".SemanticModel")
        ):
            dataset_dirs = [path]
    if not dataset_dirs:
        return None
    target_dir = dataset_dirs[0]
    parts: list[dict[str, str]] = []
    for file_path in target_dir.rglob("*"):
        if file_path.is_file():
            rel_path = file_path.relative_to(target_dir).as_posix()
            parts.append(
                {
                    "path": rel_path,
                    "payload": base64.b64encode(file_path.read_bytes()).decode("ascii"),
                    "payloadType": "InlineBase64",
                }
            )
    return {"parts": parts} if parts else None


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

    gate = PreDeployGate(profile=gate_profile)
    gate_result = gate.evaluate(findings)
    result.gate_result = gate_result
    if not gate_result.passed:
        result.errors.extend([f"gate_blocked: {c}" for c in gate_result.failed_checks])
        return result

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
        dataset_id = Path(pbip_path).stem
        display_name = dataset_id
        tmdl_definition = _extract_tmdl_definition(pbip_path)
        create_kwargs: dict[str, Any] = {
            "display_name": display_name,
            "item_type": "PowerBIDataset",
        }
        if tmdl_definition is not None:
            create_kwargs["definition"] = tmdl_definition

        try:
            create_resp = await client.create_item(
                workspace_id,
                **create_kwargs,
            )
            result.publish_ok = True
            result.item_id = create_resp.get("id") or create_resp.get("objectId")
            if not result.item_id:
                result.errors.append("publish_missing_item_id")
        except Exception as exc:
            result.errors.append(f"publish_failed: {exc}")

        target_dataset_id = result.item_id or dataset_id
        result.dataset_id = target_dataset_id

        schedule_body = _build_daily_schedule(refresh_daily_hour)
        try:
            await client.update_refresh_schedule(
                workspace_id,
                target_dataset_id,
                schedule=schedule_body,
            )
            result.schedule_ok = True
        except Exception as exc:
            result.errors.append(f"schedule_failed: {exc}")

        try:
            refresh_resp = await client.refresh_dataset(
                workspace_id,
                target_dataset_id,
                refresh_type="full",
            )
            result.refresh_id = refresh_resp.get("refreshId") or refresh_resp.get("id")
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
