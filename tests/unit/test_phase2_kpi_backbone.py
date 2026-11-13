"""Phase 2 tests: define_kpi_catalog, ingest_source_table, compute_process_kpis."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from powerbi_orchestrator_mcp.orchestrator import server as mcp_server
from powerbi_orchestrator_mcp.orchestrator.authorization import (
    SCOPE_READ,
    SCOPE_WRITE,
    TOOL_REQUIRED_SCOPE,
    ToolAuthorizationError,
)
from powerbi_orchestrator_mcp.tools.compute_process_kpis import compute_process_kpis
from powerbi_orchestrator_mcp.tools.define_kpi_catalog import define_kpi_catalog
from powerbi_orchestrator_mcp.tools.ingest_source_table import ingest_source_table


def _write_ops_csv(path: Path) -> None:
    rows = [
        [
            "id",
            "rut_trabajador",
            "area",
            "fecha_inicio",
            "fecha_fin",
            "fecha_limite",
            "estado",
            "monto",
        ],
        [
            "1",
            "11.111.111-1",
            "Operaciones",
            "2026-03-01",
            "2026-03-05",
            "2026-03-01",
            "vencido",
            "1500.50",
        ],
        [
            "2",
            "22.222.222-2",
            "Operaciones",
            "2026-03-01",
            "2026-03-07",
            "2026-04-01",
            "cerrado",
            "2499.50",
        ],
        [
            "3",
            "33.333.333-3",
            "Compras",
            "2026-03-05",
            "2026-03-07",
            "2026-03-05",
            "pendiente",
            "1000.00",
        ],
        [
            "4",
            "44.444.444-4",
            "Compras",
            "2026-03-01",
            "2026-03-09",
            "2026-03-25",
            "firmada",
            "500.00",
        ],
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)


def _write_ops_xlsx(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Control"
    ws.append(["item_id", "nombre_empleado", "estado", "monto", "formula_maliciosa"])
    ws.append([1, "Ana Perez", "firmado", 1000, "=CMD|'/C calc'!A0"])
    ws.append([2, "Luis Soto", "pendiente", 2500.25, "+2+3"])
    wb.save(path)


async def test_define_kpi_catalog_dry_run_merge_and_mcp(tmp_path: Path) -> None:
    """Verify YAML KPI catalog dry_run, write, merge, and MCP tool registration."""
    catalog_path = tmp_path / "kpi-catalog.yaml"

    dry = await define_kpi_catalog(
        catalog_path=str(catalog_path),
        include_process_preset=True,
        approver="Jefe de Procesos",
        dry_run=True,
    )
    assert dry.dry_run is True
    assert dry.status == "dry_run"
    assert not catalog_path.exists()
    assert dry.kpi_count == 8
    assert "cumplimiento_rutinas" in dry.changed_ids

    live = await define_kpi_catalog(
        catalog_path=str(catalog_path),
        include_process_preset=True,
        approver="Jefe de Procesos",
        dry_run=False,
    )
    assert live.status == "ok"
    assert catalog_path.exists()
    assert "cumplimiento_rutinas" in catalog_path.read_text(encoding="utf-8")

    # Merge a custom KPI without dropping preset items
    merged = await mcp_server.define_kpi_catalog(
        catalog_path=str(catalog_path),
        kpis=json.dumps(
            [
                {
                    "id": "custom_monto_pendiente",
                    "name": "Monto Pendiente",
                    "area": "compras",
                    "unit": "CLP",
                    "formula_dax": "SUM(Control[monto])",
                    "python_rule": "sum_amount",
                    "rule_params": {"amount_column": "monto"},
                    "owner": "Admin Contrato",
                    "source_table": "control",
                    "periodicity": "weekly",
                }
            ]
        ),
        dry_run=False,
    )
    assert merged["kpi_count"] == 9
    assert TOOL_REQUIRED_SCOPE["define_kpi_catalog"] == SCOPE_WRITE


async def test_ingest_source_table_validation_staging_and_formula_escape(tmp_path: Path) -> None:
    """Verify Excel/CSV schema validation, duplicate key detection, and staged CSV escaping."""
    xlsx_path = tmp_path / "control.xlsx"
    _write_ops_xlsx(xlsx_path)
    pbip_dir = tmp_path / "OpsModel.pbip"

    res = await ingest_source_table(
        source_path=str(xlsx_path),
        target_pbip_dir=str(pbip_dir),
        table_name="Control",
        expected_schema={
            "item_id": "int",
            "nombre_empleado": "string",
            "estado": "string",
            "monto": "float",
        },
        sheet_name="Control",
        key_columns=["item_id"],
        dry_run=False,
    )

    assert res.status == "ok"
    assert res.row_count == 2
    assert res.columns_matched == ["item_id", "nombre_empleado", "estado", "monto"]
    assert res.unexpected_columns == ["formula_maliciosa"]
    staged_csv = pbip_dir / "data" / "Control.normalized.csv"
    assert staged_csv.exists()
    assert res.normalized_artifact_path == str(staged_csv)
    assert res.generated_m_expression is not None
    staged_text = staged_csv.read_text(encoding="utf-8")
    # Dangerous spreadsheet formula prefixes are neutralized with leading quote
    assert "'+2+3" in staged_text

    # Also test CSV '=' formula injection escaping
    csv_inj = tmp_path / "inj.csv"
    csv_inj.write_text("item_id,estado\n1,=CMD|'calc'!A0\n", encoding="utf-8")
    inj_res = await ingest_source_table(
        source_path=str(csv_inj),
        target_pbip_dir=str(pbip_dir),
        table_name="InjControl",
        expected_schema={"item_id": "int", "estado": "string"},
        key_columns=["item_id"],
        dry_run=False,
    )
    assert inj_res.status == "ok"
    assert inj_res.normalized_artifact_path is not None
    assert "'=CMD" in Path(inj_res.normalized_artifact_path).read_text(encoding="utf-8")

    # Negative: missing required column & duplicate keys trigger invalid status
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("id,estado\n1,ok\n1,pendiente\n", encoding="utf-8")
    bad_res = await ingest_source_table(
        source_path=str(bad_csv),
        target_pbip_dir=str(pbip_dir),
        table_name="BadControl",
        expected_schema={"id": "int", "estado": "string", "monto": "float"},
        key_columns=["id"],
        dry_run=True,
    )
    assert bad_res.status == "invalid"
    assert bad_res.missing_columns == ["monto"]
    assert bad_res.duplicate_keys_count == 1


async def test_compute_process_kpis_deterministic_pii_and_rls_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify deterministic KPI math, PII column stripping, and impersonation write gate."""
    csv_path = tmp_path / "ops.csv"
    _write_ops_csv(csv_path)

    custom_catalog = tmp_path / "kpis.yaml"
    cat_res = await define_kpi_catalog(
        catalog_path=str(custom_catalog),
        kpis=[
            {
                "id": "piezas_firmadas_pct",
                "name": "% Piezas Firmadas",
                "area": "operaciones",
                "unit": "%",
                "formula_dax": 'DIVIDE(COUNTROWS(FILTER(Control, Control[estado] IN {"firmada","cerrado"})), COUNTROWS(Control))',
                "python_rule": "ratio_pct",
                "rule_params": {"column": "estado", "equals": ["firmada", "cerrado"]},
                "owner": "Procesos",
                "source_table": "control",
                "periodicity": "daily",
            },
            {
                "id": "dias_promedio_etapa",
                "name": "Dias Promedio por Etapa",
                "area": "operaciones",
                "unit": "dias",
                "formula_dax": "AVERAGE(Control[dias_etapa])",
                "python_rule": "avg_days_between",
                "rule_params": {"start_column": "fecha_inicio", "end_column": "fecha_fin"},
                "owner": "Procesos",
                "source_table": "control",
                "periodicity": "daily",
            },
            {
                "id": "pendientes_vencidos",
                "name": "Pendientes Vencidos",
                "area": "operaciones",
                "unit": "casos",
                "formula_dax": "COUNTROWS(FILTER(Control, Control[fecha_limite] < TODAY()))",
                "python_rule": "overdue_count",
                "rule_params": {
                    "due_date_column": "fecha_limite",
                    "status_column": "estado",
                    "closed_values": ["cerrado", "firmada"],
                },
                "owner": "Procesos",
                "source_table": "control",
                "periodicity": "daily",
            },
            {
                "id": "montos_totales",
                "name": "Monto Total",
                "area": "finanzas",
                "unit": "CLP",
                "formula_dax": "SUM(Control[monto])",
                "python_rule": "sum_amount",
                "rule_params": {"amount_column": "monto"},
                "owner": "Admin",
                "source_table": "control",
                "periodicity": "monthly",
            },
        ],
        dry_run=False,
    )
    assert cat_res.status == "ok"

    result = await compute_process_kpis(
        source_path=str(csv_path),
        catalog_path=str(custom_catalog),
        as_of_date="2026-03-10",
    )
    by_id = {k.kpi_id: k.value for k in result.kpis}
    assert by_id["piezas_firmadas_pct"] == 50.0  # 2 of 4 rows
    assert by_id["dias_promedio_etapa"] == 5.0  # (4+6+2+8)/4
    assert by_id["pendientes_vencidos"] == 2.0  # rows 1 & 3 (< 2026-03-10 and open)
    assert by_id["montos_totales"] == 5500.0
    assert any("stripped 1 PII column(s)" in w for w in result.warnings)

    # RLS impersonation requires write scope on MCP wrapper
    monkeypatch.setattr(
        "powerbi_orchestrator_mcp.orchestrator.authorization._granted_scopes",
        lambda: {SCOPE_READ},
    )
    with pytest.raises(ToolAuthorizationError):
        await mcp_server.compute_process_kpis(
            workspace_id="ws-1",
            dataset_id="ds-1",
            impersonated_user_name="mandante@cliente.cl",
            fabric_client=object(),
        )

    # Also verify DAX fallback execution path via mock FabricClient
    class _DaxStub:
        async def execute_queries(
            self,
            workspace_id: str,
            dataset_id: str,
            queries: list[dict[str, Any]],
            *,
            impersonated_user_name: str | None = None,
        ) -> dict[str, Any]:
            del workspace_id, dataset_id, queries, impersonated_user_name
            return {"results": [{"tables": [{"rows": [{"Control[monto]": 42.5}]}]}]}

    dax_res = await compute_process_kpis(
        workspace_id="ws-1",
        dataset_id="ds-1",
        catalog_path=str(custom_catalog),
        kpi_ids=["montos_totales"],
        fabric_client=_DaxStub(),
    )
    assert dax_res.kpis[0].value == 42.5
