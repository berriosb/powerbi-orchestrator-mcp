# Spec: Tool `compute_process_kpis`

> Calcula determinísticamente indicadores operativos y de proyectos (cumplimiento de rutinas, días por etapa, pendientes vencidos, montos por cobrar, avance por partida, estados de pago, piezas postuladas, % firmadas) desde Excel/CSV o dataset Power BI publicado, devolviendo únicamente cifras auditables (cero prosa LLM).

**Scope:** `Tools.Read` (solo lectura; `impersonated_user_name` requiere `Tools.Write` en `server.py`).

## Seguridad
- Lectura de archivo acotada por `validate_safe_pbip_path`.
- Elimina columnas PII (`rut`, `email`, `telefono`, `sueldo`, etc.) antes de evaluar/serializar.
- Aritmética exacta con `Decimal` y `datetime.date`; sin `eval()`.
