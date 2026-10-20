"""Tests for the plan risk threshold and the templates behind it.

The deploy template publishes to a workspace, schedules a refresh and
triggers one, so it is refused by default: its risk score is above the
default impact threshold. The refusal has to say how to proceed, and the
template has to accept the arguments it is actually given.
"""

from __future__ import annotations

import pytest


class TestPlanThreshold:
    @pytest.mark.asyncio
    async def test_deploy_plan_says_how_to_raise_the_threshold(self) -> None:
        """The deploy plan is refused by default; the error must be actionable."""
        from powerbi_orchestrator_mcp.orchestrator.plan_models import PlanValidationError
        from powerbi_orchestrator_mcp.orchestrator.server import plan_change

        with pytest.raises(PlanValidationError) as exc_info:
            await plan_change(
                intent="deploy",
                options={"pbip_path": "model.pbip", "workspace_id": "ws-1"},
            )

        message = str(exc_info.value)
        assert "max_impact_threshold" in message
        assert "70" in message

    @pytest.mark.asyncio
    async def test_deploy_plan_builds_when_the_threshold_is_raised(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.server import plan_change

        result = await plan_change(
            intent="deploy",
            options={
                "pbip_path": "model.pbip",
                "workspace_id": "ws-1",
                "max_impact_threshold": 80,
            },
        )

        assert result.plan_id
        assert result.risk_score > 0.5


class TestPromoteGateDetail:
    def test_pre_deploy_gate_detail_says_why_it_blocked(self) -> None:
        from powerbi_orchestrator_mcp.tools.promote_in_pipeline import (
            QualityGate,
            _run_pre_deploy_check,
        )

        gate = _run_pre_deploy_check(QualityGate(type="pre_deploy_check"))

        assert gate.passed is False
        assert "evaluated=0" in gate.detail
        assert "blocked=" in gate.detail
