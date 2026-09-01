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

    async def test_list_datasets_uses_correct_path(self) -> None:
        cfg = AuthConfig(mode="interactive")
        cred = FabricCredential(cfg)
        captured: dict = {}

        async def fake_request(method, path, **kwargs):
            captured["method"] = method
            captured["path"] = path
            return {"value": []}

        client = FabricClient(cred, rpm=10_000, burst=10_000)
        client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
        try:
            await client.list_datasets("ws-abc")
            assert captured["method"] == "GET"
            assert captured["path"] == "/workspaces/ws-abc/datasets"
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
