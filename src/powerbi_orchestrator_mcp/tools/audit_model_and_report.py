"""audit_model_and_report tool — composes BPA + DAX lint + WCAG (SPEC §6.1 #5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.engines.errors import EngineError
from powerbi_orchestrator_mcp.validation.accessibility.wcag_auditor import (
    WcagAuditor,
)
from powerbi_orchestrator_mcp.validation.bpa_runner import BpaRunner
from powerbi_orchestrator_mcp.validation.dax_linter import DaxLinter


class AuditCheck(BaseModel):
    """Which checks to run (per spec §5 subset)."""

    bpa: bool = True
    dax_lint: bool = True
    accessibility: bool = True
    star_schema: bool = False
    naming: bool = True


class AuditModelAndReport(BaseModel):
    """Input schema for ``audit_model_and_report`` tool (SPEC §6.1 #5)."""

    pbip_path: str
    bpa_ruleset: str = "default"
    dax_measures: dict[str, str] = Field(
        default_factory=dict,
        description="Map of measure_name → dax_expression to lint.",
    )
    checks: AuditCheck = Field(default_factory=AuditCheck)


class AuditResult(BaseModel):
    """Aggregated audit report."""

    overall_score: float = Field(ge=0.0, le=100.0)
    bpa_score: float | None = None
    bpa_findings_count: int = 0
    dax_lint_findings_count: int = 0
    wcag_score: float | None = None
    wcag_findings_count: int = 0
    auto_fixable_count: int = 0
    findings: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


async def audit_model_and_report(
    pbip_path: str,
    bpa_ruleset: str = "default",
    dax_measures: dict[str, str] | None = None,
    checks: AuditCheck | None = None,
    *,
    bpa_runner: BpaRunner | None = None,
) -> AuditResult:
    from powerbi_orchestrator_mcp.validation.path_safety import (
        validate_safe_pbip_path,
    )

    resolved_path = validate_safe_pbip_path(pbip_path, must_exist=False)
    pbip_path = str(resolved_path)

    checks = checks or AuditCheck()
    findings: list[dict[str, Any]] = []
    warnings: list[str] = []
    bpa_score: float | None = None
    bpa_findings_count = 0
    dax_lint_findings_count = 0
    wcag_score: float | None = None
    wcag_findings_count = 0
    auto_fixable_count = 0

    target_p = Path(pbip_path)
    is_valid_pbip = False
    if not target_p.exists():
        warnings.append(f"PBIP path does not exist: {pbip_path}")
    else:
        has_report = bool(list(target_p.glob("*.Report"))) or target_p.name.endswith(".Report")
        has_model = (
            bool(list(target_p.glob("*.SemanticModel")))
            or bool(list(target_p.glob("*.Dataset")))
            or target_p.name.endswith(".SemanticModel")
            or target_p.name.endswith(".Dataset")
        )
        has_pbip_file = bool(list(target_p.glob("*.pbip"))) or target_p.name.endswith(".pbip")
        if has_report or has_model or has_pbip_file:
            is_valid_pbip = True
        else:
            warnings.append(
                f"Target directory is not a valid PBIP project (missing .Report, .SemanticModel, or .pbip): {pbip_path}"
            )

    if checks.bpa and target_p.exists() and is_valid_pbip:
        runner = bpa_runner or BpaRunner()
        try:
            bpa_result = await runner.run(target_p, ruleset_name=bpa_ruleset)
            bpa_score = max(0.0, min(100.0, float(bpa_result.score)))
            bpa_findings_count = len(bpa_result.findings)
            auto_fixable_count = sum(
                1 for f in bpa_result.findings if f.auto_fixable
            )
            for bf in bpa_result.findings:
                findings.append(
                    {
                        "rule_id": bf.rule_id,
                        "severity": bf.severity,
                        "message": bf.message,
                        "rewrite_suggestion": bf.fix_suggestion,
                        "object_name": bf.object_name,
                        "source": "bpa",
                        "auto_fixable": bf.auto_fixable,
                    }
                )
        except EngineError as exc:
            warnings.append(f"Tabular Editor CLI (te2) not available; BPA skipped: {exc}")
            bpa_score = None
            bpa_findings_count = 0

    if checks.dax_lint and dax_measures:
        linter = DaxLinter()
        results = linter.lint_batch(dax_measures)
        for measure_name, measure_findings in results.items():
            for df in measure_findings:
                findings.append(
                    {
                        "rule_id": df.rule_id,
                        "severity": df.severity,
                        "message": df.message,
                        "rewrite_suggestion": df.rewrite_suggestion,
                        "object_name": measure_name,
                        "source": "dax_lint",
                    }
                )
                dax_lint_findings_count += 1

    if checks.accessibility and target_p.exists() and is_valid_pbip:
        auditor = WcagAuditor()
        wcag = auditor.audit_pbip(target_p)
        wcag_score = max(0.0, min(100.0, float(wcag.score)))
        wcag_findings_count = len(wcag.findings)
        for wf in wcag.findings:
            findings.append(
                {
                    "rule_id": wf.rule_id,
                    "severity": wf.severity,
                    "message": wf.message,
                    "remediation_hint": wf.remediation_hint,
                    "object_path": wf.object_path,
                    "source": "wcag",
                }
            )

    dax_score: float | None = None
    if checks.dax_lint and dax_measures:
        dax_score = max(0.0, min(100.0, 100.0 - dax_lint_findings_count * 2.0))

    active_components: list[tuple[float, float]] = []
    if bpa_score is not None:
        active_components.append((bpa_score, 0.5))
    if dax_score is not None:
        active_components.append((dax_score, 0.25))
    if wcag_score is not None:
        active_components.append((wcag_score, 0.25))

    total_weight = sum(w for _, w in active_components)
    if total_weight > 0.0 and target_p.exists() and is_valid_pbip:
        weighted_sum = sum(score * w for score, w in active_components)
        overall_score = round(max(0.0, min(100.0, weighted_sum / total_weight)), 2)
    else:
        overall_score = 0.0

    return AuditResult(
        overall_score=overall_score,
        bpa_score=bpa_score,
        bpa_findings_count=bpa_findings_count,
        dax_lint_findings_count=dax_lint_findings_count,
        wcag_score=wcag_score,
        wcag_findings_count=wcag_findings_count,
        auto_fixable_count=auto_fixable_count,
        findings=findings,
        warnings=warnings,
    )
