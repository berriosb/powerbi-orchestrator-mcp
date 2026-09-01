"""pre_deploy_check tool — wraps PreDeployGate (SPEC §6.1 tool #10)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.validation.pre_deploy_gate import (
    GateResult,
    PreDeployGate,
)


class PreDeployCheck(BaseModel):
    """Input schema for ``pre_deploy_check`` tool (SPEC §6.1 #10)."""

    findings: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Findings to evaluate (each must have a 'severity' key).",
    )
    profile: str = "standard"  # strict | standard | relaxed
    blocking_severities: list[str] | None = None  # override defaults


def pre_deploy_check(
    findings: list[dict[str, Any]],
    profile: str = "standard",
    *,
    blocking_severities: list[str] | None = None,  # noqa: ARG001
) -> GateResult:
    """Evaluate findings against the pre-deploy gate profile.

    Returns pass/fail + per-severity counts + failed_checks.
    """
    gate = PreDeployGate(profile=profile)
    return gate.evaluate(findings)
