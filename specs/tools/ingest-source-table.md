# Spec: Tool `ingest_source_table`

> Ingesta y valida tablas origen (`.xlsx`, `.xlsm`, `.csv`) desde `PBI_WORKSPACE_ROOT`, verificando esquema esperado, tipos y unicidad de llaves antes de generar CSV normalizado + expresión Power Query M.

**Scope:** `Tools.Write` (mutante, `dry_run=True` por defecto, auditada).

## Seguridad
- Lectura validada con `validate_safe_pbip_path(source_path)` y escritura con `validate_write_path(target_pbip_dir)`.
- Escapa fórmulas peligrosas (`=`, `+`, `-`, `@`, `\t`, `\r`) al serializar CSV normalizado.
- Límite de filas (`max_rows` <= 100.000) para evitar agotamiento de memoria.
