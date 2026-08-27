"""Tests for orchestrator.server - FastMCP entrypoint with stub tools."""

from __future__ import annotations

import pytest

from powerbi_orchestrator_mcp.orchestrator.server import (
    ApplyResult,
    ConnectResult,
    EngineStatus,
    EstimatedChanges,
    PlanResult,
    apply_plan,
    connect_target,
    mcp,
    plan_change,
)


class TestModels:
    """Tests for Pydantic output schemas."""

    def test_engine_status_defaults(self) -> None:
        status = EngineStatus(available=True)
        assert status.available is True
        assert status.version is None
        assert status.reason_unavailable is None

    def test_engine_status_unavailable(self) -> None:
        status = EngineStatus(
            available=False, version=None, reason_unavailable="not installed"
        )
        assert status.available is False
        assert status.reason_unavailable == "not installed"

    def test_connect_result_defaults(self) -> None:
        result = ConnectResult(session_id="s1", engines_available={})
        assert result.session_id == "s1"
        assert result.engines_available == {}
        assert result.warnings == []

    def test_estimated_changes_defaults(self) -> None:
        ec = EstimatedChanges()
        assert ec.files_affected == 0
        assert ec.rollback_complexity == "trivial"

    def test_plan_result_risk_score_bounds(self) -> None:
        with pytest.raises(Exception):
            PlanResult(
                plan_id="p1",
                plan_yaml="y",
                risk_score=1.5,
            )
        with pytest.raises(Exception):
            PlanResult(
                plan_id="p1",
                plan_yaml="y",
                risk_score=-0.1,
            )

    def test_apply_result_defaults(self) -> None:
        result = ApplyResult()
        assert result.result == "success"
        assert result.executed_steps == []
        assert result.failed_step is None


class TestStubTools:
    """Tests for stub tool functions."""

    @pytest.mark.asyncio
    async def test_connect_target_returns_stub(self) -> None:
        result = await connect_target(
            target_type="pbip_folder",
            target_ref="./test.pbip",
        )
        assert isinstance(result, ConnectResult)
        assert result.session_id == "stub-session-001"
        assert "stub implementation" in result.warnings[0]

    @pytest.mark.asyncio
    async def test_plan_change_returns_stub(self) -> None:
        result = await plan_change(intent="safe_rename")
        assert isinstance(result, PlanResult)
        assert result.plan_id == "stub-plan-001"
        assert result.risk_score == 0.0

    @pytest.mark.asyncio
    async def test_apply_plan_returns_stub(self) -> None:
        result = await apply_plan(plan_id="stub-plan-001")
        assert isinstance(result, ApplyResult)
        assert result.result == "success"


class TestFastMCP:
    """Tests for FastMCP instance."""

    def test_mcp_instance_exists(self) -> None:
        assert mcp is not None
        assert mcp.name == "powerbi-orchestrator-mcp"
