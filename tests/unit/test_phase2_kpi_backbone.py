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


async def test_define_kpi_catalog_validation_edges(tmp_path: Path) -> None:
    """Verify catalog validator catches bad ids, duplicates, empty formulas, and corrupt YAML."""
    cat_path = tmp_path / "invalid-kpis.yaml"

    empty_res = await define_kpi_catalog(
        catalog_path=str(cat_path),
        kpis=[],
        include_process_preset=False,
        dry_run=True,
    )
    assert empty_res.status == "invalid"
    assert any("at least one KPI" in err for err in empty_res.validation_errors)

    bad_kpis = await define_kpi_catalog(
        catalog_path=str(cat_path),
        kpis=[
            {
                "id": "1BadId",
                "name": "Bad ID & Empty DAX",
                "area": "ops",
                "formula_dax": "   ",
                "python_rule": "count_where",
                "owner": "QA",
                "source_table": "control",
                "periodicity": "daily",
            },
            {
                "id": "bad_rule_kpi",
                "name": "Pydantic literal failure",
                "area": "ops",
                "formula_dax": "COUNTROWS(Control)",
                "python_rule": "unknown_rule",
                "owner": "QA",
                "source_table": "control",
                "periodicity": "daily",
            },
        ],
        merge=False,
        dry_run=True,
    )
    assert bad_kpis.status == "invalid"
    errs_joined = "\n".join(bad_kpis.validation_errors)
    assert "invalid kpi id" in errs_joined
    assert "invalid kpi definition" in errs_joined
    assert "empty formula_dax" in errs_joined

    # Direct defensive validation check for duplicate ID & unsupported rule in _validate_kpis
    from powerbi_orchestrator_mcp.tools.define_kpi_catalog import KpiDefinition, _validate_kpis

    dup_item = KpiDefinition.model_construct(
        id="dup_kpi",
        name="Dup",
        area="ops",
        formula_dax="COUNTROWS(Control)",
        python_rule="not_a_rule",
        owner="QA",
        source_table="control",
        periodicity="daily",
    )
    direct_errs = _validate_kpis([dup_item, dup_item])
    assert any("duplicate kpi id" in e for e in direct_errs)
    assert any("unsupported python_rule" in e for e in direct_errs)

    # Corrupt existing YAML file during merge is reported cleanly
    cat_path.write_text("kpis: [not: {valid\n", encoding="utf-8")
    corrupt_res = await define_kpi_catalog(
        catalog_path=str(cat_path),
        include_process_preset=True,
        merge=True,
        dry_run=True,
    )
    assert corrupt_res.status == "invalid"
    assert any("failed reading existing catalog" in err for err in corrupt_res.validation_errors)


