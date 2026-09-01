"""apply_theme_and_accessibility_rules tool (SPEC §6.1 #12).

Composes WCAG auditor (find issues) + theme generator (write theme.json)
+ automatic alt-text backfill for visuals missing altText.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.validation.accessibility.wcag_auditor import (
    WcagAuditor,
)

# Colorblind-safe palette (Okabe-Ito 8-color) per spec.
OKABE_ITO_PALETTE: dict[str, str] = {
    "black": "#000000",
    "orange": "#E69F00",
    "skyBlue": "#56B4E9",
    "bluishGreen": "#009E73",
    "yellow": "#F0E442",
    "blue": "#0072B2",
    "vermillion": "#D55E00",
    "reddishPurple": "#CC79A7",
}


class ApplyThemeAndAccessibilityRules(BaseModel):
    """Input schema for ``apply_theme_and_accessibility_rules`` tool (SPEC §6.1 #12)."""

    pbip_path: str
    palette: str = "okabe_ito"  # okabe_ito | ibm | viridis
    auto_backfill_alt_text: bool = True
    alt_text_template: str = "{visual_type} visualizing measure {first_measure}"


class AccessibilityResult(BaseModel):
    """Output of apply_theme_and_accessibility_rules."""

    theme_written: bool = False
    alt_texts_added: int = 0
    wcag_score_before: float = 0.0
    wcag_score_after: float = 0.0
    files_changed: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _build_theme(palette_name: str) -> dict[str, Any]:
    """Build a minimal Power BI theme.json from a palette name."""
    if palette_name == "okabe_ito":
        data_colors = list(OKABE_ITO_PALETTE.values())
    else:
        # Default: Okabe-Ito for unknown palettes (safe).
        data_colors = list(OKABE_ITO_PALETTE.values())

    return {
        "name": f"powerbi-orchestrator-{palette_name}",
        "dataColors": data_colors,
        "background": "#FFFFFF",
        "foreground": "#252423",
        "tableAccent": "#118DFF",
        "good": "#339933",
        "neutral": "#DDDDDD",
        "bad": "#C00000",
        "maximum": data_colors[0] if data_colors else "#000000",
        "center": data_colors[3] if len(data_colors) > 3 else "#009E73",
        "minimum": data_colors[1] if len(data_colors) > 1 else "#E69F00",
        "textClasses": {
            "title": {"fontFace": "Segoe UI Semibold", "fontSize": 14, "color": "#252423"},
            "header": {"fontFace": "Segoe UI Semibold", "fontSize": 12, "color": "#252423"},
            "label": {"fontFace": "Segoe UI", "fontSize": 10, "color": "#252423"},
        },
    }


def _first_measure_from_visual(visual_data: dict[str, Any]) -> str | None:
    """Best-effort: extract the first measure/column referenced by the visual."""
    proj = visual_data.get("projections") or {}
    # Slicer doesn't have projections, but bar/line/cards do.
    for key in ("Values", "Y", "X", "Category", "Details"):
        items = proj.get(key) or []
        for item in items:
            qref = item.get("queryRef") or item.get("Name")
            if qref:
                return str(qref)
    return None


def apply_theme_and_accessibility_rules(
    pbip_path: str,
    palette: str = "okabe_ito",
    auto_backfill_alt_text: bool = True,
    alt_text_template: str = "{visual_type} visualizing measure {first_measure}",
) -> AccessibilityResult:
    """Apply a theme + backfill alt text + run WCAG audit on a PBIP.

    The order of operations:
    1. Run WCAG audit (baseline score).
    2. Write theme.json with the chosen colorblind-safe palette.
    3. Backfill alt text on visuals missing it (if requested).
    4. Re-run WCAG audit (after-score).
    """
    pbip = Path(pbip_path)
    files_changed: list[str] = []
    warnings: list[str] = []

    # 1. Baseline WCAG audit.
    auditor = WcagAuditor()
    if not pbip.exists():
        return AccessibilityResult(
            wcag_score_before=0.0,
            wcag_score_after=0.0,
            warnings=[f"PBIP path does not exist: {pbip_path}"],
        )

    before = auditor.audit_pbip(pbip)

    # 2. Write theme.json.
    theme = _build_theme(palette)
    report_dir = next(iter(pbip.glob("*.Report")), None)
    if report_dir is None:
        warnings.append("no .Report directory found; theme.json not written")
    else:
        theme_path = report_dir / "theme.json"
        theme_path.write_text(json.dumps(theme, indent=2), encoding="utf-8")
        files_changed.append(str(theme_path.relative_to(pbip)))

    # 3. Backfill alt text.
    alt_texts_added = 0
    if auto_backfill_alt_text and report_dir is not None:
        for page_json in (report_dir / "pages").glob("*/page.json"):
            data = json.loads(page_json.read_text(encoding="utf-8"))
            modified = False
            for vc in data.get("visualContainers", []):
                if not vc.get("altText"):
                    v_type = vc.get("visual", {}).get("$type", "Visual")
                    first_measure = _first_measure_from_visual(
                        vc.get("visual", {})
                    ) or "data"
                    vc["altText"] = alt_text_template.format(
                        visual_type=v_type,
                        first_measure=first_measure,
                    )
                    alt_texts_added += 1
                    modified = True
            if modified:
                page_json.write_text(
                    json.dumps(data, indent=2), encoding="utf-8"
                )
                files_changed.append(str(page_json.relative_to(pbip)))

    # 4. Re-audit.
    after = auditor.audit_pbip(pbip)

    return AccessibilityResult(
        theme_written=(report_dir is not None),
        alt_texts_added=alt_texts_added,
        wcag_score_before=before.score,
        wcag_score_after=after.score,
        files_changed=files_changed,
        warnings=warnings,
    )
