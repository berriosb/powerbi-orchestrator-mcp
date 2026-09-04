"""create_report_from_dataset tool — v1.1 (SPEC §6.3).

Scaffolds a PBIR (Power BI Report) folder from an existing dataset.
Creates <name>.Report/, theme.json, report.json, and a sample page
with up to 4 visuals based on the dataset schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from powerbi_orchestrator_mcp.tools.apply_theme_and_accessibility_rules import (
    OKABE_ITO_PALETTE,
)


class CreateReportFromDataset(BaseModel):
    """Input schema for ``create_report_from_dataset`` (SPEC §6.3 #2)."""

    pbip_path: str
    page_name: str = "Overview"
    visual_count: int = 2  # 1-4 supported
    theme: str = "okabe_ito"
    include_card: bool = True
    inspector: Any = None  # production: modeling engine; tests: mock


def create_report_from_dataset(
    pbip_path: str,
    *,
    page_name: str = "Overview",
    visual_count: int = 2,
    theme: str = "okabe_ito",
    include_card: bool = True,
    inspector: Any = None,
) -> dict[str, Any]:
    """Scaffold a PBIR folder from an existing dataset.

    Creates (idempotently — never overwrites existing files):
    - <pbip_root>/<name>.Report/report.json
    - <pbip_root>/<name>.Report/theme.json
    - <pbip_root>/<name>.Report/pages/<page_name>/page.json

    The sample page has `visual_count` visuals alternating card (if
    include_card) and bar charts. Each visual references the first
    measure or table returned by the inspector (mock or modeling engine).
    """
    pbip_root = Path(pbip_path)
    if not pbip_root.exists():
        return {
            "success": False,
            "page_name": page_name,
            "files_created": [],
            "visual_ids": [],
            "warnings": [f"PBIP path does not exist: {pbip_path}"],
            "rollback_handle": None,
        }

    # Locate dataset.
    dataset_dirs = sorted(pbip_root.glob("*.Dataset"))
    if not dataset_dirs:
        return {
            "success": False,
            "page_name": page_name,
            "files_created": [],
            "visual_ids": [],
            "warnings": [
                f"PBIP has no .Dataset directory under {pbip_root}"
            ],
            "rollback_handle": None,
        }
    dataset_dir = dataset_dirs[0]
    report_name = dataset_dir.name.replace(".Dataset", "")
    report_dir = pbip_root / f"{report_name}.Report"

    warnings: list[str] = []
    files_created: list[str] = []

    if not report_dir.exists():
        report_dir.mkdir(parents=True)

    # 1. Write theme.json (use Okabe-Ito from apply_theme module).
    theme_data_colors: list[str] = list(OKABE_ITO_PALETTE.values())
    theme_dict: dict[str, Any] = {
        "name": f"{report_name}-{theme}",
        "dataColors": theme_data_colors,
        "background": "#FFFFFF",
        "foreground": "#252423",
        "tableAccent": "#118DFF",
        "good": "#339933",
        "neutral": "#DDDDDD",
        "bad": "#C00000",
        "maximum": theme_data_colors[0],
        "center": theme_data_colors[3] if len(theme_data_colors) > 3 else "#009E73",
        "minimum": theme_data_colors[1],
    }
    theme_path = report_dir / "theme.json"
    if not theme_path.exists():
        theme_path.write_text(json.dumps(theme_dict, indent=2), encoding="utf-8")
        files_created.append(str(theme_path.relative_to(pbip_root)))

    # 2. Write report.json (do NOT overwrite if exists).
    report_json = report_dir / "report.json"
    if report_json.exists():
        warnings.append("report.json already exists; not overwriting")
    else:
        report_json.write_text(
            json.dumps(
                {
                    "name": report_name,
                    "version": "1.0",
                    "theme": "theme.json",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        files_created.append(str(report_json.relative_to(pbip_root)))

    # 3. Write page.json.
    page_dir = report_dir / "pages" / page_name
    if page_dir.exists() and (page_dir / "page.json").exists():
        warnings.append(f"page {page_name!r} already exists; not overwriting")
    else:
        page_dir.mkdir(parents=True, exist_ok=True)

        # Pick first measure / table for visual references.
        measures: list[dict[str, Any]] = []
        first_table: str = ""
        if inspector is not None:
            try:
                measures = inspector.list_measures()
            except Exception:  # noqa: BLE001
                measures = []
            try:
                tables = inspector.list_tables()
                first_table = tables[0]["name"] if tables else ""
            except Exception:  # noqa: BLE001
                first_table = ""

        first_measure = (
            f"[{measures[0]['name']}]" if measures else f"[{first_table}]"
        )

        # Build visuals.
        visual_ids: list[str] = []
        containers: list[dict[str, Any]] = []
        visual_types_cycle = ["card", "barChart"]
        if not include_card:
            visual_types_cycle = ["barChart"]
        for i in range(max(1, min(visual_count, 4))):
            v_type = visual_types_cycle[i % len(visual_types_cycle)]
            vid = f"v_scaffold_{i}"
            visual_ids.append(vid)
            containers.append(
                {
                    "$type": "visualContainer",
                    "id": vid,
                    "x": 0 + i * 320,
                    "y": 0,
                    "width": 300,
                    "height": 200,
                    "altText": f"Scaffold {v_type} for {first_measure}",
                    "visual": {
                        "$type": v_type,
                        "id": vid,
                        "projections": {
                            "Values": [{"queryRef": first_measure}],
                        },
                    },
                    "tabOrder": i,
                }
            )

        page_data: dict[str, Any] = {
            "$type": "page",
            "name": page_name,
            "displayName": page_name,
            "displayOption": "FitToPage",
            "width": 1280,
            "height": 720,
            "visualContainers": containers,
        }
        (page_dir / "page.json").write_text(
            json.dumps(page_data, indent=2), encoding="utf-8"
        )
        files_created.append(str((page_dir / "page.json").relative_to(pbip_root)))

    return {
        "success": True,
        "page_name": page_name,
        "files_created": files_created,
        "visual_ids": visual_ids if inspector is not None else [],
        "warnings": warnings,
        "rollback_handle": None,
    }
