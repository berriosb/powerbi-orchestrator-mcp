"""Tests for the MCP fine-closure additions (run-report CLI, M partitions, ISO handoff, audit rotation)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from powerbi_orchestrator_mcp import cli
from powerbi_orchestrator_mcp.orchestrator.audit import AuditLog
from powerbi_orchestrator_mcp.orchestrator.report_runner import (
    load_report_job_spec,
    run_scheduled_report,
)
from powerbi_orchestrator_mcp.tools.create_semantic_model_from_schema import (
    create_semantic_model_from_schema,
)
from powerbi_orchestrator_mcp.tools.define_kpi_catalog import define_kpi_catalog
from powerbi_orchestrator_mcp.tools.ingest_source_table import ingest_source_table


async def test_ingest_schema_artifact_hydrates_tmdl_m_partition(tmp_path: Path) -> None:
    """ingest_source_table .schema.json feeds create_semantic_model_from_schema M partitions."""
    csv_path = tmp_path / "control.csv"
    csv_path.write_text(
        "id_item,estado,monto,fecha\n1,Firmado,1500.5,2026-03-10\n2,Pendiente,500.0,2026-03-11\n",
        encoding="utf-8",
    )
    pbip_dir = tmp_path / "IngestedOps.pbip"

    ingested = await ingest_source_table(
        source_path=str(csv_path),
        target_pbip_dir=str(pbip_dir),
        table_name="Control",
        expected_schema={
            "id_item": "int",
            "estado": "string",
            "monto": "float",
            "fecha": "date",
        },
        key_columns=["id_item"],
        dry_run=False,
    )
    assert ingested.status == "ok"
    schema_artifact = pbip_dir / "data" / "Control.schema.json"
    assert schema_artifact.exists()

    model_out = tmp_path / "GeneratedModel.pbip"
    created = create_semantic_model_from_schema(
        spec_json={
            "name": "GeneratedModel",
            "tables": [
                {
                    "name": "Control",
                    "schema_artifact_path": str(schema_artifact),
                    "measures": [
                        {
                            "name": "Monto Total",
                            "expression": "SUM(Control[monto])",
                        }
                    ],
                }
            ],
        },
        output_pbip_path=str(model_out),
        dry_run=False,
    )
    assert created.validation_passed is True
    assert created.partitions_attached == 1
    assert created.tables_created[0].columns_count == 4

    tmdl = (model_out / "GeneratedModel.Dataset" / "definition.tmdl").read_text(encoding="utf-8")
    assert "partition Control = m" in tmdl
    assert "Csv.Document(File.Contents(" in tmdl
    assert "isKey: true" in tmdl


async def test_headless_run_report_cli_and_reconciliation_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """run-report executes headless Flow B pipeline via YAML config and enforces guards."""
    data_csv = tmp_path / "diario.csv"
    data_csv.write_text(
        "id,rut,estado,fecha_compromiso,monto\n"
        "1,11.111.111-1,Firmado,2026-03-05,1000\n"
        "2,22.222.222-2,Pendiente,2026-03-01,2500\n",
        encoding="utf-8",
    )
    catalog = tmp_path / "catalog.yaml"
    await define_kpi_catalog(
        catalog_path=str(catalog),
        kpis=[
            {
                "id": "piezas_firmadas",
                "name": "Piezas Firmadas",
                "area": "Operaciones",
                "unit": "docs",
                "formula_dax": 'COUNTROWS(FILTER(Control, Control[estado] = "Firmado"))',
                "python_rule": "count_where",
                "rule_params": {"column": "estado", "equals": ["Firmado"]},
                "owner": "Procesos",
                "source_table": "control",
                "periodicity": "daily",
            },
            {
                "id": "monto_total",
                "name": "Monto Total",
                "area": "Finanzas",
                "unit": "CLP",
                "formula_dax": "SUM(Control[monto])",
                "python_rule": "sum_amount",
                "rule_params": {"amount_column": "monto"},
                "owner": "Admin",
                "source_table": "control",
                "periodicity": "daily",
            },
        ],
        dry_run=False,
    )

    out_dir = tmp_path / "out_reports"
    job_yaml = tmp_path / "daily_job.yaml"
    job_yaml.write_text(
        yaml.safe_dump(
            {
                "source_path": str(data_csv),
                "catalog_path": str(catalog),
                "output_dir": str(out_dir),
                "report_title": "Diario Faena",
                "cadence": "daily",
                "audience": "contract_admin",
                "email": {
                    "recipients_to": ["admin@quadlogic.cl"],
                    "sender_address": "bot@quadlogic.cl",
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("PBI_EMAIL_ALLOWED_DOMAINS", "quadlogic.cl")
    monkeypatch.setenv("PBI_INTERNAL_EMAIL_DOMAINS", "quadlogic.cl")

    rc = cli.main(["run-report", "--config", str(job_yaml), "--as-of-date", "2026-03-10"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["live"] is False
    assert payload["numbers_verified"] is True
    assert payload["email_status"] == "dry_run"
    assert len(payload["exported_files"]) == 2
    assert "admin@quadlogic.cl" not in json.dumps(payload)

    # Reconciliation failure + email live with mocked smtp_sender
    job = load_report_job_spec(str(job_yaml))
    job_reconcile = job.model_copy(
        update={
            "workspace_id": "ws-1",
            "dataset_id": "ds-1",
            "reconcile": True,
            "fail_on_mismatch": True,
        }
    )

    class _MismatchFabric:
        async def get_refresh_history(
            self, _ws: str, _ds: str, *, top: int = 1
        ) -> list[dict[str, Any]]:
            del top
            return [{"status": "Completed", "endTime": "2026-03-10T06:00:00Z"}]

        async def execute_queries(
            self, _ws: str, _ds: str, *, queries: list[dict[str, Any]]
        ) -> dict[str, Any]:
            del queries
            return {"results": [{"tables": [{"rows": [{"[val]": 9999.0}]}]}]}

    failed_rec = await run_scheduled_report(
        job_reconcile,
        as_of_date_override="2026-03-10",
        live=True,
        fabric_client=_MismatchFabric(),
    )
    assert failed_rec.status == "consistency_failed"
    assert failed_rec.consistency_ok is False
    assert failed_rec.mismatches_count == 2

    # Live send succeeds with injected smtp_sender
    sent_calls: list[dict[str, Any]] = []

    def _fake_smtp(**kw: Any) -> str:
        sent_calls.append(kw)
        return "smtp-headless-1"

    ok_live = await run_scheduled_report(
        job,
        as_of_date_override="2026-03-10",
        live=True,
        smtp_sender=_fake_smtp,
    )
    assert ok_live.status == "ok"
    assert ok_live.email_status == "sent"
    assert len(sent_calls) == 1


def test_audit_export_real_subdirs_rotation_and_retention(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """audit-export backs up real audit/plans subdirs, rotates linked chains, and prunes old archives."""
    home = tmp_path / "orch_home"
    audit_dir = home / "audit"
    plans_dir = home / "plans"
    audit_dir.mkdir(parents=True)
    plans_dir.mkdir(parents=True)

    monkeypatch.setenv("PBI_ORCHESTRATOR_HOME", str(home))
    monkeypatch.setenv("PBI_ORCHESTRATOR_AUDIT_SECRET", "test-rotation-secret")

    audit = AuditLog(db_path=audit_dir / "audit.db")
    audit.insert(tool_name="define_kpi_catalog", tool_args={"k": 1}, result_status="success")
    (plans_dir / "plans.db").write_bytes(b"sqlite-plans")

    # Create a stale archive directory older than 30 days to verify retention pruning.
    backup_dest = tmp_path / "backups"
    stale_dir = backup_dest / "audit-archive-20250101T000000Z"
    stale_dir.mkdir(parents=True)
    (stale_dir / "audit.db").write_bytes(b"old")
    old_epoch = time.time() - (45 * 86400)
    os.utime(stale_dir, (old_epoch, old_epoch))

    rc = cli.main(["audit-export", str(backup_dest), "--rotate", "--retention-days", "30"])
    assert rc == 0
    manifest = json.loads(capsys.readouterr().out)
    assert "audit/audit.db" in manifest["files"]
    assert "audit/.audit_anchor" in manifest["files"]
    assert "plans/plans.db" in manifest["files"]
    assert manifest["rotated"] is True
    assert manifest["rollover_tip_hash"] is not None
    assert "audit-archive-20250101T000000Z" in manifest["pruned_archives"]
    assert not stale_dir.exists()

    # Fresh DB after rotation verifies cleanly and contains rollover anchor row.
    fresh_audit = AuditLog(db_path=audit_dir / "audit.db")
    fresh_verify = fresh_audit.verify()
    assert fresh_verify.valid is True
    assert fresh_verify.total_rows == 1


async def test_export_handoff_cli_generates_iso_75_pack(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """export-handoff creates SOLUTION-HANDOFF.md + ISO 7.5 CSV/XLSX control matrix."""
    pbip_dir = tmp_path / "HandoffModel.pbip"
    cli.main(["init", str(pbip_dir)])
    capsys.readouterr()

    cat_path = tmp_path / "handoff-cat.yaml"
    await define_kpi_catalog(
        catalog_path=str(cat_path),
        include_process_preset=True,
        approver="Jefatura SIG",
        dry_run=False,
    )

    handoff_out = tmp_path / "handoff_pack"
    rc = cli.main(
        [
            "export-handoff",
            str(handoff_out),
            "--solution-name",
            "Control Licitaciones",
            "--owner",
            "Ingeniero Transformacion Digital",
            "--approver",
            "Jefatura de Procesos",
            "--catalog-path",
            str(cat_path),
            "--pbip-path",
            str(pbip_dir),
            "--doc-version",
            "1.2.0",
        ]
    )
    assert rc == 0
    out_json = json.loads(capsys.readouterr().out)
    assert out_json["status"] == "ok"
    assert out_json["documents_count"] >= 9
    assert (handoff_out / "SOLUTION-HANDOFF.md").exists()
    assert (handoff_out / "iso-7.5-document-control.csv").exists()
    assert (handoff_out / "iso-7.5-document-control.xlsx").exists()
    md_text = (handoff_out / "SOLUTION-HANDOFF.md").read_text(encoding="utf-8")
    assert "Control Licitaciones" in md_text
    assert "audit-export" in md_text