async def test_ingest_source_table_types_null_keys_and_tabular_io_edges(tmp_path: Path) -> None:
    """Exercise bool/int/float/date type checking, strict extra-columns, and _tabular_io."""
    from powerbi_orchestrator_mcp.tools._tabular_io import (
        escape_excel_formula,
        load_tabular_file,
        strip_pii_columns,
    )

    pbip_dir = tmp_path / "StrictModel.pbip"

    # Invalid table_name rejected immediately
    bad_name = await ingest_source_table(
        source_path=str(tmp_path / "unused.csv"),
        target_pbip_dir=str(pbip_dir),
        table_name="99-bad!",
        expected_schema={"id": "int"},
    )
    assert bad_name.status == "invalid"
    assert any("invalid table_name" in w for w in bad_name.warnings)

    # Mixed type mismatches, null key, strict extra columns, valid Chilean dates & bools
    csv_mixed = tmp_path / "mixed.csv"
    csv_mixed.write_text(
        "id,activo,entero,monto,fecha,extra\n"
        "1,si,10.0,1500.50,15/03/2026,allowed_not\n"
        ",no_valido,abc,not_float,fecha_mala,x\n"
        "2,false,true,12,01-04-2026,z\n",
        encoding="utf-8",
    )
    bad_types = await ingest_source_table(
        source_path=str(csv_mixed),
        target_pbip_dir=str(pbip_dir),
        table_name="StrictControl",
        expected_schema={
            "id": "int",
            "activo": "bool",
            "entero": "int",
            "monto": "float",
            "fecha": "date",
        },
        key_columns=["id"],
        allow_extra_columns=False,
        dry_run=True,
    )
    assert bad_types.status == "invalid"
    assert bad_types.null_key_rows == 1
    assert bad_types.type_mismatch_count >= 3
    assert "extra" in bad_types.unexpected_columns

    missing_key_res = await ingest_source_table(
        source_path=str(csv_mixed),
        target_pbip_dir=str(pbip_dir),
        table_name="MissingKeyControl",
        expected_schema={"id": "int"},
        key_columns=["id", "missing_key"],
        dry_run=True,
    )
    assert "missing_key" in missing_key_res.missing_columns

    # Clean Excel ingestion with dry_run=True then dry_run=False
    import datetime as dt

    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Datos"
    ws.append(["id", "activo", "entero", "monto", "fecha"])
    ws.append([1, True, 5, 100.25, dt.date(2026, 3, 1)])
    ws.append([2, "sí", 8.0, "99,5", "2026/03/02"])
    wb_path = tmp_path / "clean.xlsx"
    wb.save(wb_path)

    dry_ok = await ingest_source_table(
        source_path=str(wb_path),
        sheet_name="Datos",
        target_pbip_dir=str(pbip_dir),
        table_name="CleanControl",
        expected_schema={
            "id": "int",
            "activo": "bool",
            "entero": "int",
            "monto": "float",
            "fecha": "date",
        },
        key_columns=["id"],
        dry_run=True,
    )
    assert dry_ok.status == "dry_run"
    assert dry_ok.normalized_artifact_path is not None
    assert not (pbip_dir / "data").exists()

    # _tabular_io edges: missing file, unsupported suffix, wrong sheet, truncation, masking
    with pytest.raises(FileNotFoundError):
        load_tabular_file(str(tmp_path / "does_not_exist.csv"))
    bad_ext = tmp_path / "bad.json"
    bad_ext.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported tabular extension"):
        load_tabular_file(str(bad_ext))
    with pytest.raises(ValueError, match="not found"):
        load_tabular_file(str(wb_path), sheet_name="NoSuchSheet")

    truncated = load_tabular_file(str(wb_path), sheet_name="Datos", max_rows=1)
    assert truncated.truncated is True
    assert len(truncated.rows) == 1

    # Empty worksheet yields empty headers/rows cleanly
    empty_wb = openpyxl.Workbook()
    empty_xlsx = tmp_path / "empty.xlsx"
    empty_wb.save(empty_xlsx)
    empty_tab = load_tabular_file(str(empty_xlsx))
    assert empty_tab.headers == []
    assert empty_tab.rows == []

    # Masking PII preserves header & replaces values
    masked_h, masked_rows, pii = strip_pii_columns(
        ["id", "correo"], [{"id": 1, "correo": "a@b.cl"}], mask_value="***"
    )
    assert pii == ["correo"]
    assert masked_h == ["id", "correo"]
    assert masked_rows[0]["correo"] == "***"
    assert escape_excel_formula("-12.5") == "-12.5"
    assert escape_excel_formula("-evil") == "'-evil"


