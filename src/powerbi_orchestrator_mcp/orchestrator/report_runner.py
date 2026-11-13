"""Headless Flow B report runner for scheduled cron / Task Scheduler execution.

Executes the deterministic reporting pipeline end-to-end without launching an
MCP client or spending LLM tokens:

1. ``compute_process_kpis`` (CSV/Excel or Fabric DAX)
2. Deterministic summary assembly + ``draft_report_summary`` guardrail
3. ``export_kpi_report_bundle`` (.xlsx / .pdf with PII stripped)
4. Optional ``check_report_data_consistency`` against published Power BI dataset
5. Optional ``send_report_email`` (dry-run by default unless ``live=True``)
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.cloud.audit_cloud import CloudAuditLog
from powerbi_orchestrator_mcp.tools.check_report_data_consistency import (
    CheckReportDataConsistencyResult,
    check_report_data_consistency,
)
from powerbi_orchestrator_mcp.tools.compute_process_kpis import (
    ComputeProcessKpisResult,
    compute_process_kpis,
)
from powerbi_orchestrator_mcp.tools.draft_report_summary import draft_report_summary
from powerbi_orchestrator_mcp.tools.export_kpi_report_bundle import export_kpi_report_bundle
from powerbi_orchestrator_mcp.tools.send_report_email import (
    SendReportEmailResult,
    send_report_email,
)
from powerbi_orchestrator_mcp.validation.path_safety import (
    validate_safe_pbip_path,
    validate_write_path,
)


class ReportEmailConfig(BaseModel):
    """Email delivery settings inside a scheduled report job config."""

    recipients_to: list[str] = Field(default_factory=list)
    recipients_cc: list[str] = Field(default_factory=list)
    subject_template: str = "{report_title} — {as_of_date}"
    confirm_external_send: bool = False
    transport: Literal["auto", "graph", "smtp"] = "auto"
    sender_address: str | None = None


def _default_report_formats() -> list[Literal["xlsx", "pdf"]]:
    return ["xlsx", "pdf"]


class ReportJobSpec(BaseModel):
    """Declarative YAML/JSON schema for ``powerbi-orchestrator run-report``."""

    source_path: str | None = None
    sheet_name: str | None = None
    workspace_id: str | None = None
    dataset_id: str | None = None
    catalog_path: str | None = None
    kpi_ids: list[str] | None = None
    area_filter: str | None = None
    as_of_date: str | None = None
    dax_table_name: str = "Control"
    report_title: str = "Reporte Operacional de Procesos"
    output_dir: str
    formats: list[Literal["xlsx", "pdf"]] = Field(default_factory=_default_report_formats)
    detail_rows_path: str | None = None
    include_pii_columns: bool = False
    audience: Literal["operations", "contract_admin", "external_client"] = "operations"
    cadence: Literal["daily", "weekly", "monthly"] = "daily"
    language: Literal["es", "en"] = "es"
    reconcile: bool = False
    tolerance_pct: float = 0.001
    fail_on_mismatch: bool = False
    email: ReportEmailConfig | None = None


class RunReportResult(BaseModel):
    """Structured result emitted by ``run_scheduled_report``."""

    status: Literal["ok", "consistency_failed", "email_blocked", "email_failed"]
    live: bool
    as_of_date: str
    row_count_evaluated: int
    numbers_verified: bool
    exported_files: list[str] = Field(default_factory=list)
    consistency_ok: bool | None = None
    mismatches_count: int = 0
    email_status: str = "skipped"
    recipient_hashes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _render_deterministic_narrative(
    kpis_res: ComputeProcessKpisResult,
    *,
    cadence: str,
    language: str,
) -> str:
    """Format an auditable prose summary strictly from verified KPI numbers."""
    parts = [
        f"{kpi.name}: {kpi.value if kpi.value is not None else 'N/A'} ({kpi.unit}, {kpi.status})"
        if kpi.unit == "%"
        else f"{kpi.name}: {kpi.formatted_value} ({kpi.status})"
        for kpi in kpis_res.kpis
    ]
    if language == "en":
        header = (
            f"Automated {cadence} report as of {kpis_res.as_of_date} "
            f"({kpis_res.row_count_evaluated} rows evaluated). "
        )
    else:
        header = (
            f"Reporte {cadence} al {kpis_res.as_of_date} "
            f"({kpis_res.row_count_evaluated} registros evaluados). "
        )
    return header + "; ".join(parts) + "."


def load_report_job_spec(config_path: str) -> ReportJobSpec:
    """Load and validate a YAML or JSON report job file inside ``PBI_WORKSPACE_ROOT``."""
    safe = validate_safe_pbip_path(config_path, must_be_dir=False)
    raw_text = safe.read_text(encoding="utf-8")
    parsed = json.loads(raw_text) if safe.suffix.lower() == ".json" else yaml.safe_load(raw_text)
    if not isinstance(parsed, dict):
        raise ValueError(f"report config must parse to an object/mapping: {config_path}")
    return ReportJobSpec.model_validate(parsed)


async def run_scheduled_report(
    job: ReportJobSpec,
    *,
    as_of_date_override: str | None = None,
    output_dir_override: str | None = None,
    live: bool = False,
    fabric_client: Any = None,
    graph_client: Any = None,
    smtp_sender: Any = None,
) -> RunReportResult:
    """Run deterministic KPI -> bundle -> reconcile -> email workflow headlessly."""
    effective_as_of = as_of_date_override or job.as_of_date or dt.date.today().isoformat()
    target_out = output_dir_override or job.output_dir
    validate_write_path(target_out, must_exist=False)

    kpis_res = await compute_process_kpis(
        source_path=job.source_path,
        sheet_name=job.sheet_name,
        workspace_id=job.workspace_id,
        dataset_id=job.dataset_id,
        catalog_path=job.catalog_path,
        kpi_ids=job.kpi_ids,
        as_of_date=effective_as_of,
        area_filter=job.area_filter,
        dax_table_name=job.dax_table_name,
        fabric_client=fabric_client,
    )

    candidate = _render_deterministic_narrative(
        kpis_res,
        cadence=job.cadence,
        language=job.language,
    )
    draft = await draft_report_summary(
        computed_kpis=kpis_res,
        audience=job.audience,
        cadence=job.cadence,
        candidate_narrative=candidate,
        language=job.language,
    )
    narrative_text = draft.verified_narrative or candidate

    bundle = await export_kpi_report_bundle(
        computed_kpis=kpis_res,
        output_dir=target_out,
        report_title=job.report_title,
        summary_text=narrative_text,
        detail_rows_path=job.detail_rows_path or job.source_path,
        formats=job.formats,
        include_pii_columns=job.include_pii_columns,
        dry_run=False,
    )

    warnings = [*kpis_res.warnings, *bundle.warnings]
    exported_files = [item.path for item in bundle.files]

    consistency: CheckReportDataConsistencyResult | None = None
    if job.reconcile and job.workspace_id and job.dataset_id:
        consistency = await check_report_data_consistency(
            workspace_id=job.workspace_id,
            dataset_id=job.dataset_id,
            source_path=job.source_path,
            sheet_name=job.sheet_name,
            catalog_path=job.catalog_path,
            tolerance_pct=job.tolerance_pct,
            as_of_date=effective_as_of,
            fabric_client=fabric_client,
        )
        warnings.extend(consistency.warnings)
        if not consistency.consistent and job.fail_on_mismatch:
            _record_audit(
                status="failed",
                report_title=job.report_title,
                as_of_date=effective_as_of,
                exported_files=exported_files,
                recipient_hashes=[],
            )
            return RunReportResult(
                status="consistency_failed",
                live=live,
                as_of_date=kpis_res.as_of_date,
                row_count_evaluated=kpis_res.row_count_evaluated,
                numbers_verified=draft.numbers_verified,
                exported_files=exported_files,
                consistency_ok=False,
                mismatches_count=len(consistency.mismatches),
                email_status="skipped_due_to_consistency_failure",
                warnings=warnings,
            )

    email_result: SendReportEmailResult | None = None
    if job.email and job.email.recipients_to:
        audience_map: dict[
            Literal["operations", "contract_admin", "external_client"],
            Literal["internal_ops", "internal_admin", "external_client"],
        ] = {
            "operations": "internal_ops",
            "contract_admin": "internal_admin",
            "external_client": "external_client",
        }
        email_profile = audience_map[job.audience]
        subject = job.email.subject_template.format(
            report_title=job.report_title,
            as_of_date=kpis_res.as_of_date,
            cadence=job.cadence,
        )
        email_result = await send_report_email(
            recipients_to=job.email.recipients_to,
            recipients_cc=job.email.recipients_cc,
            subject=subject,
            body_markdown=narrative_text,
            attachment_paths=exported_files,
            audience_profile=email_profile,
            report_cadence=job.cadence,
            confirm_external_send=job.email.confirm_external_send,
            transport=job.email.transport,
            sender_address=job.email.sender_address,
            dry_run=not live,
            graph_client=graph_client,
            smtp_sender=smtp_sender,
        )
        warnings.extend(email_result.blocked_reasons)

    final_status: Literal["ok", "consistency_failed", "email_blocked", "email_failed"] = "ok"
    if email_result is not None:
        if email_result.status == "blocked":
            final_status = "email_blocked"
        elif email_result.status == "failed":
            final_status = "email_failed"

    _record_audit(
        status="success" if final_status == "ok" else "failed",
        report_title=job.report_title,
        as_of_date=effective_as_of,
        exported_files=exported_files,
        recipient_hashes=(email_result.recipient_hashes if email_result else []),
    )

    return RunReportResult(
        status=final_status,
        live=live,
        as_of_date=kpis_res.as_of_date,
        row_count_evaluated=kpis_res.row_count_evaluated,
        numbers_verified=draft.numbers_verified,
        exported_files=exported_files,
        consistency_ok=(consistency.consistent if consistency else None),
        mismatches_count=(len(consistency.mismatches) if consistency else 0),
        email_status=(email_result.status if email_result else "skipped"),
        recipient_hashes=(email_result.recipient_hashes if email_result else []),
        warnings=warnings,
    )


def _record_audit(
    *,
    status: str,
    report_title: str,
    as_of_date: str,
    exported_files: list[str],
    recipient_hashes: list[str],
) -> None:
    with contextlib.suppress(Exception):  # noqa: BLE001
        CloudAuditLog().record(
            operation="cli.run_report",
            target_id=report_title,
            result_status=status,
            payload={
                "as_of_date": as_of_date,
                "exported_count": len(exported_files),
                "recipient_hashes": recipient_hashes,
            },
        )


__all__ = [
    "ReportEmailConfig",
    "ReportJobSpec",
    "RunReportResult",
    "load_report_job_spec",
    "run_scheduled_report",
]
