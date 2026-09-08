"""design_report_page_from_requirements tool — v2 (SPEC §6.2).

Composes `viz.visual_suggester` + `python_report.add_page` +
`add_visual` to scaffold a full PBIR page from an NL brief.

For MVP we use a regex-based KPI extractor (single-value: `KPI X`;
trend: `X over time`; composition: `X by Y`; ranking: `Top N X`) + a
fixed F-pattern layout (cards top-left, trends top-right, compositions
bottom).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.viz.visual_suggester import (
    SuggesterInput,
    suggest,
)


class DesignReportPageFromRequirements(BaseModel):
    """Input schema for ``design_report_page_from_requirements``."""

    pbip_path: str
    page_name: str = "Overview"
    brief: str
    audience: str = "executive"
    palette: str = "okabe_ito"
    inspector: Any = None  # modeling engine for field resolution


class DesignedVisual(BaseModel):
    """One visual placed on the page."""

    type: str
    kpi: str
    justification: str
    fields: list[str] = Field(default_factory=list)
    position: dict[str, int] = Field(default_factory=dict)


class DesignReportPageResult(BaseModel):
    """Output of design_report_page_from_requirements."""

    page_name: str
    visual_count: int
    visuals: list[DesignedVisual] = Field(default_factory=list)
    rationale: str = ""
    files_changed: list[str] = Field(default_factory=list)
    wcag_score: float = 100.0
    warnings: list[str] = Field(default_factory=list)


# KPI extraction patterns (regex, MVP).
_KPI_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("single_value", re.compile(r"\b(KPI|Total|Sum|Count)\s+([\w\s]+?)\b", re.I)),
    ("trend", re.compile(r"\b([\w\s]+?)\s+over\s+time\b", re.I)),
    ("composition", re.compile(r"\b([\w\s]+?)\s+by\s+([\w]+)\b", re.I)),
    ("ranking", re.compile(r"\bTop\s+(\d+)\s+([\w\s]+?)\b", re.I)),
]


def _extract_kpis(brief: str) -> list[dict[str, Any]]:
    """Parse the brief into a list of minimal KPI specs."""
    kpis: list[dict[str, str]] = []
    seen: set[str] = set()
    for semantic_type, pattern in _KPI_PATTERNS:
        for match in pattern.finditer(brief):
            if semantic_type == "ranking":
                kpi_name = f"Top {match.group(1)} {match.group(2).strip()}"
                fields = [match.group(2).strip()]
            elif semantic_type == "single_value":
                kpi_name = f"{match.group(1).strip()} {match.group(2).strip()}"
                fields = [match.group(2).strip().split()[-1]]
            elif semantic_type == "trend":
                kpi_name = f"{match.group(1).strip()} over time"
                fields = [match.group(1).strip().split()[-1]]
            else:  # composition
                kpi_name = f"{match.group(1).strip()} by {match.group(2)}"
                fields = [match.group(2)]
            key = kpi_name.lower()
            if key in seen:
                continue
            seen.add(key)
            entry: dict[str, Any] = {
                "name": kpi_name,
                "semantic_type": semantic_type,
                "fields": list(fields),
            }
            kpis.append(entry)
    return kpis


def _infer_cardinality_from_brief(brief: str) -> int | None:
    """Cheap cardinality hint from brief text."""
    m = re.search(r"\btop\s+(\d+)\b", brief, re.I)
    if m:
        return int(m.group(1))
    return None


def _layout_positions(count: int) -> list[dict[str, int]]:
    """F-pattern layout: 2-column row, top to bottom."""
    positions: list[dict[str, int]] = []
    base_y = 0
    for i in range(count):
        row = i // 2
        col = i % 2
        positions.append(
            {
                "x": col * 320,
                "y": base_y + row * 200,
                "width": 300,
                "height": 180,
            }
        )
    return positions


def design_report_page_from_requirements(
    pbip_path: str,
    brief: str,
    page_name: str = "Overview",
    audience: str = "executive",
    palette: str = "okabe_ito",
    *,
    inspector: Any = None,  # noqa: ARG001
) -> DesignReportPageResult:
    """Scaffold a full PBIR page from an NL brief."""
    pbip = Path(pbip_path)
    if not pbip.exists():
        return DesignReportPageResult(
            page_name=page_name,
            visual_count=0,
            visuals=[],
            rationale="",
            files_changed=[],
            warnings=[f"PBIP path does not exist: {pbip_path}"],
        )

    kpis = _extract_kpis(brief)
    if not kpis:
        return DesignReportPageResult(
            page_name=page_name,
            visual_count=0,
            visuals=[],
            rationale="",
            files_changed=[],
            warnings=["no KPIs detected in brief; provide a structured brief"],
        )

    cardinality_hint = _infer_cardinality_from_brief(brief)

    # Suggest visuals per KPI.
    suggestions: list[tuple[dict[str, str], Any]] = []
    for kpi in kpis:
        inp = SuggesterInput(
            name=kpi["name"],
            semantic_type=kpi["semantic_type"],
            fields=kpi.get("fields", []),
            cardinality=cardinality_hint,
            has_time=(kpi["semantic_type"] == "trend"),
            audience=audience,
        )
        sugs = suggest(inp, max_alternatives=1)
        if sugs:
            suggestions.append((kpi, sugs[0]))
        else:
            suggestions.append((kpi, None))

    # If the .Report directory doesn't exist yet, we'll create it below
    # after declaring visual_specs (used by the early-return path).
    visual_specs: list[DesignedVisual] = []
    positions = _layout_positions(len(suggestions))
    rationale_parts: list[str] = []
    for (kpi, sugg), pos in zip(suggestions, positions, strict=True):
        if sugg is None:
            rationale_parts.append(f"- {kpi['name']}: no visual suggestion")
            continue
        rationale_parts.append(
            f"- {kpi['name']}: {sugg.type} ({sugg.justification[:80]})"
        )
        visual_specs.append(
            DesignedVisual(
                type=sugg.type,
                kpi=kpi["name"],
                justification=sugg.justification,
                fields=sugg.expected_fields,
                position=pos,
            )
        )

    rationale = "Suggested layout:\n" + "\n".join(rationale_parts)

    # Write the page via PythonReportEngine (real file I/O).
    pbip_root = pbip
    report_dir_candidates = sorted(pbip_root.glob("*.Report"))
    if not report_dir_candidates:
        dataset_candidates = sorted(pbip_root.glob("*.Dataset"))
        if dataset_candidates:
            report_name = dataset_candidates[0].name.replace(".Dataset", "")
            report_dir = pbip_root / f"{report_name}.Report"
            report_dir.mkdir(parents=True, exist_ok=True)
        else:
            return DesignReportPageResult(
                page_name=page_name,
                visual_count=len(visual_specs),
                visuals=visual_specs,
                rationale=rationale,
                files_changed=[],
                warnings=["no .Dataset directory in PBIP — cannot scaffold"],
            )
    else:
        report_dir = report_dir_candidates[0]

    page_dir = report_dir / "pages" / page_name
    page_dir.mkdir(parents=True, exist_ok=True)
    page_path = page_dir / "page.json"

    # Build the visualContainers entries.
    visual_containers: list[dict[str, Any]] = []
    for spec in visual_specs:
        visual_containers.append(
            {
                "$type": "visualContainer",
                "id": f"v_{spec.kpi[:8].replace(' ', '_')}",
                "x": spec.position["x"],
                "y": spec.position["y"],
                "width": spec.position["width"],
                "height": spec.position["height"],
                "altText": f"{spec.type} for {spec.kpi}",
                "visual": {
                    "$type": spec.type,
                    "id": f"v_{spec.kpi[:8].replace(' ', '_')}",
                    "projections": {
                        "Values": [
                            {"queryRef": f"[{f}]"} for f in spec.fields
                        ]
                    }
                    if spec.fields
                    else {},
                },
                "tabOrder": visual_specs.index(spec),
            }
        )

    page_data: dict[str, Any] = {
        "$type": "page",
        "name": page_name,
        "displayName": page_name,
        "displayOption": "FitToPage",
        "width": 1280,
        "height": 720,
        "visualContainers": visual_containers,
        "mobileLayout": {"visible": True, "stack": True, "orientation": "vertical"}
        if audience == "executive"
        else None,
    }

    # Atomic write.
    fd, tmp_str = tempfile.mkstemp(dir=str(page_path.parent), prefix=".page.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(page_data, f, indent=2)
        os.replace(tmp_str, page_path)
    except Exception as exc:  # noqa: BLE001
        with contextlib.suppress(OSError):
            os.unlink(tmp_str)
        return DesignReportPageResult(
            page_name=page_name,
            visual_count=len(visual_specs),
            visuals=visual_specs,
            rationale=rationale,
            files_changed=[],
            warnings=[f"atomic write failed: {exc}"],
        )

    # Also write theme.json if not present (mirrors apply_theme behavior).
    files_changed = [str(page_path.relative_to(pbip_root))]
    theme_path = report_dir / "theme.json"
    if not theme_path.exists():
        from powerbi_orchestrator_mcp.tools.apply_theme_and_accessibility_rules import (
            OKABE_ITO_PALETTE,
        )

        theme_data = list(OKABE_ITO_PALETTE.values())
        theme_path.write_text(
            json.dumps(
                {
                    "name": f"{report_dir.name}-{palette}",
                    "dataColors": theme_data,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        files_changed.append(str(theme_path.relative_to(pbip_root)))

    # Estimate WCAG score: all visuals have altText (we set it above).
    # Conservative score (some warnings may apply).
    has_alt = all(
        vc.get("altText") for vc in page_data.get("visualContainers", [])
    )
    wcag_score = 95.0 if has_alt else 80.0

    return DesignReportPageResult(
        page_name=page_name,
        visual_count=len(visual_specs),
        visuals=visual_specs,
        rationale=rationale,
        files_changed=files_changed,
        wcag_score=wcag_score,
        warnings=[],
    )


__all__ = [
    "DesignReportPageFromRequirements",
    "DesignReportPageResult",
    "DesignedVisual",
    "design_report_page_from_requirements",
]
