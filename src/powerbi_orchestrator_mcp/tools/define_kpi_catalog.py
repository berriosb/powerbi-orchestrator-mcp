"""define_kpi_catalog tool — versioned KPI single source of truth (ISO 7.5).

Maintains a repository-backed YAML KPI catalog (``kpi_catalog.yaml``) inside
``PBI_WORKSPACE_ROOT`` with deterministic Python rule metadata, optional DAX
formulas, business owner, source table, periodicity, and breach thresholds.
Feeds ``compute_process_kpis`` and ``check_report_data_consistency``.
"""

from __future__ import annotations

import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.validation.path_safety import validate_write_path

_KPI_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

_VALID_RULES = frozenset(
    {
        "ratio_pct",
        "avg_days_between",
        "p95_days_between",
        "overdue_count",
        "sum_amount",
        "count_where",
    }
)


class KpiDefinition(BaseModel):
    """Declarative specification for one business/process KPI."""

    id: str = Field(..., description="Snake-case unique identifier (e.g. 'pct_firmadas').")
    name: str
    area: str
    formula_dax: str | None = None
    python_rule: Literal[
        "ratio_pct",
        "avg_days_between",
        "p95_days_between",
        "overdue_count",
        "sum_amount",
        "count_where",
    ]
    rule_params: dict[str, Any] = Field(default_factory=dict)
    owner: str
    source_table: str
    periodicity: Literal["daily", "weekly", "monthly"]
    unit: str = "%"
    target_threshold: float | None = None
    threshold_direction: Literal["min", "max"] = "min"


class DefineKpiCatalogInput(BaseModel):
    """Input schema for ``define_kpi_catalog``."""

    catalog_path: str
    kpis: list[KpiDefinition] = Field(default_factory=list)
    version: str = "1.0.0"
    approver: str | None = None
    merge: bool = True
    include_process_preset: bool = False
    dry_run: bool = True


class DefineKpiCatalogResult(BaseModel):
    """Output schema for ``define_kpi_catalog``."""

    status: Literal["ok", "invalid", "dry_run"]
    dry_run: bool
    catalog_path: str
    version: str
    kpi_count: int = 0
    changed_ids: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)


def default_process_kpi_preset() -> list[KpiDefinition]:
    """Starter catalog covering operational/project control KPIs (QuadLogic / Flow B)."""
    return [
        KpiDefinition(
            id="cumplimiento_rutinas",
            name="Cumplimiento de Rutinas",
            area="Operaciones",
            formula_dax='DIVIDE(CALCULATE(COUNTROWS(Control), Control[estado] = "Completada"), COUNTROWS(Control), 0) * 100',
            python_rule="ratio_pct",
            rule_params={"column": "estado", "equals": ["Completada", "Firmada", "OK"]},
            owner="Jefatura de Procesos",
            source_table="control",
            periodicity="daily",
            unit="%",
            target_threshold=90.0,
            threshold_direction="min",
        ),
        KpiDefinition(
            id="dias_por_etapa",
            name="Días Promedio por Etapa",
            area="Proyectos",
            formula_dax="AVERAGE(Control[dias_etapa])",
            python_rule="avg_days_between",
            rule_params={"start_column": "fecha_inicio", "end_column": "fecha_fin"},
            owner="Administrador de Contrato",
            source_table="control",
            periodicity="weekly",
            unit="días",
            target_threshold=15.0,
            threshold_direction="max",
        ),
        KpiDefinition(
            id="pendientes_vencidos",
            name="Pendientes Vencidos",
            area="Operaciones",
            formula_dax='CALCULATE(COUNTROWS(Control), Control[fecha_compromiso] < TODAY(), Control[estado] <> "Completada")',
            python_rule="overdue_count",
            rule_params={
                "due_date_column": "fecha_compromiso",
                "status_column": "estado",
                "closed_values": ["Completada", "Cerrado", "Firmado", "Pagado"],
            },
            owner="Jefatura de Procesos",
            source_table="control",
            periodicity="daily",
            unit="items",
            target_threshold=0.0,
            threshold_direction="max",
        ),
        KpiDefinition(
            id="montos_por_cobrar",
            name="Montos por Cobrar",
            area="Finanzas / EP",
            formula_dax='CALCULATE(SUM(Control[monto]), Control[estado_pago] = "Por Cobrar")',
            python_rule="sum_amount",
            rule_params={
                "amount_column": "monto",
                "filter_column": "estado_pago",
                "equals": ["Por Cobrar", "Pendiente", "Facturado"],
            },
            owner="Administrador de Contrato",
            source_table="control",
            periodicity="monthly",
            unit="CLP",
            target_threshold=None,
            threshold_direction="max",
        ),
        KpiDefinition(
            id="avance_por_partida",
            name="Avance por Partida",
            area="Proyectos",
            formula_dax="DIVIDE(SUM(Control[cantidad_ejecutada]), SUM(Control[cantidad_programada]), 0) * 100",
            python_rule="ratio_pct",
            rule_params={
                "numerator_column": "cantidad_ejecutada",
                "denominator_column": "cantidad_programada",
            },
            owner="Administrador de Contrato",
            source_table="control",
            periodicity="weekly",
            unit="%",
            target_threshold=85.0,
            threshold_direction="min",
        ),
        KpiDefinition(
            id="estados_de_pago_pendientes",
            name="Estados de Pago Pendientes",
            area="Finanzas / EP",
            formula_dax='CALCULATE(COUNTROWS(Control), Control[estado_pago] = "Pendiente")',
            python_rule="count_where",
            rule_params={"column": "estado_pago", "equals": ["Pendiente", "En Revisión"]},
            owner="Administrador de Contrato",
            source_table="control",
            periodicity="weekly",
            unit="EP",
            target_threshold=2.0,
            threshold_direction="max",
        ),
        KpiDefinition(
            id="piezas_postuladas",
            name="Piezas / Documentos Postulados",
            area="Licitaciones",
            formula_dax="COUNTROWS(Control)",
            python_rule="count_where",
            rule_params={"column": "estado", "not_null": True},
            owner="Encargado Documental",
            source_table="control",
            periodicity="daily",
            unit="docs",
            target_threshold=None,
            threshold_direction="min",
        ),
        KpiDefinition(
            id="pct_firmadas",
            name="Porcentaje Firmadas",
            area="Licitaciones",
            formula_dax='DIVIDE(CALCULATE(COUNTROWS(Control), Control[estado] = "Firmado"), COUNTROWS(Control), 0) * 100',
            python_rule="ratio_pct",
            rule_params={"column": "estado", "equals": ["Firmado", "Firmada", "Aprobado"]},
            owner="Encargado Documental",
            source_table="control",
            periodicity="daily",
            unit="%",
            target_threshold=95.0,
            threshold_direction="min",
        ),
    ]


