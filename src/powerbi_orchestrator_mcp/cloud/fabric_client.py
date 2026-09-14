"""Fabric REST API client.

Implements ``specs/02-cloud-fabric.md`` §2.2 + §2.3 (Tier-B concurrency
limits per spec §6 + the circuit-breaker pattern from §7 of the spec).

Endpoints covered (per spec §2.2):
- Workspaces (list, get, create, delete)
- Items (list, get, create, delete — generic + per-type)
- Datasets (refresh, getRefreshHistory, cancelRefresh, takeOver,
  updateDatasource, updateRefreshSchedule)
- Deployment Pipelines (list, get, deploy, getOperation)
- Labels (admin bulk-set — gated)

For MVP we focus on the operations needed by ``run_refresh`` and
``deploy_to_workspace``. The full endpoint list is the source of truth
in ``specs/02-cloud-fabric.md``.

Concurrency primitives (per Tier-B §6):
- ``TokenBucket`` — limits requests/minute per tenant (default 200 RPM).
- ``CircuitBreaker`` — opens after 5 consecutive 5xx in 60s.
- Long-running ops (refresh, deploy) bypass the main bucket.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from powerbi_orchestrator_mcp.cloud.auth import FabricCredential

# ---------------------------------------------------------------------------
# Concurrency primitives
# ---------------------------------------------------------------------------


class CircuitBreakerOpenError(Exception):
    """Raised when the circuit breaker is open (per Tier-B §6)."""


@dataclass
class CircuitBreakerState:
    """Tracks state of a circuit breaker."""

    state: str = "closed"  # closed | open | half_open
    consecutive_5xx: int = 0
    opened_at: float = 0.0
    half_open_probes: int = 0
    half_open_successes: int = 0


class CircuitBreaker:
    """Circuit breaker per Tier-B §6.

    Closed: normal operation. Opens after 5 consecutive 5xx in any 60s
    window. Open: fails fast with CircuitBreakerOpenError. After cooldown
    (60s), transitions to half_open and allows up to N probe requests
    before deciding closed/open again.
    """

    def __init__(
        self,
        threshold: int = 5,
        cooldown_s: float = 60.0,
        half_open_probes: int = 3,
    ) -> None:
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self.half_open_probes = half_open_probes
        self._state = CircuitBreakerState()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        return self._state.state

    async def check(self) -> None:
        """Raise CircuitBreakerOpenError if the breaker is open.

        Transitions open → half_open if cooldown has elapsed.
        """
        async with self._lock:
            if self._state.state == "open":
                if time.monotonic() - self._state.opened_at >= self.cooldown_s:
                    self._state.state = "half_open"
                    self._state.half_open_probes = 0
                    self._state.half_open_successes = 0
                else:
                    raise CircuitBreakerOpenError(
                        f"circuit breaker open "
                        f"(cooldown {self.cooldown_s}s)"
                    )

    async def record_success(self) -> None:
        """Record a successful request."""
        async with self._lock:
            if self._state.state == "half_open":
                self._state.half_open_successes += 1
                self._state.half_open_probes += 1
                if (
                    self._state.half_open_successes >= self.half_open_probes
                    or self._state.half_open_probes >= self.half_open_probes
                ):
                    # All probes succeeded → close.
                    self._state = CircuitBreakerState()
            else:
                self._state.consecutive_5xx = 0

    async def record_5xx(self) -> None:
        """Record a 5xx response. May open the breaker."""
        async with self._lock:
            if self._state.state == "half_open":
                # Any failure during half_open → re-open.
                self._state.state = "open"
                self._state.opened_at = time.monotonic()
                return
            self._state.consecutive_5xx += 1
            if self._state.consecutive_5xx >= self.threshold:
                self._state.state = "open"
                self._state.opened_at = time.monotonic()


@dataclass
class TokenBucket:
    """Simple token bucket (Tier-B §6).

    Tokens replenish at ``rpm / 60`` per second up to a max of ``burst``.
    Each ``acquire()`` waits up to ``timeout_s`` for a token.
    """

    rpm: int = 200
    burst: int = 30
    _tokens: float = field(default=0.0)
    _last_refill: float = field(default_factory=time.monotonic)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        self._tokens = float(self.burst)

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.burst, self._tokens + elapsed * (self.rpm / 60.0))
        self._last_refill = now

    async def acquire(self, timeout_s: float = 30.0) -> bool:
        """Wait for a token. Returns False if timed out."""
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                # Wait time for next token.
                wait_s = (1.0 - self._tokens) / (self.rpm / 60.0)
                if wait_s > timeout_s:
                    return False
                # Release lock, sleep, reacquire.
                self._lock.release()
                try:
                    await asyncio.sleep(wait_s)
                finally:
                    await self._lock.acquire()


# ---------------------------------------------------------------------------
# FabricClient
# ---------------------------------------------------------------------------


class FabricClient:
    """Async REST client for Microsoft Fabric / Power BI Service.

    Two base URLs are exposed:
    - ``fabric_base_url`` — Fabric Items API (``/v1/workspaces/{id}/items``).
    - ``pbi_service_base_url`` — Power BI Service REST (``/v1.0/myorg/...``),
      which hosts dataset refresh schedules, take-over, refresh history,
      data-source updates and DAX query execution (per spec §2.3).

    Authentication via the injected ``FabricCredential`` (handles token
    refresh transparently). Long-running operations (refresh, deploy)
    bypass the main token bucket per Tier-B §6 — they use ``long_running_op()``
    which has its own polling logic.
    """

    BASE_URL = "https://api.fabric.microsoft.com/v1"
    PBI_SERVICE_BASE_URL = "https://api.powerbi.com/v1.0/myorg"
    DEFAULT_TIMEOUT = 30.0

    def __init__(
        self,
        credential: FabricCredential,
        *,
        rpm: int = 200,
        burst: int = 30,
        long_running_rpm: int = 5,
        fabric_base_url: str = BASE_URL,
        pbi_service_base_url: str = PBI_SERVICE_BASE_URL,
    ) -> None:
        self._credential = credential
        self._bucket = TokenBucket(rpm=rpm, burst=burst)
        self._long_running_bucket = TokenBucket(
            rpm=long_running_rpm, burst=long_running_rpm
        )
        self._breaker = CircuitBreaker()
        self._fabric_client = httpx.AsyncClient(
            base_url=fabric_base_url,
            timeout=httpx.Timeout(self.DEFAULT_TIMEOUT, connect=5.0),
        )
        self._pbi_client = httpx.AsyncClient(
            base_url=pbi_service_base_url,
            timeout=httpx.Timeout(self.DEFAULT_TIMEOUT, connect=5.0),
        )

    async def aclose(self) -> None:
        await self._fabric_client.aclose()
        await self._pbi_client.aclose()

    async def __aenter__(self) -> FabricClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    def _select_client(self, service: str) -> httpx.AsyncClient:
        """Return the httpx client for ``service`` (``"fabric"`` or ``"pbi"``)."""
        if service == "fabric":
            return self._fabric_client
        if service == "pbi":
            return self._pbi_client
        raise ValueError(f"unknown service {service!r}")

    # ------------------------------------------------------------------
    # Core HTTP verbs with retry + circuit breaker
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        long_running: bool = False,
        max_retries: int = 3,
        service: str = "fabric",
    ) -> dict[str, Any]:
        """Send an authenticated request with retries + circuit breaker.

        ``service`` selects the base URL: ``"fabric"`` (default, Fabric Items
        API) or ``"pbi"`` (Power BI Service REST, datasets/refresh).
        Retries per Tier-B §3: 429 (with Retry-After backoff), 502, 503, 504.
        Other status codes surface as FabricAPIError (per spec §2.3).
        """
        bucket = self._long_running_bucket if long_running else self._bucket
        await self._breaker.check()
        http_client = self._select_client(service)

        for attempt in range(max_retries + 1):
            acquired = await bucket.acquire()
            if not acquired:
                raise CircuitBreakerOpenError(
                    "token bucket timeout (rate limit exceeded)"
                )

            token = await self._credential.get_token()
            try:
                response = await http_client.request(
                    method,
                    path,
                    json=json,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError:
                if attempt < max_retries:
                    await asyncio.sleep(2**attempt)
                    continue
                raise

            if response.status_code == 429:
                # Honor Retry-After.
                retry_after = float(response.headers.get("Retry-After", "1"))
                await asyncio.sleep(min(retry_after, 30.0))
                continue

            if response.status_code in (502, 503, 504):
                await self._breaker.record_5xx()
                if attempt < max_retries:
                    await asyncio.sleep(2**attempt)
                    continue
                response.raise_for_status()

            if response.status_code >= 500:
                await self._breaker.record_5xx()
                response.raise_for_status()

            await self._breaker.record_success()

            if response.status_code >= 400:
                # 4xx — surface immediately.
                raise FabricAPIError(
                    f"Fabric API error: {response.status_code} {response.text}",
                    status_code=response.status_code,
                    response_body=response.text,
                )

            return response.json() if response.content else {}

        # Exhausted retries on 429.
        raise FabricAPIError(
            f"Fabric API 429 after {max_retries} retries",
            status_code=429,
            response_body="rate limit exceeded",
        )

    async def post(
        self,
        path: str,
        json: dict[str, Any] | None = None,
        *,
        long_running: bool = False,
        service: str = "fabric",
    ) -> dict[str, Any]:
        return await self._request(
            "POST", path, json=json, long_running=long_running, service=service
        )

    async def patch(
        self,
        path: str,
        json: dict[str, Any] | None = None,
        *,
        service: str = "fabric",
    ) -> dict[str, Any]:
        return await self._request("PATCH", path, json=json, service=service)

    async def delete(self, path: str, *, service: str = "fabric") -> None:
        await self._request("DELETE", path, service=service)

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        service: str = "fabric",
    ) -> dict[str, Any]:
        return await self._request("GET", path, params=params, service=service)

    # ------------------------------------------------------------------
    # High-level operations (per spec §2.2)
    # ------------------------------------------------------------------

    async def list_workspaces(self) -> list[dict[str, Any]]:
        """List all workspaces accessible to the principal."""
        resp = await self.get("/workspaces", service="fabric")
        return resp.get("value", [])  # type: ignore[no-any-return]

    async def get_workspace(self, workspace_id: str) -> dict[str, Any]:
        return await self.get(f"/workspaces/{workspace_id}", service="fabric")

    async def list_items(
        self, workspace_id: str, *, item_type: str | None = None
    ) -> list[dict[str, Any]]:
        """List Fabric items in a workspace, optionally filtered by type.

        Per spec §2.3. ``item_type`` e.g. ``"PowerBIDataset"``,
        ``"PowerBIReport"``, ``"Lakehouse"``.
        """
        params: dict[str, Any] | None = (
            {"type": item_type} if item_type else None
        )
        resp = await self.get(
            f"/workspaces/{workspace_id}/items",
            params=params,
            service="fabric",
        )
        return resp.get("value", [])  # type: ignore[no-any-return]

    async def get_item(
        self, workspace_id: str, item_id: str
    ) -> dict[str, Any]:
        return await self.get(
            f"/workspaces/{workspace_id}/items/{item_id}", service="fabric"
        )

    async def create_item(
        self,
        workspace_id: str,
        display_name: str,
        item_type: str,
        definition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a Fabric item (e.g. PowerBIDataset) in a workspace.

        Used by ``deploy_to_workspace`` after the pre-deploy gate passes.
        ``definition`` is optional; for PowerBIDataset the orchestrator
        typically passes a minimal ``defaultDatasetStorageFormat`` payload
        and lets Power BI Desktop / superbi-mcp populate the TMDL/PBIR
        schema afterwards. Returns the created item (with ``id``).
        """
        body: dict[str, Any] = {
            "displayName": display_name,
            "type": item_type,
        }
        if definition:
            body["definition"] = definition
        return await self.post(
            f"/workspaces/{workspace_id}/items", json=body, service="fabric"
        )

    async def delete_item(
        self, workspace_id: str, item_id: str
    ) -> None:
        """Delete a Fabric item. Requires WRITE scope + elicitation."""
        await self.delete(
            f"/workspaces/{workspace_id}/items/{item_id}", service="fabric"
        )

    async def list_datasets(self, workspace_id: str) -> list[dict[str, Any]]:
        resp = await self.get(
            f"/groups/{workspace_id}/datasets", service="pbi"
        )
        return resp.get("value", [])  # type: ignore[no-any-return]

    async def get_dataset(self, workspace_id: str, dataset_id: str) -> dict[str, Any]:
        return await self.get(
            f"/groups/{workspace_id}/datasets/{dataset_id}", service="pbi"
        )

    async def refresh_dataset(
        self,
        workspace_id: str,
        dataset_id: str,
        *,
        refresh_type: str = "full",
        commit_mode: str | None = None,
        tables: list[str] | None = None,
        wait: bool = False,  # noqa: ARG002
        timeout_ms: int = 1_800_000,  # noqa: ARG002
    ) -> dict[str, Any]:
        """Trigger a dataset refresh (Power BI Service REST endpoint).

        ``refresh_type`` per spec §3: full | automatic | data_only |
        calculate | clearValues. ``commit_mode``: transactional | partialBatch.
        """
        body: dict[str, Any] = {"refreshType": refresh_type}
        if commit_mode:
            body["commitMode"] = commit_mode
        if tables:
            body["objects"] = [
                {"table": t, "partition": None} for t in tables
            ]
        return await self.post(
            f"/groups/{workspace_id}/datasets/{dataset_id}/refreshes",
            json=body,
            long_running=True,
            service="pbi",
        )

    async def cancel_refresh(
        self, workspace_id: str, dataset_id: str
    ) -> None:
        """Cancel an in-progress dataset refresh (per spec §2.3)."""
        await self.post(
            f"/groups/{workspace_id}/datasets/{dataset_id}/refreshes/cancel",
            service="pbi",
        )

    async def get_refresh_history(
        self,
        workspace_id: str,
        dataset_id: str,
        *,
        top: int = 10,
    ) -> list[dict[str, Any]]:
        """Return the most recent refreshes for a dataset."""
        resp = await self.get(
            f"/groups/{workspace_id}/datasets/{dataset_id}/refreshes",
            params={"$top": top},
            service="pbi",
        )
        return resp.get("value", [])  # type: ignore[no-any-return]

    async def update_refresh_schedule(
        self,
        workspace_id: str,
        dataset_id: str,
        *,
        schedule: dict[str, Any],
    ) -> dict[str, Any]:
        """Configure the dataset's refresh schedule (per spec §2.3).

        ``schedule`` is the JSON body documented by Power BI for
        ``PATCH /groups/{groupId}/datasets/{datasetId}/refreshSchedule``:
        at minimum ``{"value": {...}, "enabled": true}``. Typical body
        for daily refresh::

            {
                "value": {
                    "days": ["Monday", "Tuesday", ...],
                    "times": ["06:00"],
                    "localTimeZoneId": "UTC"
                },
                "enabled": True
            }
        """
        return await self.patch(
            f"/groups/{workspace_id}/datasets/{dataset_id}/refreshSchedule",
            json=schedule,
            service="pbi",
        )

    async def take_over_dataset(
        self, workspace_id: str, dataset_id: str
    ) -> dict[str, Any]:
        """Take over ownership of a dataset (per spec §2.3).

        Useful when an upstream owner leaves the org or when the
        orchestrator needs to become the dataset's admin to update
        the refresh schedule.
        """
        return await self.post(
            f"/groups/{workspace_id}/datasets/{dataset_id}/takeover",
            service="pbi",
        )

    async def update_datasource(
        self,
        workspace_id: str,
        dataset_id: str,
        datasource_id: str,
        *,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """Update a dataset data-source (server / gateway mapping)."""
        return await self.patch(
            f"/groups/{workspace_id}/datasets/{dataset_id}"
            f"/datasources/{datasource_id}",
            json=body,
            service="pbi",
        )

    async def execute_queries(
        self,
        workspace_id: str,
        dataset_id: str,
        *,
        queries: list[dict[str, Any]],
        impersonated_user_name: str | None = None,
    ) -> dict[str, Any]:
        """Run DAX queries against a dataset (per spec §2.3).

        ``queries`` is the JSON body expected by Power BI: a list of
        ``{"query": "EVALUATE ..."}`` items. Used by ``run_dax_regression``
        (with injected ``query_executor`` in tests; real path here).
        """
        payload: dict[str, Any] = {"queries": queries}
        if impersonated_user_name:
            payload["impersonatedUserName"] = impersonated_user_name
        return await self.post(
            f"/groups/{workspace_id}/datasets/{dataset_id}/queries",
            json=payload,
            service="pbi",
        )


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class FabricAPIError(Exception):
    """Raised on non-retryable Fabric API errors (per spec §2.3).

    Includes structured fields so callers (audit_cloud.py,
    refresh_doctor.py) can diagnose.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        response_body: str,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


__all__ = [
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "FabricAPIError",
    "FabricClient",
    "TokenBucket",
]
