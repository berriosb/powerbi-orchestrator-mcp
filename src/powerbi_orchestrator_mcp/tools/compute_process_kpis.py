"""compute_process_kpis tool — deterministic process KPI calculator.

Evaluates business/operational KPIs from CSV/Excel tables inside
``PBI_WORKSPACE_ROOT`` or from an already-published Power BI semantic model
via DAX ``executeQueries``, returning structured numeric outputs with exact
``Decimal`` currency arithmetic and zero generative prose.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.tools._tabular_io import (
    MAX_TABULAR_ROWS_DEFAULT,
    load_tabular_file,
    strip_pii_columns,
)
from powerbi_orchestrator_mcp.tools.define_kpi_catalog import (
    KpiDefinition,
    default_process_kpi_preset,
)
from powerbi_orchestrator_mcp.validation.path_safety import validate_safe_pbip_path


class ComputedKpiValue(BaseModel):
    """Deterministic calculation output for a single KPI."""

    kpi_id: str
    name: str
    area: str
    value: float | None = None
    formatted_value: str
    numerator: float | None = None
    denominator: float | None = None
    unit: str
    formula_used: str
    status: Literal["ok", "breach", "missing_data"]
    target_threshold: float | None = None


class ComputeProcessKpisInput(BaseModel):
    """Input schema for ``compute_process_kpis``."""

    source_path: str | None = None
    sheet_name: str | None = None
    workspace_id: str | None = None
    dataset_id: str | None = None
    catalog_path: str | None = None
    kpi_ids: list[str] | None = None
    as_of_date: str | None = None
    area_filter: str | None = None
    dax_table_name: str = "Control"
    impersonated_user_name: str | None = None


class ComputeProcessKpisResult(BaseModel):
    """Output schema for ``compute_process_kpis``."""

    as_of_date: str
    source_hash_sha256: str
    row_count_evaluated: int
    kpis: list[ComputedKpiValue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _parse_date(val: Any) -> dt.date | None:
    if val is None:
        return None
    if isinstance(val, dt.datetime):
        return val.date()
    if isinstance(val, dt.date):
        return val
    raw = str(val).strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    return None


def _parse_decimal(val: Any) -> Decimal | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, Decimal):
        return val
    if isinstance(val, (int, float)):
        if math.isnan(float(val)) or math.isinf(float(val)):
            return None
        return Decimal(str(val))
    raw = str(val).strip()
    if not raw:
        return None
    cleaned = raw.replace("$", "").replace("CLP", "").replace(" ", "")
    # Normalize common Spanish/English thousands/decimal formatting heuristically.
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _row_matches(row: dict[str, Any], params: dict[str, Any]) -> bool:
    col = params.get("column") or params.get("filter_column")
    if not col:
        return True
    if col not in row:
        return False
    val = row.get(col)
    if params.get("not_null"):
        return val is not None and str(val).strip() != ""
    equals = params.get("equals")
    if equals is not None:
        targets = equals if isinstance(equals, list) else [equals]
        norm_targets = {str(t).strip().lower() for t in targets}
        return val is not None and str(val).strip().lower() in norm_targets
    return True


def _format_value(val: float | None, unit: str) -> str:
    if val is None:
        return "N/A"
    if unit == "%":
        return f"{val:.2f}%"
    if unit.upper() in {"CLP", "USD", "UF"}:
        if unit.upper() == "CLP":
            return f"${val:,.0f} CLP"
        return f"{val:,.2f} {unit}"
    if val.is_integer():
        return f"{int(val)} {unit}".strip()
    return f"{val:.2f} {unit}".strip()


def _status_for(val: float | None, kpi: KpiDefinition) -> Literal["ok", "breach", "missing_data"]:
    if val is None:
        return "missing_data"
    if kpi.target_threshold is None:
        return "ok"
    if kpi.threshold_direction == "min":
        return "ok" if val >= kpi.target_threshold else "breach"
    return "ok" if val <= kpi.target_threshold else "breach"


def _evaluate_kpi(
    kpi: KpiDefinition,
    rows: list[dict[str, Any]],
    headers: set[str],
    as_of: dt.date,
) -> ComputedKpiValue:
    params = kpi.rule_params
    rule = kpi.python_rule

    if not rows:
        return ComputedKpiValue(
            kpi_id=kpi.id,
            name=kpi.name,
            area=kpi.area,
            value=None,
            formatted_value="N/A",
            unit=kpi.unit,
            formula_used=f"{rule}({params})",
            status="missing_data",
            target_threshold=kpi.target_threshold,
        )

    if rule == "ratio_pct":
        num_col = params.get("numerator_column")
        den_col = params.get("denominator_column")
        if num_col and den_col:
            if num_col not in headers or den_col not in headers:
                return _missing_kpi(kpi, f"ratio_pct({num_col}/{den_col})")
            num_sum = Decimal("0")
            den_sum = Decimal("0")
            for row in rows:
                n = _parse_decimal(row.get(num_col))
                d = _parse_decimal(row.get(den_col))
                if n is not None:
                    num_sum += n
                if d is not None:
                    den_sum += d
            if den_sum == 0:
                return _missing_kpi(kpi, f"sum({num_col}) / sum({den_col}) * 100")
            val = float((num_sum / den_sum) * Decimal("100"))
            return _ok_kpi(
                kpi,
                round(val, 2),
                float(num_sum),
                float(den_sum),
                f"sum({num_col})/sum({den_col})*100",
            )

        col = params.get("column")
        if col and col not in headers:
            return _missing_kpi(kpi, f"ratio_pct({col})")
        matched = sum(1 for row in rows if _row_matches(row, params))
        total = len(rows)
        val = round((matched / total) * 100.0, 2) if total else 0.0
        return _ok_kpi(
            kpi,
            val,
            float(matched),
            float(total),
            f"count({col} in {params.get('equals')}) / {total} * 100",
        )

    if rule in {"avg_days_between", "p95_days_between"}:
        start_col = str(params.get("start_column", "fecha_inicio"))
        end_col = str(params.get("end_column", "fecha_fin"))
        if start_col not in headers:
            return _missing_kpi(kpi, f"{rule}({start_col}, {end_col})")
        deltas: list[int] = []
        for row in rows:
            start = _parse_date(row.get(start_col))
            if start is None:
                continue
            end = _parse_date(row.get(end_col)) if end_col in headers else None
            effective_end = end or as_of
            deltas.append(max(0, (effective_end - start).days))
        if not deltas:
            return _missing_kpi(kpi, f"{rule}({start_col}, {end_col})")
        if rule == "avg_days_between":
            val = round(sum(deltas) / len(deltas), 2)
        else:
            sorted_d = sorted(deltas)
            idx = min(len(sorted_d) - 1, max(0, math.ceil(0.95 * len(sorted_d)) - 1))
            val = float(sorted_d[idx])
        return _ok_kpi(
            kpi, val, float(sum(deltas)), float(len(deltas)), f"{rule}({start_col}, {end_col})"
        )

    if rule == "overdue_count":
        due_col = str(params.get("due_date_column", "fecha_compromiso"))
        status_col = str(params.get("status_column", "estado"))
        closed_vals = {
            str(v).strip().lower()
            for v in (params.get("closed_values") or ["Completada", "Cerrado", "Firmado"])
        }
        if due_col not in headers:
            return _missing_kpi(kpi, f"overdue_count({due_col})")
        overdue = 0
        for row in rows:
            due = _parse_date(row.get(due_col))
            if due is None or due >= as_of:
                continue
            st = str(row.get(status_col, "")).strip().lower() if status_col in headers else ""
            if st not in closed_vals:
                overdue += 1
        return _ok_kpi(
            kpi,
            float(overdue),
            float(overdue),
            float(len(rows)),
            f"count({due_col} < {as_of.isoformat()} and open)",
        )

    if rule == "sum_amount":
        amount_col = str(params.get("amount_column", "monto"))
        if amount_col not in headers:
            return _missing_kpi(kpi, f"sum_amount({amount_col})")
        amount_total = Decimal("0")
        matched_rows = 0
        for row in rows:
            if not _row_matches(row, params):
                continue
            amt = _parse_decimal(row.get(amount_col))
            if amt is not None:
                amount_total += amt
                matched_rows += 1
        val = round(float(amount_total), 2)
        return _ok_kpi(
            kpi, val, float(matched_rows), float(len(rows)), f"sum({amount_col}) where filter"
        )

    if rule == "count_where":
        col = params.get("column")
        if col and col not in headers:
            return _missing_kpi(kpi, f"count_where({col})")
        cnt = sum(1 for row in rows if _row_matches(row, params))
        return _ok_kpi(kpi, float(cnt), float(cnt), float(len(rows)), f"count({col} filter)")

    return _missing_kpi(kpi, f"unsupported:{rule}")


def _missing_kpi(kpi: KpiDefinition, formula: str) -> ComputedKpiValue:
    return ComputedKpiValue(
        kpi_id=kpi.id,
        name=kpi.name,
        area=kpi.area,
        value=None,
        formatted_value="N/A",
        unit=kpi.unit,
        formula_used=formula,
        status="missing_data",
        target_threshold=kpi.target_threshold,
    )


def _ok_kpi(
    kpi: KpiDefinition,
    val: float,
    numerator: float | None,
    denominator: float | None,
    formula: str,
) -> ComputedKpiValue:
    return ComputedKpiValue(
        kpi_id=kpi.id,
        name=kpi.name,
        area=kpi.area,
        value=val,
        formatted_value=_format_value(val, kpi.unit),
        numerator=numerator,
        denominator=denominator,
        unit=kpi.unit,
        formula_used=formula,
        status=_status_for(val, kpi),
        target_threshold=kpi.target_threshold,
    )


def _load_catalog(catalog_path: str | None) -> list[KpiDefinition]:
    if not catalog_path:
        return default_process_kpi_preset()
    safe = validate_safe_pbip_path(catalog_path, must_be_dir=False)
    raw = yaml.safe_load(safe.read_text(encoding="utf-8")) or {}
    return [KpiDefinition.model_validate(item) for item in (raw.get("kpis") or [])]


async def compute_process_kpis(
    source_path: str | None = None,
    sheet_name: str | None = None,
    workspace_id: str | None = None,
    dataset_id: str | None = None,
    catalog_path: str | None = None,
    kpi_ids: list[str] | None = None,
    as_of_date: str | None = None,
    area_filter: str | None = None,
    dax_table_name: str = "Control",
    impersonated_user_name: str | None = None,
    fabric_client: Any = None,
    auth_mode: str | None = None,
    tenant_id: str | None = None,
    client_id: str | None = None,
) -> ComputeProcessKpisResult:
    """Deterministically compute operational/process KPIs from table or dataset."""
    effective_date = _parse_date(as_of_date) or dt.date.today()
    warnings: list[str] = []

    catalog = _load_catalog(catalog_path)
    if kpi_ids:
        requested = set(kpi_ids)
        catalog = [k for k in catalog if k.id in requested]
    if area_filter:
        af = area_filter.strip().lower()
        catalog = [k for k in catalog if k.area.strip().lower() == af]

    rows: list[dict[str, Any]] = []
    headers: list[str] = []
    source_sha = ""

    if source_path:
        tabular = load_tabular_file(
            source_path,
            sheet_name=sheet_name,
            max_rows=MAX_TABULAR_ROWS_DEFAULT,
        )
        headers, rows, stripped = strip_pii_columns(tabular.headers, tabular.rows)
        if stripped:
            warnings.append(f"stripped {len(stripped)} PII column(s) prior to KPI evaluation")
        source_sha = tabular.source_sha256
        warnings.extend(tabular.warnings)
    elif workspace_id and dataset_id:
        from powerbi_orchestrator_mcp.cloud.auth import AuthConfig, FabricCredential
        from powerbi_orchestrator_mcp.cloud.fabric_client import FabricClient

        client = fabric_client
        close_client = False
        if client is None:
            cfg = AuthConfig.from_env_or_args(
                mode=auth_mode,
                tenant_id=tenant_id,
                client_id=client_id,
            )
            client = FabricClient(FabricCredential(cfg))
            close_client = True
        try:
            safe_table = dax_table_name.replace("'", "''")
            resp = await client.execute_queries(
                workspace_id,
                dataset_id,
                queries=[{"query": f"EVALUATE TOPN(10000, '{safe_table}')"}],
                impersonated_user_name=impersonated_user_name,
            )
        finally:
            if close_client and hasattr(client, "aclose"):
                await client.aclose()

        results = resp.get("results") or []
        raw_rows = (
            results[0].get("tables", [{}])[0].get("rows", [])
            if results and isinstance(results, list)
            else []
        )
        # Strip 'Table[Col]' prefix returned by Power BI executeQueries.
        normalized_rows: list[dict[str, Any]] = []
        for r in raw_rows:
            norm = {}
            for k, v in r.items():
                col_name = k.split("[")[-1].rstrip("]") if "[" in k else k
                norm[col_name] = v
            normalized_rows.append(norm)
        raw_headers = list(normalized_rows[0].keys()) if normalized_rows else []
        headers, rows, stripped = strip_pii_columns(raw_headers, normalized_rows)
        if stripped:
            warnings.append(f"stripped {len(stripped)} PII column(s) from DAX query output")
        source_sha = hashlib.sha256(
            json.dumps(rows, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
    else:
        return ComputeProcessKpisResult(
            as_of_date=effective_date.isoformat(),
            source_hash_sha256="",
            row_count_evaluated=0,
            kpis=[],
            warnings=["provide source_path or (workspace_id and dataset_id)"],
        )

    header_set = set(headers)
    computed = [_evaluate_kpi(kpi, rows, header_set, effective_date) for kpi in catalog]

    return ComputeProcessKpisResult(
        as_of_date=effective_date.isoformat(),
        source_hash_sha256=source_sha,
        row_count_evaluated=len(rows),
        kpis=computed,
        warnings=warnings,
    )


__all__ = [
    "ComputeProcessKpisInput",
    "ComputeProcessKpisResult",
    "ComputedKpiValue",
    "compute_process_kpis",
]
