"""End-to-end integration test chaining the 7 process-KPI & reporting tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from powerbi_orchestrator_mcp.orchestrator import server as mcp_server
from powerbi_orchestrator_mcp.tools.check_report_data_consistency import (
    check_report_data_consistency,
)
from powerbi_orchestrator_mcp.tools.send_report_email import send_report_email


async def test_chained_process_kpi_reporting_pipeline_e2e(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Chain catalog -> ingest -> compute -> draft -> export -> consistency -> email."""
    pbip_dir = tmp_path / "OpsControl.pbip"
    pbip_dir.mkdir()
    catalog_path = tmp_path / "kpi-catalog.yaml"
    source_xlsx = tmp_path / "control_diario.xlsx"
    bundle_dir = tmp_path / "salida_reporte"

    # Raw workbook simulates operational input with PII column, CLP format, and formula attempt
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Control"
    ws.append(
        [
            "id_item",
            "rut_trabajador",
            "estado",
            "fecha_inicio",
            "fecha_fin",
            "fecha_compromiso",
            "monto",
            "observacion",
        ]
    )
    ws.append(
        [1, "11.111.111-1", "Firmado", "01/03/2026", "04/03/2026", "2026-03-05", 1000.0, "ok"]
    )
    ws.append(
        [2, "22.222.222-2", "Cerrado", "02/03/2026", "06/03/2026", "2026-03-10", 2500.0, "ok"]
    )
    ws.append(
        [3, "33.333.333-3", "Pendiente", "01/03/2026", "", "2026-03-05", 500.0, "+CMD|'/C calc'!A0"]
    )
    wb.save(source_xlsx)

    # Step 1: define versioned KPI catalog on disk via MCP tool wrapper
    cat_res = await mcp_server.define_kpi_catalog(
        catalog_path=str(catalog_path),
        include_process_preset=False,
        approver="Jefatura de Procesos",
        kpis=[
            {
                "id": "pct_firmadas",
                "name": "% Piezas Firmadas",
                "area": "Operaciones",
                "unit": "%",
                "formula_dax": 'DIVIDE(COUNTROWS(FILTER(Control, Control[estado] IN {"Firmado","Cerrado"})), COUNTROWS(Control)) * 100',
                "python_rule": "ratio_pct",
                "rule_params": {"column": "estado", "equals": ["Firmado", "Cerrado"]},
                "owner": "Procesos",
                "source_table": "control",
                "periodicity": "daily",
            },
            {
                "id": "pendientes_vencidos",
                "name": "Pendientes Vencidos",
                "area": "Operaciones",
                "unit": "casos",
                "formula_dax": 'COUNTROWS(FILTER(Control, Control[estado] = "Pendiente"))',
                "python_rule": "overdue_count",
                "rule_params": {
                    "due_date_column": "fecha_compromiso",
                    "status_column": "estado",
                    "closed_values": ["Firmado", "Cerrado"],
                },
                "owner": "Procesos",
                "source_table": "control",
                "periodicity": "daily",
            },
            {
                "id": "monto_controlado",
                "name": "Monto Controlado",
                "area": "Finanzas",
                "unit": "CLP",
                "formula_dax": "SUM(Control[monto])",
                "python_rule": "sum_amount",
                "rule_params": {"amount_column": "monto"},
                "owner": "Admin Contrato",
                "source_table": "control",
                "periodicity": "daily",
            },
        ],
        dry_run=False,
    )
    assert cat_res["status"] == "ok"
    assert catalog_path.exists()

    # Step 2: ingest Excel into normalized CSV + Power Query M artifact inside PBIP dir
    ingest_res = await mcp_server.ingest_source_table(
        source_path=str(source_xlsx),
        sheet_name="Control",
        target_pbip_dir=str(pbip_dir),
        table_name="Control",
        expected_schema={
            "id_item": "int",
            "estado": "string",
            "fecha_inicio": "string",
            "fecha_fin": "string",
            "fecha_compromiso": "string",
            "monto": "float",
            "observacion": "string",
        },
        key_columns=["id_item"],
        dry_run=False,
    )
    assert ingest_res["status"] == "ok"
    normalized_csv = Path(ingest_res["normalized_artifact_path"])
    assert normalized_csv.exists()
    assert "'+CMD" in normalized_csv.read_text(encoding="utf-8")

    # Step 3: deterministically compute process KPIs from the staged normalized CSV
    kpis_res = await mcp_server.compute_process_kpis(
        source_path=str(normalized_csv),
        catalog_path=str(catalog_path),
        as_of_date="2026-03-10",
    )
    assert kpis_res["row_count_evaluated"] == 3
    by_id = {k["kpi_id"]: k["value"] for k in kpis_res["kpis"]}
    assert by_id["pct_firmadas"] == 66.67
    assert by_id["pendientes_vencidos"] == 1.0
    assert by_id["monto_controlado"] == 4000.0
    assert any("stripped 1 PII" in w for w in kpis_res["warnings"])

    # Step 4: verify LLM narrative strictly matches deterministic figures
    draft_res = await mcp_server.draft_report_summary(
        computed_kpis=kpis_res,
        audience="contract_admin",
        cadence="daily",
        candidate_narrative=(
            "Al 2026-03-10 se evaluaron 3 registros: 66.67% firmadas, "
            "1 pendientes vencidos y monto controlado de $4,000 CLP."
        ),
    )
    assert draft_res["numbers_verified"] is True
    assert draft_res["unverified_numbers"] == []
    assert draft_res["missing_required_kpis"] == []

    # Step 5: export Excel + PDF executive bundle from computed KPIs + sanitized detail CSV
    bundle_res = await mcp_server.export_kpi_report_bundle(
        computed_kpis=kpis_res,
        output_dir=str(bundle_dir),
        report_title="Control Diario Faena",
        summary_text=draft_res["verified_narrative"],
        detail_rows_path=str(normalized_csv),
        dry_run=False,
    )
    assert bundle_res["status"] == "ok"
    assert bundle_res["stripped_pii_columns"] == ["rut_trabajador"]
    exported_paths = [item["path"] for item in bundle_res["files"]]
    assert len(exported_paths) == 2
    assert any(p.endswith(".xlsx") and Path(p).exists() for p in exported_paths)
    assert any(
        p.endswith(".pdf") and Path(p).read_bytes().startswith(b"%PDF-") for p in exported_paths
    )

    # Step 6: reconcile staged source KPIs against simulated published dataset values
    class _ConsistentFabricStub:
        async def get_refresh_history(
            self, _ws: str, _ds: str, *, top: int = 1
        ) -> list[dict[str, Any]]:
            del top
            return [{"status": "Completed", "endTime": "2026-03-10T07:30:00Z"}]

        async def execute_queries(
            self, _ws: str, _ds: str, *, queries: list[dict[str, Any]]
        ) -> dict[str, Any]:
            q = queries[0]["query"]
            if "DIVIDE" in q:
                val = 66.67
            elif "Pendiente" in q:
                val = 1.0
            else:
                val = 4000.0
            return {"results": [{"tables": [{"rows": [{"[val]": val}]}]}]}

    consistency_ok = await check_report_data_consistency(
        workspace_id="ws-ops",
        dataset_id="ds-control",
        source_path=str(normalized_csv),
        catalog_path=str(catalog_path),
        as_of_date="2026-03-10",
        fabric_client=_ConsistentFabricStub(),
    )
    assert consistency_ok.consistent is True
    assert consistency_ok.checks_run == 3
    assert consistency_ok.dataset_last_refresh == "2026-03-10T07:30:00Z"

    # Step 7: prepare & guard email delivery with real .xlsx/.pdf attachments (dry_run + redaction)
    monkeypatch.setenv("PBI_EMAIL_ALLOWED_DOMAINS", "quadlogic.cl")
    monkeypatch.setenv("PBI_INTERNAL_EMAIL_DOMAINS", "quadlogic.cl")
    email_dry = await send_report_email(
        recipients_to=["jefatura@quadlogic.cl"],
        subject="Reporte Control Diario 2026-03-10",
        body_markdown=str(draft_res["verified_narrative"]),
        attachment_paths=exported_paths,
        audience_profile="internal_admin",
        report_cadence="daily",
        dry_run=True,
    )
    assert email_dry.status == "dry_run"
    assert len(email_dry.attachment_hashes) == 2
    assert "jefatura@quadlogic.cl" not in json.dumps(email_dry.model_dump(mode="json"))
