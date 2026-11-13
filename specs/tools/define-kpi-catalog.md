# Spec: Tool `define_kpi_catalog`

> Mantiene un catálogo YAML versionado (`kpi_catalog.yaml`) con la definición única de KPIs de proceso y negocio (fórmula DAX, regla determinista Python, dueño, tabla fuente, periodicidad y umbral) dentro de `PBI_WORKSPACE_ROOT` como evidencia documental ISO 7.5.

**Scope:** `Tools.Write` (mutante, `dry_run=True` por defecto, auditada).

## Seguridad
- `validate_write_path(catalog_path)` restringido estrictamente a `PBI_WORKSPACE_ROOT`.
- Valida IDs `^[a-z][a-z0-9_]{1,63}$` y reglas Python declarativas permitidas (`ratio_pct`, `avg_days_between`, `p95_days_between`, `overdue_count`, `sum_amount`, `count_where`); sin `eval`.
