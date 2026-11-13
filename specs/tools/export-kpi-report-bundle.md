# Spec: Tool `export_kpi_report_bundle`

> Genera archivos Excel (`.xlsx` multi-hoja) y PDF reales desde los resultados deterministas de `compute_process_kpis`, sin requerir capacidad Premium/Fabric (`ExportTo`).

**Scope:** `Tools.Write` (mutante, `dry_run=True` por defecto, auditada).

## Seguridad
- `validate_write_path(output_dir)` restringido a `PBI_WORKSPACE_ROOT`.
- `include_pii_columns=False` por defecto: elimina columnas PII (`rut`, `email`, `telefono`, `sueldo`) antes de escribir la hoja `Detalle`.
- Neutraliza formula injection (`=`, `+`, `-`, `@`) en celdas Excel.
