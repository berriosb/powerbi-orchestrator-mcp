"""Phase 3 tests: export_kpi_report_bundle, draft_report_summary, send_report_email, check_report_data_consistency."""

from __future__ import annotations

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
    required_scope_for_tool,
)
from powerbi_orchestrator_mcp.tools.check_report_data_consistency import (
    check_report_data_consistency,
)
from powerbi_orchestrator_mcp.tools.compute_process_kpis import (
    ComputedKpiValue,
    ComputeProcessKpisResult,
)
from powerbi_orchestrator_mcp.tools.define_kpi_catalog import define_kpi_catalog
from powerbi_orchestrator_mcp.tools.draft_report_summary import draft_report_summary
from powerbi_orchestrator_mcp.tools.export_kpi_report_bundle import export_kpi_report_bundle
from powerbi_orchestrator_mcp.tools.send_report_email import send_report_email


def _sample_kpis() -> ComputeProcessKpisResult:
    return ComputeProcessKpisResult(
        as_of_date="2026-03-10",
        source_hash_sha256="0" * 64,
        row_count_evaluated=43,
        kpis=[
            ComputedKpiValue(
                kpi_id="piezas_firmadas",
                name="Piezas Firmadas",
                area="operaciones",
                value=43.0,
                formatted_value="43",
                unit="count",
                formula_used="count(firmada)",
                status="ok",
            ),
            ComputedKpiValue(
                kpi_id="pendientes_vencidos",
                name="Pendientes Vencidos",
                area="operaciones",
                value=5.0,
                formatted_value="5",
                unit="count",
                formula_used="overdue_count",
                status="ok",
            ),
            ComputedKpiValue(
                kpi_id="montos_por_cobrar",
                name="Montos por Cobrar",
                area="finanzas",
                value=1250000.0,
                formatted_value="1,250,000",
                unit="CLP",
                formula_used="sum(monto)",
                status="ok",
            ),
        ],
    )


