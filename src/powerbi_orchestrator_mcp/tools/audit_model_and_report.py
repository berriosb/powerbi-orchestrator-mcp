"""audit_model_and_report tool — composes BPA + DAX lint + WCAG (SPEC §6.1 #5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

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
    star_schema: bool = False  # placeholder for v2
    naming: bool = True  # included in BPA via ruleset


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

    overall_score: float  # 0-100
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
) -> AuditResult:
    """Run a composite audit (BPA + DAX lint + WCAG) on a PBIP folder.

    For MVP, BPA is mocked (no real te binary required); DAX lint runs
    over the supplied measures; WCAG walks pages/*.json. The overall
    score is a weighted average.
    """
    checks = checks or AuditCheck()
    findings: list[dict[str, Any]] = []
    warnings: list[str] = []
    bpa_score: float | None = None
    bpa_findings_count = 0
    dax_lint_findings_count = 0
    wcag_score: float | None = None
    wcag_findings_count = 0
    auto_fixable_count = 0

    # 1. BPA (mocked — no real subprocess).
    if checks.bpa:
        bpa_runner = BpaRunner(
            mock_findings=[],
            mock_score=100.0,  # MVP: assume clean when mocked
        )
        bpa_result = await bpa_runner.run(Path(pbip_path), ruleset_name=bpa_ruleset)
        bpa_score = bpa_result.score
        bpa_findings_count = len(bpa_result.findings)
        auto_fixable_count = sum(
            1 for f in bpa_result.findings if f.auto_fixable
        )

    # 2. DAX lint.
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

    # 3. Accessibility (WCAG).
    if checks.accessibility:
        auditor = WcagAuditor()
        wcag = auditor.audit_pbip(Path(pbip_path))
        wcag_score = wcag.score
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

    # Weighted average: 50% BPA + 25% DAX + 25% WCAG.
    components: list[tuple[float | None, float]] = [
        (bpa_score, 0.5),
        (100.0 - dax_lint_findings_count * 2, 0.25),
        (wcag_score, 0.25),
    ]
    total_weight = sum(w for _, w in components)
    weighted_sum = sum((s or 0) * w for s, w in components)
    overall_score = weighted_sum / total_weight if total_weight else 0.0

    if not Path(pbip_path).exists():
        warnings.append(f"PBIP path does not exist: {pbip_path}")

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
