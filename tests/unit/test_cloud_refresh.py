"""Tests for cloud.refresh + refresh_doctor."""

from __future__ import annotations

from typing import Any

from powerbi_orchestrator_mcp.cloud.refresh import (
    RefreshDoctor,
    RefreshOrchestrator,
    RefreshResult,
)


class FakeClient:
    """Stand-in FabricClient that returns scripted outcomes per method."""

    def __init__(self, scripts: dict[str, Any]) -> None:
        self._scripts = scripts
        self.calls: list[tuple[str, dict]] = []

    async def refresh_dataset(
        self, workspace_id, dataset_id, **kwargs
    ) -> dict[str, Any]:
        self.calls.append(("refresh_dataset", kwargs))
        return self._scripts.get("refresh_dataset", {"refreshId": "abc"})

    async def get(self, path: str, **kwargs) -> dict[str, Any]:
        self.calls.append(("get", {"path": path}))
        return self._scripts.get("get", {"value": []})

    async def post(self, path: str, json=None, *, long_running=False) -> dict[str, Any]:
        self.calls.append(("post", {"path": path, "long_running": long_running}))
        return self._scripts.get("post", {})


class TestRefreshDoctor:
    def test_diagnose_auth_token_expired(self) -> None:
        doctor = RefreshDoctor()
        entry = {
            "serviceExceptionJson": "{\"error\":{\"code\":\"401 Unauthorized\"}}",
        }
        findings = doctor.diagnose(entry)
        assert any(f["code"] == "auth_token_expired" for f in findings)

    def test_diagnose_workspace_no_capacity(self) -> None:
        doctor = RefreshDoctor()
        entry = {"error": "403 CapacityNotAssigned in workspace"}
        findings = doctor.diagnose(entry)
        assert any(f["code"] == "workspace_no_capacity" for f in findings)

    def test_diagnose_gateway_out_of_memory(self) -> None:
        doctor = RefreshDoctor()
        entry = {"serviceExceptionJson": '{"code":"DM_GatewayOutOfMemory"}'}
        findings = doctor.diagnose(entry)
        assert any(f["code"] == "gateway_out_of_memory" for f in findings)

    def test_diagnose_throttling(self) -> None:
        doctor = RefreshDoctor()
        entry = {"message": "400 RefreshRequestThrottled"}
        findings = doctor.diagnose(entry)
        assert any(f["code"] == "refresh_throttled" for f in findings)

    def test_diagnose_unknown_returns_empty(self) -> None:
        doctor = RefreshDoctor()
        entry = {"message": "weird unrelated error"}
        assert doctor.diagnose(entry) == []

    def test_diagnose_accepts_list(self) -> None:
        doctor = RefreshDoctor()
        entries = [
            {"message": "401 Unauthorized"},
            {"message": "403 CapacityNotAssigned"},
        ]
        findings = doctor.diagnose(entries)
        codes = {f["code"] for f in findings}
        assert "auth_token_expired" in codes
        assert "workspace_no_capacity" in codes


class TestRefreshOrchestrator:
    async def test_run_no_wait_returns_in_progress(self) -> None:
        client = FakeClient({})
        orch = RefreshOrchestrator(client)  # type: ignore[arg-type]
        result = await orch.run("ws", "ds", wait=False)
        assert isinstance(result, RefreshResult)
        assert result.status == "InProgress"

    async def test_run_wait_completes(self) -> None:
        # Script: refresh_dataset returns id, get returns the same id with
        # status=Completed → orchestrator returns success.
        client = FakeClient(
            {
                "refresh_dataset": {"refreshId": "r1"},
                "get": {
                    "value": [
                        {"id": "r1", "status": "Completed", "serviceExceptionJson": ""}
                    ]
                },
            }
        )
        orch = RefreshOrchestrator(client)  # type: ignore[arg-type]
        result = await orch.run("ws", "ds", wait=True, timeout_s=5)
        assert result.status == "Completed"
        assert result.refresh_id == "r1"

    async def test_run_failed_diagnoses_and_attempts_rollback(self) -> None:
        client = FakeClient(
            {
                "refresh_dataset": {"refreshId": "r1"},
                "get": {
                    "value": [
                        {
                            "id": "r1",
                            "status": "Failed",
                            "serviceExceptionJson": (
                                '{"code":"DM_GatewayOutOfMemory"}'
                            ),
                        }
                    ]
                },
                "post": {},
            }
        )
        orch = RefreshOrchestrator(client)  # type: ignore[arg-type]
        result = await orch.run("ws", "ds", wait=True, timeout_s=5)
        assert result.status == "Failed"
        assert any(
            f["code"] == "gateway_out_of_memory" for f in result.errors
        )
        assert result.rollback_performed is True