async def test_export_kpi_report_bundle_xlsx_and_pdf(tmp_path: Path) -> None:
    """Verify real openpyxl (.xlsx) & reportlab (.pdf) output, formula escaping, and PII strip."""
    detail_csv = tmp_path / "detail.csv"
    detail_csv.write_text(
        "item,rut,nota\nA1,12.345.678-9,=CMD|'/C calc'!A0\nA2,98.765.432-1,ok\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "bundle_out"
    kpis = _sample_kpis()

    dry = await export_kpi_report_bundle(
        computed_kpis=kpis,
        output_dir=str(out_dir),
        dry_run=True,
    )
    assert dry.dry_run is True
    assert not out_dir.exists()

    live = await export_kpi_report_bundle(
        computed_kpis=kpis,
        output_dir=str(out_dir),
        report_title="Reporte Semanal Los Bronces",
        summary_text="Se registran 43 piezas firmadas y 5 pendientes vencidos.",
        detail_rows_path=str(detail_csv),
        dry_run=False,
    )
    assert live.status == "ok"
    assert live.stripped_pii_columns == ["rut"]
    assert len(live.files) == 2

    xlsx_path = next(Path(f.path) for f in live.files if f.format == "xlsx")
    pdf_path = next(Path(f.path) for f in live.files if f.format == "pdf")
    assert xlsx_path.exists()
    assert pdf_path.exists()
    assert pdf_path.read_bytes().startswith(b"%PDF-")

    wb = openpyxl.load_workbook(xlsx_path)
    assert set(wb.sheetnames) >= {"Resumen", "KPIs", "Detalle"}
    detail_ws = wb["Detalle"]
    detail_headers = [cell.value for cell in detail_ws[1]]
    assert "rut" not in detail_headers
    assert str(detail_ws["B2"].value).startswith("'=")


async def test_draft_report_summary_guards_hallucinated_numbers() -> None:
    """Verify number extraction rejects invented numbers and accepts exact cited KPI figures."""
    kpis = _sample_kpis()

    bad = await draft_report_summary(
        computed_kpis=kpis,
        audience="contract_admin",
        cadence="weekly",
        candidate_narrative="Hoy cerramos 47 piezas firmadas con 9 pendientes vencidos.",
    )
    assert bad.numbers_verified is False
    assert bad.verified_narrative is None
    assert sorted(bad.unverified_numbers) == ["47", "9"]

    good = await draft_report_summary(
        computed_kpis=kpis,
        audience="contract_admin",
        cadence="weekly",
        candidate_narrative=(
            "En 2026-03-10 se reportan 43 piezas firmadas, 5 pendientes vencidos "
            "y montos por cobrar de 1250000."
        ),
        allowed_extra_numbers=[2026.0, 3.0, 10.0],
    )
    assert good.numbers_verified is True
    assert good.verified_narrative is not None
    assert good.unverified_numbers == []


async def test_send_report_email_security_controls_and_delivery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify domain allowlist, external send confirmation, size limits, and email hashing."""
    attachment = tmp_path / "reporte.pdf"
    attachment.write_bytes(b"%PDF-1.4 mock report")
    monkeypatch.setenv("PBI_EMAIL_ALLOWED_DOMAINS", "quadlogic.cl,mandante.cl")
    monkeypatch.setenv("PBI_INTERNAL_EMAIL_DOMAINS", "quadlogic.cl")

    # External recipient blocked without confirm_external_send=True
    ext_blocked = await send_report_email(
        recipients_to=["gerencia@mandante.cl"],
        subject="Mensual",
        body_markdown="Resumen.",
        attachment_paths=[str(attachment)],
        audience_profile="external_client",
        dry_run=True,
    )
    assert ext_blocked.status == "blocked"
    assert any("confirm_external_send=True" in b for b in ext_blocked.blocked_reasons)
    # Plain email addresses never appear in result hashes
    serialized = json.dumps(ext_blocked.model_dump(mode="json"))
    assert "gerencia@mandante.cl" not in serialized

    # Disallowed domain blocked
    bad_dom = await send_report_email(
        recipients_to=["leak@gmail.com"],
        subject="Mensual",
        body_markdown="Resumen.",
        dry_run=True,
    )
    assert bad_dom.status == "blocked"
    assert any("not in PBI_EMAIL_ALLOWED_DOMAINS" in b for b in bad_dom.blocked_reasons)

    # Oversized attachment blocked
    big_file = tmp_path / "huge.bin"
    big_file.write_bytes(b"x" * 2048)
    oversize = await send_report_email(
        recipients_to=["ops@quadlogic.cl"],
        subject="Diario",
        body_markdown="Resumen.",
        attachment_paths=[str(big_file)],
        max_attachment_bytes=512,
        dry_run=True,
    )
    assert oversize.status == "blocked"
    assert any("exceeds limit" in b for b in oversize.blocked_reasons)

    # Mock Microsoft Graph post delivery works
    sent_payloads: list[dict[str, Any]] = []

    class _GraphResp:
        status_code = 202
        headers = {"request-id": "req-abc"}

    class _GraphStub:
        async def post(
            self, url: str, *, json: dict[str, Any], headers: dict[str, str]
        ) -> _GraphResp:
            sent_payloads.append({"url": url, "json": json, "headers": headers})
            return _GraphResp()

    monkeypatch.setenv("PBI_EMAIL_SENDER", "bot@quadlogic.cl")
    monkeypatch.setenv("PBI_GRAPH_ACCESS_TOKEN", "dummy-graph-token")
    delivered = await send_report_email(
        recipients_to=["ops@quadlogic.cl"],
        subject="Reporte Diario",
        body_markdown="Cuerpo ok",
        attachment_paths=[str(attachment)],
        dry_run=False,
        transport="graph",
        graph_client=_GraphStub(),
    )
    assert delivered.status == "sent"
    assert delivered.transport_used == "graph"
    assert delivered.message_id == "req-abc"
    assert len(sent_payloads) == 1
    assert sent_payloads[0]["url"].endswith("/users/bot@quadlogic.cl/sendMail")


async def test_check_report_data_consistency_detects_47_vs_43(tmp_path: Path) -> None:
    """Verify reconciliation flags 'dashboard says 47 while source says 43' discrepancy."""
    catalog_path = tmp_path / "cat.yaml"
    cat_res = await define_kpi_catalog(
        catalog_path=str(catalog_path),
        kpis=[
            {
                "id": "piezas_firmadas",
                "name": "Piezas Firmadas",
                "area": "operaciones",
                "unit": "piezas",
                "formula_dax": 'COUNTROWS(FILTER(Control, Control[estado] = "firmada"))',
                "python_rule": "count_where",
                "rule_params": {"column": "estado", "equals": ["firmada"]},
                "owner": "Procesos",
                "source_table": "control",
                "periodicity": "daily",
            }
        ],
        dry_run=False,
    )
    assert cat_res.status == "ok"

    async def _query_47(_dax: str) -> list[dict[str, Any]]:
        return [{"[val]": 47.0}]

    res = await check_report_data_consistency(
        workspace_id="ws-1",
        dataset_id="ds-1",
        catalog_path=str(catalog_path),
        expected_kpis={"piezas_firmadas": 43.0},
        tolerance_pct=0.001,
        query_executor=_query_47,
    )
    assert res.consistent is False
    assert res.checks_run == 1
    assert res.mismatches[0].expected_value == 43.0
    assert res.mismatches[0].actual_value == 47.0

    # Passing match returns consistent=True
    async def _query_43(_dax: str) -> list[dict[str, Any]]:
        return [{"[val]": 43.0}]

    ok_res = await mcp_server.check_report_data_consistency(
        workspace_id="ws-1",
        dataset_id="ds-1",
        catalog_path=str(catalog_path),
        expected_kpis={"piezas_firmadas": 43.0},
        query_executor=_query_43,
    )
    assert ok_res["consistent"] is True
    assert required_scope_for_tool("check_report_data_consistency") == SCOPE_READ
    assert TOOL_REQUIRED_SCOPE["send_report_email"] == SCOPE_WRITE


def test_all_seven_new_tools_registered_in_fastmcp() -> None:
    """Verify all 7 Phase 2/3 tools are registered in mcp._tool_manager."""
    tool_names = {t.name for t in mcp_server.mcp._tool_manager.list_tools()}
    expected_new = {
        "define_kpi_catalog",
        "ingest_source_table",
        "compute_process_kpis",
        "export_kpi_report_bundle",
        "draft_report_summary",
        "send_report_email",
        "check_report_data_consistency",
    }
    assert expected_new <= tool_names
    assert len(tool_names) >= 38
