"""Tests for cloud.fabric_client — REST + retry + circuit breaker."""

from __future__ import annotations

import asyncio

import pytest

from powerbi_orchestrator_mcp.cloud.auth import AuthConfig, FabricCredential
from powerbi_orchestrator_mcp.cloud.fabric_client import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    FabricAPIError,
    FabricClient,
    TokenBucket,
)


class TestCircuitBreaker:
    async def test_starts_closed(self) -> None:
        cb = CircuitBreaker()
        await cb.check()  # does not raise

    async def test_opens_after_threshold(self) -> None:
        cb = CircuitBreaker(threshold=3, cooldown_s=60.0)
        for _ in range(3):
            await cb.record_5xx()
        with pytest.raises(CircuitBreakerOpenError):
            await cb.check()

    async def test_success_resets_consecutive_count(self) -> None:
        cb = CircuitBreaker(threshold=3)
        await cb.record_5xx()
        await cb.record_5xx()
        await cb.record_success()
        # After a success, count should reset.
        # We can take 2 more 5xx without opening.
        await cb.record_5xx()
        await cb.record_5xx()
        await cb.check()  # still closed (only 2 consecutive)

    async def test_half_open_transitions_after_cooldown(self) -> None:
        cb = CircuitBreaker(threshold=2, cooldown_s=0.05)
        await cb.record_5xx()
        await cb.record_5xx()
        with pytest.raises(CircuitBreakerOpenError):
            await cb.check()
        await asyncio.sleep(0.1)  # wait past cooldown
        # Next check should transition to half_open (no raise).
        await cb.check()
        assert cb.state == "half_open"

    async def test_half_open_failure_reopens(self) -> None:
        cb = CircuitBreaker(threshold=2, cooldown_s=0.05)
        await cb.record_5xx()
        await cb.record_5xx()
        await asyncio.sleep(0.1)
        await cb.check()  # transitions to half_open
        await cb.record_5xx()  # any failure during half_open → re-open
        assert cb.state == "open"


class TestTokenBucket:
    async def test_initial_tokens_equals_burst(self) -> None:
        bucket = TokenBucket(rpm=60, burst=10)
        # Can consume up to burst immediately.
        for _ in range(10):
            assert await bucket.acquire(timeout_s=0.1) is True
        # 11th consumes a future token (refill rate ~1/s).
        # With rpm=60 → 1 token/sec, the 11th call should wait or timeout.
        # We don't assert behavior here, just that consume works.

    async def test_acquire_returns_false_on_timeout(self) -> None:
        bucket = TokenBucket(rpm=1, burst=1)
        await bucket.acquire(timeout_s=0.1)  # consume the 1 token
        # Next acquire should time out quickly.
        result = await bucket.acquire(timeout_s=0.05)
        assert result is False


class TestFabricClient:
    async def test_list_workspaces_returns_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Stub the credential so we don't make real Azure calls.
        cfg = AuthConfig(mode="interactive")
        cred = FabricCredential(cfg)

        # Monkey-patch _request to return a canned response.
        async def fake_request(method, path, **kwargs):
            return {"value": [{"id": "ws-1", "name": "Test"}]}

        client = FabricClient(cred, rpm=10_000, burst=10_000)
        client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
        try:
            result = await client.list_workspaces()
            assert result == [{"id": "ws-1", "name": "Test"}]
        finally:
            await client.aclose()

    async def test_list_datasets_uses_pbi_service_path(self) -> None:
        cfg = AuthConfig(mode="interactive")
        cred = FabricCredential(cfg)
        captured: dict = {}

        async def fake_request(method, path, *, service="fabric", **kwargs):
            captured["method"] = method
            captured["path"] = path
            captured["service"] = service
            return {"value": []}

        client = FabricClient(cred, rpm=10_000, burst=10_000)
        client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
        try:
            await client.list_datasets("ws-abc")
            assert captured["method"] == "GET"
            # Datasets live on the Power BI Service REST API
            # (`/groups/{groupId}/datasets`), not the Fabric Items API
            # (`/v1/workspaces/{id}/datasets`).
            assert captured["path"] == "/groups/ws-abc/datasets"
            assert captured["service"] == "pbi"
        finally:
            await client.aclose()

    async def test_refresh_dataset_uses_long_running_bucket(self) -> None:
        cfg = AuthConfig(mode="interactive")
        cred = FabricCredential(cfg)
        captured: dict = {}

        async def fake_request(method, path, *, long_running=False, **kwargs):
            captured["long_running"] = long_running
            captured["body"] = kwargs.get("json", {})
            return {"refreshId": "abc"}

        client = FabricClient(cred, rpm=10_000, burst=10_000)
        client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
        try:
            result = await client.refresh_dataset(
                "ws-abc", "ds-xyz", refresh_type="full"
            )
            assert captured["long_running"] is True
            assert captured["body"]["refreshType"] == "full"
            assert result["refreshId"] == "abc"
        finally:
            await client.aclose()


class TestFabricAPIError:
    def test_carries_status_code_and_body(self) -> None:
        exc = FabricAPIError(
            "boom",
            status_code=403,
            response_body="forbidden",
        )
        assert exc.status_code == 403
        assert exc.response_body == "forbidden"
        assert "boom" in str(exc)


# ---------------------------------------------------------------------------
# Sprint 14B — new endpoints (per spec §2.3)
# ---------------------------------------------------------------------------


