"""export_kpi_report_bundle tool — deterministic Excel (.xlsx) + PDF report generator.

Replaces wireframe placeholders with real, management-ready Excel workbooks
(via ``openpyxl``) and clean printable PDFs (via ``reportlab``) generated
from ``compute_process_kpis`` results at zero cloud capacity cost. Strips or
masks PII columns by default before writing attachments.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.tools._tabular_io import (
    MAX_TABULAR_ROWS_DEFAULT,
    compute_file_sha256,
    escape_excel_formula,
    load_tabular_file,
    strip_pii_columns,
)
from powerbi_orchestrator_mcp.tools.compute_process_kpis import (
    ComputedKpiValue,
    ComputeProcessKpisResult,
)
from powerbi_orchestrator_mcp.validation.path_safety import validate_write_path


class ExportedArtifact(BaseModel):
    """Metadata for a single generated report file."""

    format: Literal["xlsx", "pdf"]
    path: str
    bytes: int
    sha256: str


def _default_formats() -> list[Literal["xlsx", "pdf"]]:
    return ["xlsx", "pdf"]


class ExportKpiReportBundleInput(BaseModel):
    """Input schema for ``export_kpi_report_bundle``."""

    computed_kpis: ComputeProcessKpisResult
    output_dir: str
    report_title: str = "Reporte de Control y KPIs"
    summary_text: str | None = None
    detail_rows_path: str | None = None
    detail_sheet_name: str | None = None
    formats: list[Literal["xlsx", "pdf"]] = Field(default_factory=_default_formats)
    include_pii_columns: bool = False
    dry_run: bool = True


class ExportKpiReportBundleResult(BaseModel):
    """Output schema for ``export_kpi_report_bundle``."""

    status: Literal["ok", "dry_run"]
    dry_run: bool
    files: list[ExportedArtifact] = Field(default_factory=list)
    row_counts: dict[str, int] = Field(default_factory=dict)
    sha256_by_file: dict[str, str] = Field(default_factory=dict)
    stripped_pii_columns: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _slug(title: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", title.strip().lower()).strip("_")
    return cleaned[:60] or "kpi_report"


def _write_xlsx(
    path: Path,
    *,
    report_title: str,
    computed_kpis: ComputeProcessKpisResult,
    summary_text: str | None,
    detail_headers: list[str],
    detail_rows: list[dict[str, Any]],
) -> None:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = openpyxl.Workbook()
    ws_summary = wb.active
    assert ws_summary is not None
    ws_summary.title = "Resumen"

    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    title_font = Font(size=14, bold=True)

    ws_summary["A1"] = escape_excel_formula(report_title)
    ws_summary["A1"].font = title_font
    ws_summary["A2"] = "Fecha corte"
    ws_summary["B2"] = escape_excel_formula(computed_kpis.as_of_date)
    ws_summary["A3"] = "Filas evaluadas"
    ws_summary["B3"] = computed_kpis.row_count_evaluated
    ws_summary["A4"] = "Hash fuente (SHA-256)"
    ws_summary["B4"] = computed_kpis.source_hash_sha256

    ws_summary["A6"] = "Resumen Ejecutivo"
    ws_summary["A6"].font = Font(bold=True)
    ws_summary["A7"] = escape_excel_formula(summary_text or "Sin narrativa ejecutiva adjunta.")
    ws_summary["A7"].alignment = Alignment(wrap_text=True, vertical="top")
    ws_summary.column_dimensions["A"].width = 28
    ws_summary.column_dimensions["B"].width = 72

    ws_kpis = wb.create_sheet("KPIs")
    kpi_headers = [
        "KPI ID",
        "Indicador",
        "Área",
        "Valor",
        "Formateado",
        "Unidad",
        "Umbral",
        "Estado",
        "Fórmula",
    ]
    ws_kpis.append(kpi_headers)
    for col_idx in range(1, len(kpi_headers) + 1):
        cell = ws_kpis.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font

    for k in computed_kpis.kpis:
        ws_kpis.append(
            [
                escape_excel_formula(k.kpi_id),
                escape_excel_formula(k.name),
                escape_excel_formula(k.area),
                k.value,
                escape_excel_formula(k.formatted_value),
                escape_excel_formula(k.unit),
                k.target_threshold,
                escape_excel_formula(k.status),
                escape_excel_formula(k.formula_used),
            ]
        )
    for col_letter in ("A", "B", "C", "D", "E", "F", "G", "H", "I"):
        ws_kpis.column_dimensions[col_letter].width = 20

    if detail_headers:
        ws_detail = wb.create_sheet("Detalle")
        ws_detail.append([escape_excel_formula(h) for h in detail_headers])
        for col_idx in range(1, len(detail_headers) + 1):
            cell = ws_detail.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
        for row in detail_rows:
            ws_detail.append([escape_excel_formula(row.get(h, "")) for h in detail_headers])

    wb.save(path)


def _write_pdf(
    path: Path,
    *,
    report_title: str,
    computed_kpis: ComputeProcessKpisResult,
    summary_text: str | None,
) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    styles = getSampleStyleSheet()
    story: list[Any] = []

    story.append(Paragraph(_xml_escape(report_title), styles["Title"]))
    meta = (
        f"Fecha corte: {_xml_escape(computed_kpis.as_of_date)} | "
        f"Filas evaluadas: {computed_kpis.row_count_evaluated} | "
        f"Generado UTC: {dt.datetime.now(dt.UTC).strftime('%Y-%m-%d %H:%M')}"
    )
    story.append(Paragraph(meta, styles["Normal"]))
    story.append(Spacer(1, 12))

    if summary_text:
        story.append(Paragraph("Resumen Ejecutivo", styles["Heading2"]))
        for line in summary_text.splitlines():
            clean = line.strip()
            if clean:
                story.append(Paragraph(_xml_escape(clean), styles["Normal"]))
        story.append(Spacer(1, 12))

    story.append(Paragraph("Indicadores Deterministas", styles["Heading2"]))
    table_data: list[list[str]] = [["Indicador", "Área", "Valor", "Umbral", "Estado"]]
    for k in computed_kpis.kpis:
        thresh = "-" if k.target_threshold is None else str(k.target_threshold)
        table_data.append(
            [
                k.name,
                k.area,
                k.formatted_value,
                thresh,
                k.status.upper(),
            ]
        )

    table = Table(table_data, repeatRows=1, colWidths=[165, 95, 95, 65, 80])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E78")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7FA")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("PADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 14))
    sha_short = (computed_kpis.source_hash_sha256 or "n/a")[:16]
    story.append(
        Paragraph(
            f"Trazabilidad fuente SHA-256: {_xml_escape(sha_short)}...",
            styles["Italic"],
        )
    )
    doc.build(story)


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def export_kpi_report_bundle(
    computed_kpis: ComputeProcessKpisResult | dict[str, Any],
    output_dir: str,
    report_title: str = "Reporte de Control y KPIs",
    summary_text: str | None = None,
    detail_rows_path: str | None = None,
    detail_sheet_name: str | None = None,
    formats: list[Literal["xlsx", "pdf"]] | None = None,
    include_pii_columns: bool = False,
    dry_run: bool = True,
) -> ExportKpiReportBundleResult:
    """Render deterministic KPI results into printable PDF and styled Excel files."""
    kpis_obj = (
        computed_kpis
        if isinstance(computed_kpis, ComputeProcessKpisResult)
        else ComputeProcessKpisResult.model_validate(computed_kpis)
    )
    out_dir = validate_write_path(output_dir, must_exist=False)
    selected_formats: list[Literal["xlsx", "pdf"]] = list(formats or ["xlsx", "pdf"])

    detail_headers: list[str] = []
    detail_rows: list[dict[str, Any]] = []
    stripped_pii: list[str] = []
    warnings: list[str] = []

    if detail_rows_path:
        tabular = load_tabular_file(
            detail_rows_path,
            sheet_name=detail_sheet_name,
            max_rows=MAX_TABULAR_ROWS_DEFAULT,
        )
        warnings.extend(tabular.warnings)
        if include_pii_columns:
            detail_headers, detail_rows = tabular.headers, tabular.rows
        else:
            detail_headers, detail_rows, stripped_pii = strip_pii_columns(
                tabular.headers,
                tabular.rows,
            )
            if stripped_pii:
                warnings.append(
                    f"stripped PII columns from detail sheet: {', '.join(stripped_pii)}"
                )

    base = f"{_slug(report_title)}_{kpis_obj.as_of_date}"
    row_counts = {"kpis": len(kpis_obj.kpis), "detail_rows": len(detail_rows)}

    if dry_run:
        planned_files = [
            ExportedArtifact(
                format=fmt,
                path=str(out_dir / f"{base}.{fmt}"),
                bytes=0,
                sha256="dry_run",
            )
            for fmt in selected_formats
        ]
        return ExportKpiReportBundleResult(
            status="dry_run",
            dry_run=True,
            files=planned_files,
            row_counts=row_counts,
            stripped_pii_columns=stripped_pii,
            warnings=warnings,
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[ExportedArtifact] = []
    sha_map: dict[str, str] = {}

    for fmt in selected_formats:
        target_path = out_dir / f"{base}.{fmt}"
        if fmt == "xlsx":
            _write_xlsx(
                target_path,
                report_title=report_title,
                computed_kpis=kpis_obj,
                summary_text=summary_text,
                detail_headers=detail_headers,
                detail_rows=detail_rows,
            )
        else:
            _write_pdf(
                target_path,
                report_title=report_title,
                computed_kpis=kpis_obj,
                summary_text=summary_text,
            )
        digest = compute_file_sha256(target_path)
        size = target_path.stat().st_size
        artifacts.append(
            ExportedArtifact(
                format=fmt,
                path=str(target_path),
                bytes=size,
                sha256=digest,
            )
        )
        sha_map[str(target_path)] = digest

    return ExportKpiReportBundleResult(
        status="ok",
        dry_run=False,
        files=artifacts,
        row_counts=row_counts,
        sha256_by_file=sha_map,
        stripped_pii_columns=stripped_pii,
        warnings=warnings,
    )


__all__ = [
    "ComputedKpiValue",
    "ExportKpiReportBundleInput",
    "ExportKpiReportBundleResult",
    "ExportedArtifact",
    "export_kpi_report_bundle",
]
