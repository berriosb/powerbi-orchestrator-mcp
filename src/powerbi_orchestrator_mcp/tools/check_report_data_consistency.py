"""check_report_data_consistency tool — source vs Power BI semantic model reconciliation.

Reconciles deterministic KPI values computed from source files (Excel/CSV) or
expected golden baselines against live Power BI dataset DAX ``executeQueries``
results, catching discrepancies ("report says 47 while source has 43") and
stale dataset refreshes before distribution.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.tools.compute_process_kpis import compute_process_kpis
from powerbi_orchestrator_mcp.tools.define_kpi_catalog import (
    KpiDefinition,
    default_process_kpi_preset,
)
from powerbi_orchestrator_mcp.validation.path_safety import validate_safe_pbip_path


class ConsistencyMismatch(BaseModel):
    """Single KPI discrepancy between source and published semantic model."""

    kpi_id: str
    expected_value: float | None = None
    actual_value: float | None = None
    delta: float | None = None
    delta_pct: float | None = None
    reason: str


class CheckReportDataConsistencyInput(BaseModel):
    """Input schema for ``check_report_data_consistency``."""

    workspace_id: str
    dataset_id: str
    source_path: str | None = None
    sheet_name: str | None = None
    catalog_path: str | None = None
    expected_kpis: dict[str, float] = Field(default_factory=dict)
    tolerance_pct: float = 0.001
    as_of_date: str | None = None


class CheckReportDataConsistencyResult(BaseModel):
    """Output schema for ``check_report_data_consistency``."""

    consistent: bool
    checks_run: int = 0
    mismatches: list[ConsistencyMismatch] = Field(default_factory=list)
    dataset_last_refresh: str | None = None
    warnings: list[str] = Field(default_factory=list)


def _extract_scalar(resp: Any) -> float | None:
    if isinstance(resp, (int, float)) and not isinstance(resp, bool):
        return float(resp)
    if isinstance(resp, list) and resp and isinstance(resp[0], dict):
        first_val = next(iter(resp[0].values()), None)
        if isinstance(first_val, (int, float)) and not isinstance(first_val, bool):
            return float(first_val)
    if isinstance(resp, dict):
        results = resp.get("results") or []
        if results and isinstance(results, list):
            tables = results[0].get("tables") or []
            if tables:
                rows = tables[0].get("rows") or []
                if rows and isinstance(rows[0], dict):
                    first_val = next(iter(rows[0].values()), None)
                    if isinstance(first_val, (int, float)) and not isinstance(first_val, bool):
                        return float(first_val)
    return None


def _load_catalog(catalog_path: str | None) -> list[KpiDefinition]:
    if not catalog_path:
        return default_process_kpi_preset()
    import yaml

    safe = validate_safe_pbip_path(catalog_path, must_be_dir=False)
    raw = yaml.safe_load(safe.read_text(encoding="utf-8")) or {}
    return [KpiDefinition.model_validate(item) for item in (raw.get("kpis") or [])]


async def check_report_data_consistency(
    workspace_id: str,
    dataset_id: str,
    source_path: str | None = None,
    sheet_name: str | None = None,
    catalog_path: str | None = None,
    expected_kpis: dict[str, float] | None = None,
    tolerance_pct: float = 0.001,
    as_of_date: str | None = None,
    query_executor: Callable[[str], Any] | None = None,
    fabric_client: Any = None,
    auth_mode: str | None = None,
    tenant_id: str | None = None,
    client_id: str | None = None,
) -> CheckReportDataConsistencyResult:
    """Reconcile source/expected KPIs against a published Power BI dataset."""
    expected_map: dict[str, float] = dict(expected_kpis or {})
    warnings: list[str] = []

    if source_path:
        src_kpis = await compute_process_kpis(
            source_path=source_path,
            sheet_name=sheet_name,
            catalog_path=catalog_path,
            as_of_date=as_of_date,
        )
        warnings.extend(src_kpis.warnings)
        for item in src_kpis.kpis:
            if item.value is not None and item.kpi_id not in expected_map:
                expected_map[item.kpi_id] = float(item.value)

    if not expected_map:
        return CheckReportDataConsistencyResult(
            consistent=False,
            checks_run=0,
            warnings=[*warnings, "no expected KPIs or source_path figures available to reconcile"],
        )

    catalog = {k.id: k for k in _load_catalog(catalog_path)}
    client = fabric_client
    close_client = False
    if query_executor is None and client is None:
        from powerbi_orchestrator_mcp.cloud.auth import AuthConfig, FabricCredential
        from powerbi_orchestrator_mcp.cloud.fabric_client import FabricClient

        cfg = AuthConfig.from_env_or_args(
            mode=auth_mode,
            tenant_id=tenant_id,
            client_id=client_id,
        )
        client = FabricClient(FabricCredential(cfg))
        close_client = True

    last_refresh: str | None = None
    mismatches: list[ConsistencyMismatch] = []
    checks_run = 0

    try:
        if client is not None and hasattr(client, "get_refresh_history"):
            try:
                hist = await client.get_refresh_history(workspace_id, dataset_id, top=1)
                if hist and isinstance(hist, list):
                    last_refresh = (
                        str(hist[0].get("endTime") or hist[0].get("startTime") or "") or None
                    )
                    if str(hist[0].get("status", "")).lower() == "failed":
                        warnings.append("latest dataset refresh status is Failed")
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"could not read refresh history: {exc}")

        for kpi_id, expected_val in expected_map.items():
            kpi_def = catalog.get(kpi_id)
            if kpi_def is None or not kpi_def.formula_dax:
                warnings.append(f"skipping {kpi_id}: no formula_dax defined in catalog")
                continue
            dax = f'EVALUATE ROW("val", {kpi_def.formula_dax})'
            checks_run += 1
            try:
                if query_executor is not None:
                    raw_out = query_executor(dax)
                    resp = await raw_out if inspect.isawaitable(raw_out) else raw_out
                else:
                    resp = await client.execute_queries(
                        workspace_id,
                        dataset_id,
                        queries=[{"query": dax}],
                    )
                actual_val = _extract_scalar(resp)
            except Exception as exc:  # noqa: BLE001
                mismatches.append(
                    ConsistencyMismatch(
                        kpi_id=kpi_id,
                        expected_value=expected_val,
                        actual_value=None,
                        reason=f"dax query failed: {exc}",
                    )
                )
                continue

            if actual_val is None:
                mismatches.append(
                    ConsistencyMismatch(
                        kpi_id=kpi_id,
                        expected_value=expected_val,
                        actual_value=None,
                        reason="dataset query returned no numeric scalar",
                    )
                )
                continue

            delta = actual_val - expected_val
            denom = abs(expected_val) if expected_val != 0 else 1.0
            delta_pct = (abs(delta) / denom) * 100.0
            if delta_pct > tolerance_pct:
                mismatches.append(
                    ConsistencyMismatch(
                        kpi_id=kpi_id,
                        expected_value=expected_val,
                        actual_value=actual_val,
                        delta=round(delta, 4),
                        delta_pct=round(delta_pct, 4),
                        reason=f"drift {delta_pct:.4f}% exceeds tolerance {tolerance_pct}%",
                    )
                )
    finally:
        if close_client and client is not None and hasattr(client, "aclose"):
            await client.aclose()

    return CheckReportDataConsistencyResult(
        consistent=(checks_run > 0 and len(mismatches) == 0),
        checks_run=checks_run,
        mismatches=mismatches,
        dataset_last_refresh=last_refresh,
        warnings=warnings,
    )


__all__ = [
    "CheckReportDataConsistencyInput",
    "CheckReportDataConsistencyResult",
    "ConsistencyMismatch",
    "check_report_data_consistency",
]