class TestFabricClientSprint14B:
    """Cover the endpoints added in Sprint 14B:
    create_item, update_refresh_schedule, take_over_dataset,
    cancel_refresh, get_refresh_history, execute_queries,
    update_datasource, delete_item, list_items, get_item."""

    @staticmethod
    async def _make_client() -> tuple[FabricClient, dict[str, Any]]:
        cfg = AuthConfig(mode="interactive")
        cred = FabricCredential(cfg)
        captured: dict[str, Any] = {}

        async def fake_request(method, path, *, service="fabric", **kwargs):
            captured["method"] = method
            captured["path"] = path
            captured["service"] = service
            captured["json"] = kwargs.get("json")
            captured["params"] = kwargs.get("params")
            return {"value": [{"id": "v-1"}], "id": "new-item-1"}

        client = FabricClient(cred, rpm=10_000, burst=10_000)
        client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
        return client, captured

    async def test_create_item_uses_fabric_service(self) -> None:
        client, captured = await self._make_client()
        try:
            result = await client.create_item(
                "ws-1", display_name="sales", item_type="PowerBIDataset"
            )
            assert captured["service"] == "fabric"
            assert captured["method"] == "POST"
            assert captured["path"] == "/workspaces/ws-1/items"
            assert captured["json"] == {
                "displayName": "sales",
                "type": "PowerBIDataset",
            }
            assert result["id"] == "new-item-1"
        finally:
            await client.aclose()

    async def test_update_refresh_schedule_uses_pbi_service_and_patch(
        self,
    ) -> None:
        client, captured = await self._make_client()
        try:
            await client.update_refresh_schedule(
                "ws-1",
                "ds-1",
                schedule={
                    "value": {
                        "days": ["Monday"],
                        "times": ["06:00"],
                        "localTimeZoneId": "UTC",
                    },
                    "enabled": True,
                },
            )
            assert captured["service"] == "pbi"
            assert captured["method"] == "PATCH"
            assert captured["path"] == (
                "/groups/ws-1/datasets/ds-1/refreshSchedule"
            )
            assert captured["json"]["enabled"] is True
            assert captured["json"]["value"]["localTimeZoneId"] == "UTC"
        finally:
            await client.aclose()

    async def test_take_over_dataset_uses_post(self) -> None:
        client, captured = await self._make_client()
        try:
            await client.take_over_dataset("ws-1", "ds-1")
            assert captured["service"] == "pbi"
            assert captured["method"] == "POST"
            assert captured["path"] == "/groups/ws-1/datasets/ds-1/takeover"
        finally:
            await client.aclose()

    async def test_cancel_refresh_uses_post(self) -> None:
        client, captured = await self._make_client()
        try:
            await client.cancel_refresh("ws-1", "ds-1")
            assert captured["service"] == "pbi"
            assert captured["method"] == "POST"
            assert captured["path"] == (
                "/groups/ws-1/datasets/ds-1/refreshes/cancel"
            )
        finally:
            await client.aclose()

    async def test_get_refresh_history_passes_query(self) -> None:
        client, captured = await self._make_client()
        try:
            history = await client.get_refresh_history(
                "ws-1", "ds-1", top=5
            )
            assert captured["service"] == "pbi"
            assert captured["method"] == "GET"
            assert captured["path"] == "/groups/ws-1/datasets/ds-1/refreshes"
            assert captured["params"] == {"$top": 5}
            assert history == [{"id": "v-1"}]
        finally:
            await client.aclose()

    async def test_execute_queries_includes_impersonation(self) -> None:
        client, captured = await self._make_client()
        try:
            await client.execute_queries(
                "ws-1",
                "ds-1",
                queries=[{"query": "EVALUATE ROW(\"x\", 1)"}],
                impersonated_user_name="alice@contoso.com",
            )
            assert captured["service"] == "pbi"
            assert captured["method"] == "POST"
            assert captured["path"] == "/groups/ws-1/datasets/ds-1/queries"
            assert captured["json"]["impersonatedUserName"] == "alice@contoso.com"
            assert captured["json"]["queries"] == [
                {"query": "EVALUATE ROW(\"x\", 1)"}
            ]
        finally:
            await client.aclose()

    async def test_list_items_filters_by_type(self) -> None:
        client, captured = await self._make_client()
        try:
            items = await client.list_items("ws-1", item_type="PowerBIReport")
            assert captured["service"] == "fabric"
            assert captured["path"] == "/workspaces/ws-1/items"
            assert captured["params"] == {"type": "PowerBIReport"}
            assert items == [{"id": "v-1"}]
        finally:
            await client.aclose()

    async def test_delete_item_uses_delete(self) -> None:
        client, captured = await self._make_client()
        try:
            await client.delete_item("ws-1", "item-1")
            assert captured["service"] == "fabric"
            assert captured["method"] == "DELETE"
            assert captured["path"] == "/workspaces/ws-1/items/item-1"
        finally:
            await client.aclose()

    async def test_update_datasource_patches_correct_path(self) -> None:
        client, captured = await self._make_client()
        try:
            await client.update_datasource(
                "ws-1",
                "ds-1",
                "dsrc-1",
                body={"credentialDetails": {"credentials": "{}"}},
            )
            assert captured["service"] == "pbi"
            assert captured["method"] == "PATCH"
            assert captured["path"] == (
                "/groups/ws-1/datasets/ds-1/datasources/dsrc-1"
            )
            assert captured["json"]["credentialDetails"]["credentials"] == "{}"
        finally:
            await client.aclose()

    async def test_two_httpx_clients_independent(self) -> None:
        """Fabric and PBI base URLs must not collide."""
        client, _ = await self._make_client()
        try:
            assert client._select_client("fabric") is not client._select_client("pbi")  # type: ignore[attr-defined]  # noqa: SLF001
        finally:
            await client.aclose()