async def test_compute_process_kpis_all_rules_formats_and_thresholds(tmp_path: Path) -> None:
    """Exercise p95_days_between, ratio_pct num/den, count_where, CLP/date parsing, and filters."""
    csv_path = tmp_path / "chile_ops.csv"
    csv_path.write_text(
        "area,fecha_inicio,fecha_fin,fecha_compromiso,estado,planificado,ejecutado,monto\n"
        'Operaciones,01/03/2026,03/03/2026,05-03-2026,Firmado,10,8,"$ 1.250,50"\n'
        'Operaciones,01-03-2026,,02/03/2026,Pendiente,10,12,"1,500.50"\n'
        'Compras,2026/03/02,2026-03-10,2026-03-15,Aprobado,20,20,"1,000,000"\n',
        encoding="utf-8",
    )
    cat_path = tmp_path / "full_catalog.yaml"
    await define_kpi_catalog(
        catalog_path=str(cat_path),
        kpis=[
            {
                "id": "cumplimiento_ratio",
                "name": "Cumplimiento Plan",
                "area": "Operaciones",
                "unit": "%",
                "formula_dax": "DIVIDE(SUM(Control[ejecutado]), SUM(Control[planificado]))*100",
                "python_rule": "ratio_pct",
                "rule_params": {
                    "numerator_column": "ejecutado",
                    "denominator_column": "planificado",
                },
                "owner": "Procesos",
                "source_table": "control",
                "periodicity": "weekly",
                "target_threshold": 110.0,
                "threshold_direction": "min",
            },
            {
                "id": "ciclo_p95",
                "name": "P95 Ciclo",
                "area": "Operaciones",
                "unit": "dias",
                "formula_dax": "PERCENTILE.INC(Control[dias], 0.95)",
                "python_rule": "p95_days_between",
                "rule_params": {"start_column": "fecha_inicio", "end_column": "fecha_fin"},
                "owner": "Procesos",
                "source_table": "control",
                "periodicity": "weekly",
                "target_threshold": 5.0,
                "threshold_direction": "max",
            },
            {
                "id": "piezas_firmadas",
                "name": "Piezas Firmadas",
                "area": "Operaciones",
                "unit": "docs",
                "formula_dax": "COUNTROWS(Control)",
                "python_rule": "count_where",
                "rule_params": {"column": "estado", "not_null": True},
                "owner": "Doc",
                "source_table": "control",
                "periodicity": "daily",
            },
            {
                "id": "total_clp",
                "name": "Monto CLP",
                "area": "Finanzas",
                "unit": "CLP",
                "formula_dax": "SUM(Control[monto])",
                "python_rule": "sum_amount",
                "rule_params": {"amount_column": "monto"},
                "owner": "Admin",
                "source_table": "control",
                "periodicity": "monthly",
            },
            {
                "id": "col_inexistente",
                "name": "Columna Faltante",
                "area": "Operaciones",
                "unit": "docs",
                "formula_dax": "COUNTROWS(Control)",
                "python_rule": "overdue_count",
                "rule_params": {"due_date_column": "no_existe"},
                "owner": "Doc",
                "source_table": "control",
                "periodicity": "daily",
            },
        ],
        dry_run=False,
    )

    all_res = await compute_process_kpis(
        source_path=str(csv_path),
        catalog_path=str(cat_path),
        as_of_date="2026-03-10",
    )
    by_id = {k.kpi_id: k for k in all_res.kpis}
    # 40 / 40 = 100.0% < target 110 min -> breach
    assert by_id["cumplimiento_ratio"].value == 100.0
    assert by_id["cumplimiento_ratio"].status == "breach"
    assert by_id["cumplimiento_ratio"].formatted_value == "100.00%"
    # deltas: 2 days, 9 days (open row uses as_of=2026-03-10), 8 days -> sorted [2,8,9] -> p95=9 > max 5
    assert by_id["ciclo_p95"].value == 9.0
    assert by_id["ciclo_p95"].status == "breach"
    assert by_id["piezas_firmadas"].value == 3.0
    assert by_id["total_clp"].value == 1002751.0
    assert "CLP" in by_id["total_clp"].formatted_value
    assert by_id["col_inexistente"].status == "missing_data"

    # Area filter + empty args validation
    filtered = await compute_process_kpis(
        source_path=str(csv_path),
        catalog_path=str(cat_path),
        area_filter="finanzas",
        as_of_date="2026-03-10",
    )
    assert [k.kpi_id for k in filtered.kpis] == ["total_clp"]

    no_src = await compute_process_kpis()
    assert no_src.kpis == []
    assert any("provide source_path" in w for w in no_src.warnings)

    # Exercise zero denominator, missing columns for ratio/avg/count_where/sum, and filter
    zero_csv = tmp_path / "zero.csv"
    zero_csv.write_text("ejecutado,planificado,estado\n5,0,ok\n", encoding="utf-8")
    edge_cat = tmp_path / "edge_cat.yaml"
    await define_kpi_catalog(
        catalog_path=str(edge_cat),
        kpis=[
            {
                "id": "div_zero",
                "name": "Div Zero",
                "area": "ops",
                "unit": "%",
                "formula_dax": "0",
                "python_rule": "ratio_pct",
                "rule_params": {
                    "numerator_column": "ejecutado",
                    "denominator_column": "planificado",
                },
                "owner": "QA",
                "source_table": "control",
                "periodicity": "daily",
            },
            {
                "id": "miss_num",
                "name": "Missing Num",
                "area": "ops",
                "unit": "%",
                "formula_dax": "0",
                "python_rule": "ratio_pct",
                "rule_params": {"numerator_column": "nope", "denominator_column": "planificado"},
                "owner": "QA",
                "source_table": "control",
                "periodicity": "daily",
            },
            {
                "id": "miss_avg",
                "name": "Missing Avg",
                "area": "ops",
                "unit": "dias",
                "formula_dax": "0",
                "python_rule": "avg_days_between",
                "rule_params": {"start_column": "nope", "end_column": "also_nope"},
                "owner": "QA",
                "source_table": "control",
                "periodicity": "daily",
            },
            {
                "id": "miss_cnt",
                "name": "Missing Count",
                "area": "ops",
                "unit": "docs",
                "formula_dax": "0",
                "python_rule": "count_where",
                "rule_params": {"column": "nope", "not_null": True},
                "owner": "QA",
                "source_table": "control",
                "periodicity": "daily",
            },
            {
                "id": "miss_sum",
                "name": "Missing Sum",
                "area": "ops",
                "unit": "CLP",
                "formula_dax": "0",
                "python_rule": "sum_amount",
                "rule_params": {"amount_column": "nope"},
                "owner": "QA",
                "source_table": "control",
                "periodicity": "daily",
            },
        ],
        dry_run=False,
    )
    edge_res = await compute_process_kpis(source_path=str(zero_csv), catalog_path=str(edge_cat))
    assert all(k.status == "missing_data" for k in edge_res.kpis)
