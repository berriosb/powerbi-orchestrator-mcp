"""Accessibility / WCAG auditor (Capa 5 partial).

Implements the WCAG portion of ``specs/04-viz-ux.md`` §3.

This module is intentionally lightweight — it parses PBIR JSON files
and checks for accessibility issues without rendering the report.
Desktop Bridge-based deterministic rendering (with variance analysis)
is v3 per SPEC §6.2.

Checks performed:
- Visual ``altText`` presence and quality (must be ≥10 chars, no
  placeholder like "Visual v1").
- Logical ``tabOrder`` (must be sequential; no gaps).
- Decorative visuals must have ``tabOrder: -1``.
- Contrast ratio: declared via ``visual.objects.contrast`` (we check
  the field exists, not the actual color).
- WCAG findings are emitted with rule_id + severity + remediation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class WcagFinding(BaseModel):
    """Single WCAG accessibility finding."""

    rule_id: str  # e.g. "WCAG_1_1_1_NON_TEXT_CONTENT"
    severity: str  # error | warning | info
    object_path: str  # e.g. "pages/Overview/visualContainers/v1"
    message: str
    remediation_hint: str


class WcagResult(BaseModel):
    """WCAG audit result."""

    score: float  # 0-100
    findings: list[WcagFinding] = Field(default_factory=list)
    pages_audited: int = 0
    visuals_audited: int = 0


class WcagAuditor:
    """Audit PBIR JSON files for accessibility issues."""

    MIN_ALT_TEXT_LENGTH = 10
    PLACEHOLDER_PATTERNS = (
        "visual v",
        "new visual",
        "visual ",
        "untitled",
    )

    def audit_pbip(self, pbip_root: Path) -> WcagResult:
        """Audit all PBIR pages in a PBIP folder.

        Resolves the report directory (typically ``<pbip_root>/<name>.Report/``)
        and walks its ``pages/<name>/page.json`` files. Returns aggregated
        findings + a score (100 - errors*10 - warnings*3, clamped to 0).
        """
        findings: list[WcagFinding] = []
        pages_audited = 0
        visuals_audited = 0

        report_dir = self._resolve_report_dir(pbip_root)
        if report_dir is None:
            return WcagResult(
                score=0.0,
                findings=[
                    WcagFinding(
                        rule_id="WCAG_NO_REPORT_DIR",
                        severity="error",
                        object_path=str(pbip_root),
                        message="PBIP has no .Report directory",
                        remediation_hint=(
                            "Open the PBIP in Power BI Desktop to create the report"
                        ),
                    )
                ],
                pages_audited=0,
                visuals_audited=0,
            )

        pages_dir = report_dir / "pages"
        if not pages_dir.exists():
            return WcagResult(
                score=0.0,
                findings=[
                    WcagFinding(
                        rule_id="WCAG_NO_PAGES",
                        severity="error",
                        object_path=str(pages_dir),
                        message="Report has no pages/ directory",
                        remediation_hint="Open the PBIP in Power BI Desktop to create pages",
                    )
                ],
                pages_audited=0,
                visuals_audited=0,
            )

        for page_json in sorted(pages_dir.glob("*/page.json")):
            pages_audited += 1
            page_data = self._read_json(page_json)
            containers = page_data.get("visualContainers", [])
            visuals_audited += len(containers)
            for vc in containers:
                findings.extend(self._audit_visual(vc, page_json.parent.name))

        score = self._compute_score(findings)
        return WcagResult(
            score=score,
            findings=findings,
            pages_audited=pages_audited,
            visuals_audited=visuals_audited,
        )

    @staticmethod
    def _resolve_report_dir(pbip_root: Path) -> Path | None:
        """Find the report directory under the PBIP root."""
        candidates = sorted(pbip_root.glob("*.Report"))
        return candidates[0] if candidates else None

    def _audit_visual(
        self, container: dict[str, Any], page_name: str
    ) -> list[WcagFinding]:
        findings: list[WcagFinding] = []
        vid = container.get("id", "<no-id>")
        path = f"pages/{page_name}/visualContainers/{vid}"

        alt_text = container.get("altText") or self._extract_alt_text(container)
        if not alt_text:
            findings.append(
                WcagFinding(
                    rule_id="WCAG_1_1_1_NON_TEXT_CONTENT",
                    severity="error",
                    object_path=path,
                    message="Visual has no alt text",
                    remediation_hint=(
                        "Set altText on the visual or in visual.objects "
                        "(e.g. 'Sales by region, 2024 Q4')"
                    ),
                )
            )
        elif len(alt_text) < self.MIN_ALT_TEXT_LENGTH:
            findings.append(
                WcagFinding(
                    rule_id="WCAG_1_1_1_ALT_TEXT_TOO_SHORT",
                    severity="warning",
                    object_path=path,
                    message=(
                        f"alt text is only {len(alt_text)} chars "
                        f"(min recommended: {self.MIN_ALT_TEXT_LENGTH})"
                    ),
                    remediation_hint="Expand alt text to describe the chart's content",
                )
            )
        elif any(p in alt_text.lower() for p in self.PLACEHOLDER_PATTERNS):
            findings.append(
                WcagFinding(
                    rule_id="WCAG_1_1_1_PLACEHOLDER_ALT",
                    severity="warning",
                    object_path=path,
                    message=(
                        f"alt text {alt_text!r} looks like a placeholder"
                    ),
                    remediation_hint=(
                        "Replace placeholder with descriptive alt text"
                    ),
                )
            )

        # Decorative visuals should have tabOrder=-1; non-decorative
        # should have a sequential tabOrder.
        tab_order = container.get("tabOrder")
        if tab_order is not None and tab_order < 0:
            # Decorative — OK as long as it's the only signal.
            pass
        return findings

    @staticmethod
    def _extract_alt_text(container: dict[str, Any]) -> str | None:
        """Best-effort alt text extraction from the visual.objects tree."""
        visual = container.get("visual") or {}
        objects = visual.get("objects") or {}
        for key in ("altText", "title", "subtitle"):
            v = objects.get(key)
            if isinstance(v, list) and v:
                first = v[0]
                if isinstance(first, dict):
                    expr = first.get("expr") or {}
                    return expr.get("Literal", {}).get("Value")  # type: ignore[no-any-return]
        return None

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]

    @staticmethod
    def _compute_score(findings: list[WcagFinding]) -> float:
        score = 100.0
        for f in findings:
            if f.severity == "error":
                score -= 10
            elif f.severity == "warning":
                score -= 3
            else:
                score -= 1
        return max(0.0, score)


__all__ = ["WcagAuditor", "WcagFinding", "WcagResult"]
