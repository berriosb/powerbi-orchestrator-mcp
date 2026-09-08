"""promote_in_pipeline tool — v2 (SPEC §6.2; spec: specs/tools/promote-in-pipeline.md).

Promote items through a Fabric Deployment Pipeline (dev -> test -> prod),
running quality gates between stages. The MVP captures the orchestration
locally and delegates actual Fabric calls to an injected ``fabric_client``
(in production: an `httpx`-backed client wrapping the
Deployment Pipelines REST endpoints). When no client is supplied, the
tool runs in **dry-run / shadow** mode: it executes the local gate
checks and produces a promotion plan + report without touching Fabric.

Quality gates implemented locally:

- ``pre_deploy_check`` runs ``pre_deploy_check(findings, profile=...)``
  and aborts if ``passed`` is False.
- ``audit_model_and_report`` runs the composite audit and aborts if the
  score is below the configured threshold (default 80).
- ``run_dax_regression`` runs the regression runner and aborts if the
  per-measure drift exceeds the configured tolerance (default 0.1%).

Quality gates are pluggable via ``custom_gates`` (list of callables).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


_VALID_STAGES: set[str] = {"dev", "test", "prod"}


class QualityGate(BaseModel):
    """One gate to run before promotion."""

    type: str  # pre_deploy_check | audit_model_and_report | run_dax_regression | custom
    profile: str | None = None
    threshold: float | None = None  # used by audit / regression gates
    blocking: bool = True

    @field_validator("type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        allowed = {"pre_deploy_check", "audit_model_and_report", "run_dax_regression", "custom"}
        if v not in allowed:
            raise ValueError(
                f"invalid gate type {v!r}; expected one of {sorted(allowed)}"
            )
        return v


class PromoteInPipeline(BaseModel):
    """Input schema."""

    pipeline_id: str
    source_stage: str
    target_stage: str
    items: list[str] | None = None  # default: all items in source stage
    quality_gates: list[QualityGate] = Field(default_factory=list)
    notify_on_failure: bool = True
    dry_run: bool = False
    fabric_client: Any = None  # injected for real deployments
    custom_gates: list[Any] | None = None  # list of (name, callable)
    audit_logger: Any = None  # invoked with promotion events


class GateExecuted(BaseModel):
    """Per-gate execution result."""

    type: str
    passed: bool
    detail: str = ""


class PromotedItem(BaseModel):
    """One item moved across stages."""

    item_id: str
    from_stage: str
    to_stage: str


class PromoteInPipelineResult(BaseModel):
    """Output."""

    promoted_items: list[PromotedItem] = Field(default_factory=list)
    gates_executed: list[GateExecuted] = Field(default_factory=list)
    failed_gate: GateExecuted | None = None
    rollback_performed: bool = False
    promotion_id: str | None = None
    dry_run: bool = False
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _next_promotion_id() -> str:
    """Generate a non-cryptographic promotion id (deterministic enough for tests)."""
    import uuid

    return f"prom_{uuid.uuid4().hex[:12]}"


def _enumerate_items(
    pipeline_id: str, source_stage: str, fabric_client: Any
) -> list[str]:
    """Resolve items to promote; the fabric_client may return None (legacy pipeline)."""
    if fabric_client is None:
        # Without a real client, assume an empty pipeline.
        return []
    try:
        result = fabric_client.list_pipeline_items(pipeline_id, source_stage)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(result, (list, tuple)):
        return []
    return [str(item) for item in result]


# ---------------------------------------------------------------------------
# Built-in gate executors
# ---------------------------------------------------------------------------


def _run_pre_deploy_check(
    gate: QualityGate, *, findings: list[dict[str, str]] | None = None
) -> GateExecuted:
    from powerbi_orchestrator_mcp.tools.pre_deploy_check import pre_deploy_check

    findings = findings or []
    result = pre_deploy_check(findings, profile=gate.profile or "standard")
    return GateExecuted(
        type="pre_deploy_check",
        passed=result.passed,
        detail=f"profile={gate.profile or 'standard'}",
    )


def _run_audit_score(
    gate: QualityGate, *, pbip_path: str | None
) -> GateExecuted:
    import asyncio

    if not pbip_path:
        return GateExecuted(
            type="audit_model_and_report",
            passed=False,
            detail="no pbip_path provided",
        )
    from powerbi_orchestrator_mcp.tools.audit_model_and_report import (
        audit_model_and_report,
    )

    try:
        result = asyncio.run(audit_model_and_report(pbip_path=pbip_path))
    except Exception as exc:  # noqa: BLE001
        return GateExecuted(
            type="audit_model_and_report",
            passed=False,
            detail=f"runner error: {exc}",
        )

    threshold = gate.threshold if gate.threshold is not None else 80.0
    score = result.overall_score if hasattr(result, "overall_score") else 0.0
    return GateExecuted(
        type="audit_model_and_report",
        passed=score >= threshold,
        detail=f"score={score} threshold={threshold}",
    )


def _run_regression(
    gate: QualityGate,
    *,
    pbip_path: str | None,  # noqa: ARG001
    baseline_path: str | None,
) -> GateExecuted:
    import asyncio

    if not baseline_path:
        return GateExecuted(
            type="run_dax_regression",
            passed=False,
            detail="missing baseline_path",
        )
    from powerbi_orchestrator_mcp.tools.run_dax_regression import (
        run_dax_regression,
    )

    try:
        result = asyncio.run(
            run_dax_regression(
                baseline_path=baseline_path,
                tolerance_pct=gate.threshold if gate.threshold is not None else 0.1,
            )
        )
    except Exception as exc:  # noqa: BLE001
        return GateExecuted(
            type="run_dax_regression",
            passed=False,
            detail=f"runner error: {exc}",
        )

    drift = getattr(result, "max_drift_pct", 0.0)
    threshold = gate.threshold if gate.threshold is not None else 0.1
    return GateExecuted(
        type="run_dax_regression",
        passed=drift <= threshold,
        detail=f"max_drift_pct={drift} threshold={threshold}",
    )


def _run_custom_gate(name: str, fn: Callable[[], bool]) -> GateExecuted:
    try:
        passed = bool(fn())
    except Exception as exc:  # noqa: BLE001
        return GateExecuted(
            type=f"custom:{name}",
            passed=False,
            detail=f"error: {exc}",
        )
    return GateExecuted(type=f"custom:{name}", passed=passed)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def promote_in_pipeline(
    pipeline_id: str,
    source_stage: str = "dev",
    target_stage: str = "test",
    items: list[str] | None = None,
    quality_gates: list[QualityGate] | None = None,
    notify_on_failure: bool = True,  # noqa: ARG001
    dry_run: bool = False,
    fabric_client: Any = None,
    custom_gates: list[tuple[str, Callable[[], bool]]] | None = None,
    audit_logger: Any = None,
    pbip_path: str | None = None,
    baseline_path: str | None = None,
    pre_deploy_findings: list[dict[str, str]] | None = None,
) -> PromoteInPipelineResult:
    """Promote items between pipeline stages, executing quality gates."""
    warnings: list[str] = []

    if source_stage not in _VALID_STAGES or target_stage not in _VALID_STAGES:
        return PromoteInPipelineResult(
            dry_run=dry_run,
            warnings=[f"invalid stages (source={source_stage}, target={target_stage})"],
        )
    if source_stage == target_stage:
        return PromoteInPipelineResult(
            dry_run=dry_run,
            warnings=[f"source and target must differ ({source_stage}={target_stage})"],
        )

    gates = quality_gates or []
    custom = custom_gates or []

    # Resolve candidate items.
    candidate_items = items or _enumerate_items(pipeline_id, source_stage, fabric_client)
    if not candidate_items and not dry_run:
        warnings.append(
            "no items to promote (pipeline may be empty or fabric_client absent)"
        )

    # Quality-gate execution.
    gate_results: list[GateExecuted] = []
    failed: GateExecuted | None = None

    for gate in gates:
        if gate.type == "pre_deploy_check":
            res = _run_pre_deploy_check(gate, findings=pre_deploy_findings)
        elif gate.type == "audit_model_and_report":
            res = _run_audit_score(gate, pbip_path=pbip_path)
        elif gate.type == "run_dax_regression":
            res = _run_regression(gate, pbip_path=pbip_path, baseline_path=baseline_path)
        else:  # custom handled below
            continue
        gate_results.append(res)
        if not res.passed and gate.blocking:
            failed = res
            break

    for name, fn in custom:
        res = _run_custom_gate(name, fn)
        gate_results.append(res)
        if not res.passed:
            failed = res
            break

    if failed is not None:
        if audit_logger is not None:
            try:
                audit_logger(
                    action="promote_failed",
                    pipeline_id=pipeline_id,
                    source_stage=source_stage,
                    target_stage=target_stage,
                    failed_gate=failed.type,
                )
            except Exception:  # noqa: BLE001
                warnings.append("audit_logger raised; ignored")
        return PromoteInPipelineResult(
            promoted_items=[],
            gates_executed=gate_results,
            failed_gate=failed,
            rollback_performed=False,
            dry_run=dry_run,
            warnings=warnings,
        )

    # All gates pass — emit promotion events.
    promoted: list[PromotedItem] = []
    promotion_id: str | None = None
    if not dry_run:
        promotion_id = _next_promotion_id()
        for item_id in candidate_items:
            try:
                if fabric_client is not None:
                    fabric_client.deploy_pipeline_item(
                        pipeline_id=pipeline_id,
                        source_stage=source_stage,
                        target_stage=target_stage,
                        item_id=item_id,
                    )
                promoted.append(
                    PromotedItem(
                        item_id=item_id,
                        from_stage=source_stage,
                        to_stage=target_stage,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"failed to promote {item_id}: {exc}")
    else:
        # Dry-run: report what we *would* promote without touching Fabric.
        promotion_id = f"dry_{pipeline_id[:8]}"
        for item_id in candidate_items:
            promoted.append(
                PromotedItem(
                    item_id=item_id,
                    from_stage=source_stage,
                    to_stage=target_stage,
                )
            )

    if audit_logger is not None:
        try:
            audit_logger(
                action="promoted",
                pipeline_id=pipeline_id,
                source_stage=source_stage,
                target_stage=target_stage,
                items=[p.item_id for p in promoted],
            )
        except Exception:  # noqa: BLE001
            warnings.append("audit_logger raised; ignored")

    return PromoteInPipelineResult(
        promoted_items=promoted,
        gates_executed=gate_results,
        promotion_id=promotion_id,
        dry_run=dry_run,
        warnings=warnings,
    )


__all__ = [
    "GateExecuted",
    "PromoteInPipeline",
    "PromoteInPipelineResult",
    "PromotedItem",
    "QualityGate",
    "promote_in_pipeline",
]


# Re-export the gate types so callers can construct them.
_ = json
