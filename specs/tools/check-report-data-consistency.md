# Spec: Tool `check_report_data_consistency`

> Reconcilia cifras de un dataset Power BI publicado (vía DAX `executeQueries`) contra la tabla fuente local (`source_path`) o KPIs esperados, detectando discrepancias de refresco o transformación antes de enviar reportes.

**Scope:** `Tools.Read` (solo lectura).

## Seguridad
- `validate_safe_pbip_path` sobre `source_path` y `catalog_path`.
- Falla explícitamente si `checks_run == 0` o si alguna cifra supera `tolerance_pct`.