def _validate_kpis(kpis: list[KpiDefinition]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for kpi in kpis:
        if not _KPI_ID_RE.match(kpi.id):
            errors.append(f"invalid kpi id {kpi.id!r}: must match ^[a-z][a-z0-9_]{{1,63}}$")
        if kpi.id in seen:
            errors.append(f"duplicate kpi id {kpi.id!r}")
        seen.add(kpi.id)
        if kpi.python_rule not in _VALID_RULES:
            errors.append(f"unsupported python_rule {kpi.python_rule!r} for {kpi.id!r}")
        if kpi.formula_dax is not None and not kpi.formula_dax.strip():
            errors.append(f"empty formula_dax for {kpi.id!r}")
    return errors


async def define_kpi_catalog(
    catalog_path: str,
    kpis: list[dict[str, Any] | KpiDefinition] | None = None,
    version: str = "1.0.0",
    approver: str | None = None,
    merge: bool = True,
    include_process_preset: bool = False,
    dry_run: bool = True,
) -> DefineKpiCatalogResult:
    """Create, merge, and validate a versioned YAML KPI catalog."""
    target = validate_write_path(catalog_path, must_exist=False)

    parsed_kpis: list[KpiDefinition] = []
    if include_process_preset:
        parsed_kpis.extend(default_process_kpi_preset())

    validation_errors: list[str] = []
    for raw in kpis or []:
        try:
            item = raw if isinstance(raw, KpiDefinition) else KpiDefinition.model_validate(raw)
            # Replace preset item if caller explicitly supplied same id.
            parsed_kpis = [existing for existing in parsed_kpis if existing.id != item.id]
            parsed_kpis.append(item)
        except Exception as exc:  # noqa: BLE001
            validation_errors.append(f"invalid kpi definition: {exc}")

    if merge and target.exists():
        try:
            existing_raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
            existing_items = [
                KpiDefinition.model_validate(entry) for entry in (existing_raw.get("kpis") or [])
            ]
            incoming_by_id = {k.id: k for k in parsed_kpis}
            merged: list[KpiDefinition] = []
            for old in existing_items:
                merged.append(incoming_by_id.pop(old.id, old))
            merged.extend(incoming_by_id.values())
            parsed_kpis = merged
        except Exception as exc:  # noqa: BLE001
            validation_errors.append(f"failed reading existing catalog: {exc}")

    validation_errors.extend(_validate_kpis(parsed_kpis))
    if not parsed_kpis and not validation_errors:
        validation_errors.append(
            "catalog must contain at least one KPI (pass kpis or include_process_preset=True)"
        )

    if validation_errors:
        return DefineKpiCatalogResult(
            status="invalid",
            dry_run=dry_run,
            catalog_path=str(target),
            version=version,
            kpi_count=len(parsed_kpis),
            validation_errors=validation_errors,
        )

    changed_ids = [k.id for k in parsed_kpis]
    if dry_run:
        return DefineKpiCatalogResult(
            status="dry_run",
            dry_run=True,
            catalog_path=str(target),
            version=version,
            kpi_count=len(parsed_kpis),
            changed_ids=changed_ids,
        )

    payload = {
        "schema_version": "1.0",
        "version": version,
        "approver": approver,
        "kpis": [k.model_dump(mode="json") for k in parsed_kpis],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )

    return DefineKpiCatalogResult(
        status="ok",
        dry_run=False,
        catalog_path=str(target),
        version=version,
        kpi_count=len(parsed_kpis),
        changed_ids=changed_ids,
    )


__all__ = [
    "DefineKpiCatalogInput",
    "DefineKpiCatalogResult",
    "KpiDefinition",
    "default_process_kpi_preset",
    "define_kpi_catalog",
]
