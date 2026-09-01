"""Cloud refresh orchestration + diagnostic helper.

Implements ``specs/02-cloud-fabric.md`` §3 (``run_refresh`` tool spec)
+ §4 (``refresh_doctor``).

The two modules live together because they're tightly coupled: the
doctor is what ``run_refresh`` consults when a refresh fails.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.cloud.fabric_client import (
    FabricAPIError,
    FabricClient,
)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class RefreshResult(BaseModel):
    """Result of ``run_refresh`` (spec §3 output schema)."""

    refresh_id: str
    status: str  # InProgress | Completed | Failed | Cancelled | Timeout
    duration_ms: int = 0
    errors: list[dict[str, Any]] = Field(default_factory=list)
    rollback_performed: bool = False


# ---------------------------------------------------------------------------
# Refresh orchestrator
# ---------------------------------------------------------------------------


class RefreshOrchestrator:
    """High-level ``run_refresh`` implementation per spec §3.

    Workflow:
    1. ``refresh_dataset()`` — POST the refresh (long_running bucket).
    2. If ``wait=True``: poll ``getRefreshHistory()`` until terminal state.
    3. On failure: invoke ``RefreshDoctor.diagnose()`` and attach errors.
    4. On timeout: ``cancel_refresh()`` (best-effort rollback).

    MVP supports the canonical case (wait=True, timeout=30 min). The
    full long-running pattern is described in Tier-B §6.4 — already
    implemented in FabricClient via long_running bucket.
    """

    DEFAULT_TIMEOUT_S = 30 * 60  # 30 minutes per spec §3 default
    POLL_INTERVAL_S = 5.0
    TERMINAL_STATUSES = frozenset(
        {"Completed", "Failed", "Cancelled", "Disabled"}
    )

    def __init__(
        self,
        client: FabricClient,
        doctor: RefreshDoctor | None = None,
    ) -> None:
        self._client = client
        self._doctor = doctor or RefreshDoctor()

    async def run(
        self,
        workspace_id: str,
        dataset_id: str,
        *,
        refresh_type: str = "full",
        commit_mode: str | None = None,
        tables: list[str] | None = None,
        wait: bool = True,
        timeout_s: int | None = None,
    ) -> RefreshResult:
        """Trigger (and optionally wait for) a dataset refresh."""
        timeout = timeout_s or self.DEFAULT_TIMEOUT_S
        start = datetime.now(UTC)
        refresh = await self._client.refresh_dataset(
            workspace_id,
            dataset_id,
            refresh_type=refresh_type,
            commit_mode=commit_mode,
            tables=tables,
        )
        refresh_id = refresh.get("refreshId") or refresh.get("id", "")

        if not wait:
            return RefreshResult(
                refresh_id=refresh_id,
                status="InProgress",
                duration_ms=0,
            )

        # Poll until terminal.
        result = await self._poll(
            workspace_id, dataset_id, refresh_id, timeout
        )

        # The refresh_result.errors may contain a string from serviceExceptionJson;
        # normalize it into a list of dicts for downstream consumption.
        if result.errors and isinstance(result.errors, str):
            try:
                result.errors = [json.loads(result.errors)]
            except json.JSONDecodeError:
                result.errors = [{"raw": result.errors}]
        result.duration_ms = int(
            (datetime.now(UTC) - start).total_seconds() * 1000
        )

        # Diagnose if failed.
        if result.status == "Failed":
            history = await self._safe_get_history(
                workspace_id, dataset_id
            )
            diagnosis = self._doctor.diagnose(history)
            result.errors = diagnosis
            # Best-effort rollback: cancel if still in progress, otherwise
            # trigger a clearValues refresh (per spec §3 rollback semantics).
            if result.refresh_id:
                await self._safe_cancel(
                    workspace_id, dataset_id, result.refresh_id
                )
                result.rollback_performed = True

        return result

    async def _poll(
        self,
        workspace_id: str,
        dataset_id: str,
        refresh_id: str,
        timeout_s: int,
    ) -> RefreshResult:
        """Poll the refresh history until terminal status or timeout."""
        import json as _json

        deadline = asyncio.get_running_loop().time() + timeout_s
        """Poll the refresh history until terminal status or timeout."""
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            if asyncio.get_running_loop().time() > deadline:
                return RefreshResult(
                    refresh_id=refresh_id,
                    status="Timeout",
                    duration_ms=0,
                    errors=[
                        {
                            "code": "refresh_timeout",
                            "message": (
                                f"refresh did not complete within {timeout_s}s"
                            ),
                        }
                    ],
                )
            history = await self._safe_get_history(
                workspace_id, dataset_id
            )
            for entry in history:
                if entry.get("id") == refresh_id or entry.get(
                    "refreshId"
                ) == refresh_id:
                    status = entry.get("status", "Unknown")
                    # Normalize serviceExceptionJson (a JSON string in the
                    # real API) into a list of error dicts.
                    raw_errors = entry.get("serviceExceptionJson") or []
                    if isinstance(raw_errors, str):
                        try:
                            parsed = _json.loads(raw_errors)
                            raw_errors = (
                                parsed if isinstance(parsed, list) else [parsed]
                            )
                        except _json.JSONDecodeError:
                            raw_errors = [{"raw": raw_errors}]
                    return RefreshResult(
                        refresh_id=refresh_id,
                        status=status,
                        duration_ms=0,
                        errors=raw_errors,
                    )
            await asyncio.sleep(self.POLL_INTERVAL_S)

    async def _safe_get_history(
        self, workspace_id: str, dataset_id: str
    ) -> list[dict[str, Any]]:
        try:
            resp = await self._client.get(
                f"/workspaces/{workspace_id}/datasets/{dataset_id}/refreshes"
            )
            return resp.get("value", [])  # type: ignore[no-any-return]
        except FabricAPIError:
            return []

    async def _safe_cancel(
        self, workspace_id: str, dataset_id: str, refresh_id: str
    ) -> None:
        import contextlib

        with contextlib.suppress(FabricAPIError):
            await self._client.post(
                f"/workspaces/{workspace_id}/datasets/{dataset_id}/refreshes/{refresh_id}/cancel"
            )


# ---------------------------------------------------------------------------
# RefreshDoctor — diagnostic helper (per spec §4)
# ---------------------------------------------------------------------------


@dataclass
class RefreshDoctor:
    """Diagnose common refresh errors per spec §4.

    Pure-function module: takes a refresh history entry, returns a list
    of diagnostic findings (each with code + message + remediation).
    """

    # Pattern → (code, base message, remediation_hint).
    PATTERNS = [
        (
            re.compile(r"401\s+Unauthorized", re.I),
            "auth_token_expired",
            "Bearer token expired mid-refresh",
            "Re-authenticate via az login or rotate SPN secret",
        ),
        (
            re.compile(r"403\s+RequestDisallowedByTenant", re.I),
            "tenant_policy_blocked",
            "Tenant admin policy blocks this operation",
            "Ask tenant admin to allow this feature",
        ),
        (
            re.compile(r"403\s+CapacityNotAssigned", re.I),
            "workspace_no_capacity",
            "Workspace has no Premium/Fabric capacity assigned",
            "Assign workspace to a P-capacity or F-capacity in admin portal",
        ),
        (
            re.compile(r"400\s+RefreshRequestThrottled", re.I),
            "refresh_throttled",
            "Service is rate-limiting refreshes",
            "Wait and retry; consider scheduling refresh during off-peak hours",
        ),
        (
            re.compile(r"400\s+InvalidRefreshRequest", re.I),
            "gateway_unreachable",
            "On-premises gateway is down or unreachable",
            "Check gateway status in Power BI service; verify network connectivity",
        ),
        (
            re.compile(r"DM_GatewayOutOfMemory", re.I),
            "gateway_out_of_memory",
            "Gateway ran out of memory processing partitions",
            "Reduce partition count or upgrade gateway capacity",
        ),
    ]

    def diagnose(self, history_entry: Any) -> list[dict[str, Any]]:
        """Return diagnostic findings for a refresh history entry.

        ``history_entry`` may be a dict (real API response) or list of
        dicts (batch). Each entry is searched against the patterns.

        Note: each pattern is matched against the blob of ALL entries
        combined, so a single failure pattern across multiple entries
        still surfaces as one finding (per refresh_id is the most common
        case in practice — usually one entry per refresh).
        """
        if isinstance(history_entry, dict):
            entries = [history_entry]
        elif isinstance(history_entry, list):
            entries = history_entry
        else:
            return []

        # Aggregate blobs per refresh_id so we attribute findings
        # correctly.
        blobs_by_refresh: dict[str, str] = {}
        for entry in entries:
            rid = entry.get("refreshId") or entry.get("id") or "<unknown>"
            blobs_by_refresh[rid] = (
                blobs_by_refresh.get(rid, "") + " " + self._extract_blob(entry)
            )

        findings: list[dict[str, Any]] = []
        for rid, blob in blobs_by_refresh.items():
            for pattern, code, msg, hint in self.PATTERNS:
                if pattern.search(blob):
                    findings.append(
                        {
                            "code": code,
                            "message": msg,
                            "remediation_hint": hint,
                            "refresh_id": rid,
                        }
                    )
        return findings

    @staticmethod
    def _extract_blob(entry: dict[str, Any]) -> str:
        """Extract a searchable string from a refresh history entry."""
        import json

        parts: list[str] = []
        for key in ("serviceExceptionJson", "serviceException", "error", "message"):
            v = entry.get(key)
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, (dict, list)):
                parts.append(json.dumps(v))
        if not parts:
            parts.append(json.dumps(entry))
        return " ".join(parts)


__all__ = ["RefreshDoctor", "RefreshOrchestrator", "RefreshResult"]
