# Spec: Tool `draft_report_summary`

> Construye el prompt acotado para redacción ejecutiva de reportes (Flujo B) y verifica determinísticamente que cada número citado en la narrativa generada exista en la salida de `compute_process_kpis`.

**Scope:** `Tools.Read` (solo lectura, determinista, sin SDK/API key de LLM incrustada).

## Seguridad
- No acopla credenciales ni SDK de terceros dentro del servidor MCP.
- Rechaza (`numbers_verified=False`) cualquier cifra alucinada o alterada en `candidate_narrative`.
