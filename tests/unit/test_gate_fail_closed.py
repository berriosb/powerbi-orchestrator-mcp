"""Tests for the fail-closed behaviour of the pre-deploy quality gate.

The gate decides whether an artifact may be published. Two things must
never happen:

- A gate that evaluated nothing may not report "passed": that reads as
  "everything was checked and nothing was found".
- A severity the gate cannot interpret may not be dropped: "critical"
  is not a level the gate knows, but it is certainly not an "info".
"""

from __future__ import annotations

import pytest

from powerbi_orchestrator_mcp.tools.deploy_to_workspace import deploy_to_workspace
from powerbi_orchestrator_mcp.tools.pre_deploy_check import pre_deploy_check
from powerbi_orchestrator_mcp.validation.pre_deploy_gate import PreDeployGate


class TestGateFailsClosed:
    def test_no_findings_is_not_a_pass(self) -> None:
        gate = PreDeployGate(profile="standard")
        result = gate.evaluate([])

        assert result.passed is False
        assert result.evaluated == 0
        assert any("no findings were evaluated" in c for c in result.failed_checks)

    def test_an_unknown_severity_blocks(self) -> None:
        gate = PreDeployGate(profile="relaxed")
        result = gate.evaluate([{"severity": "critical", "message": "boom"}])

        assert result.passed is False
        assert result.unknown_severities == ["critical"]
        assert any("unknown severity" in c for c in result.failed_checks)

    def test_a_missing_severity_blocks(self) -> None:
        gate = PreDeployGate(profile="relaxed")
        result = gate.evaluate([{"message": "no severity here"}])

        assert result.passed is False
        assert result.unknown_severities == ["(missing severity)"]

    def test_severity_case_is_normalized(self) -> None:
        gate = PreDeployGate(profile="standard")
        result = gate.evaluate([{"severity": "Error", "message": "x"}])

        assert result.error_count == 1
        assert result.unknown_severities == []
        assert result.passed is False

    def test_severity_whitespace_is_normalized(self) -> None:
        gate = PreDeployGate(profile="standard")
        result = gate.evaluate([{"severity": "  WARNING  ", "message": "x"}])

        assert result.warning_count == 1
        assert result.unknown_severities == []

    def test_known_severities_still_behave(self) -> None:
        gate = PreDeployGate(profile="standard")
        result = gate.evaluate([{"severity": "warning", "message": "x"}])

        assert result.passed is True
        assert result.evaluated == 1


class TestPreDeployCheckTool:
    def test_empty_findings_do_not_pass(self) -> None:
        result = pre_deploy_check([], profile="standard")

        assert result.passed is False
        assert result.evaluated == 0


class TestDeployGate:
    @pytest.mark.asyncio
    async def test_deploy_without_findings_is_blocked(self) -> None:
        """A deploy must not clear its gate on zero evidence."""
        result = await deploy_to_workspace("/tmp/model.pbip", "ws-1", dry_run=True)

        assert result.gate_result is not None
        assert result.gate_result.passed is False
        assert result.gate_result.evaluated == 0
        assert result.publish_ok is False
        assert any("gate_blocked" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_blocked_deploy_still_reports_the_planned_actions(self) -> None:
        """A dry run that is blocked must still say what it would have done."""
        result = await deploy_to_workspace("/tmp/model.pbip", "ws-1", dry_run=True)

        assert result.planned_actions
        assert any("create PowerBIDataset" in a for a in result.planned_actions)

    @pytest.mark.asyncio
    async def test_deploy_with_clean_findings_clears_the_gate(self) -> None:
        result = await deploy_to_workspace(
            "/tmp/model.pbip",
            "ws-1",
            dry_run=True,
            findings=[{"severity": "info", "message": "all good"}],
        )

        assert result.gate_result is not None
        assert result.gate_result.passed is True
        assert result.dry_run is True
