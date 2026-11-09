# Release Notes — v1.15.0

**Fecha:** 2026-10-08
**Tipo:** Minor — cambia el default de las tools de escritura (breaking change
para consumidores que dependían de la ejecución implícita)

---

## Resumen

Auditoría completa del proyecto (tests, lint, tipos, cobertura, seguridad,
empaquetado, CI y handshake MCP real). Esta release corrige los tres
bloqueantes encontrados y cierra las brechas de proceso.

**Todos los comandos de verificación se ejecutaron realmente sobre este
árbol:** 967 tests, `ruff check`, `ruff format --check`, `mypy --strict`,
`pip-audit`, handshake MCP y `pip install` en venv limpio.

---

## ⚠️ Cambio de comportamiento (requiere atención)

### Las tools de escritura ahora son seguras por defecto

`add_measure_with_validation`, `apply_plan` y `promote_in_pipeline` pasaron de
`dry_run=False` a `dry_run=True` por defecto.

**Por qué:** estas tools modifican archivos locales o mueven artefactos reales
entre stages de un Deployment Pipeline de Fabric (incluido `prod`). Con el
default anterior, un LLM que invoque la tool sin especificar el flag ejecutaba
la acción de inmediato. `apply_plan` además tenía `confirm_each_step` como un
placeholder sin implementar (`noqa: ARG001 — elicitation hook for v2`), así que
no existía ninguna confirmación por paso.

**Qué hacer si dependías de la ejecución implícita:**

```python
# antes
add_measure_with_validation(target=..., measure_name="Total Sales", ...)
promote_in_pipeline(pipeline_id=..., source_stage="dev", target_stage="test")

# ahora
add_measure_with_validation(target=..., measure_name="Total Sales", ..., dry_run=False)
promote_in_pipeline(pipeline_id=..., source_stage="dev", target_stage="test", dry_run=False)
```

---

## Correcciones de seguridad

### 1. No-op silencioso en `add_measure_with_validation`

`measure_writer` es un `callable`, y **un cliente MCP no puede inyectar un
callable**. Por lo tanto, en el 100 % de las llamadas reales vía MCP la tool
no tenía escritor, y el retorno era:

```
success = True
error_message = "no measure_writer provided; measure NOT persisted (test path)"
changed_files = []
```

Reportaba éxito sin haber escrito nada. Un LLM informaría al usuario "medida
creada" cuando no se creó nada — el peor tipo de fallo en un flujo de
modelado semántico.

**Fix:** devuelve `success=False` con un mensaje explícito de "no persistido".

### 2. `runtime_check` reportaba ejecución que no ocurría

Retornaba `{"ran": True, "parsed_ok": False, "error": "...requires modeling
engine integration (v2)"}`. La combinación `ran=True` + error se lee como
"se validó en runtime y falló" cuando en realidad ningún motor fue invocado.

**Fix:** `{"ran": False, "supported": False, "error": "..."}`.

---

## Proceso y calidad

| Cambio | Detalle |
|---|---|
| `ruff format` aplicado | 96 archivos reformatados; el árbol comply |
| `ruff format --check` en CI | Antes nadie lo corría: 95 de 129 archivos habían derivado sin señal |
| `pip-audit` en CI y dev extra | `--strict`. Resultado actual: **0 CVEs** |
| `dist/` obsoleto eliminado | Contenía wheel/tarball de 1.11.0 con el código en 1.14.2 |

---

## Tests

`967 passed, 11 skipped` (antes 962). Los 11 omitidos siguen requiriendo
binarios externos (`te` de TabularEditor, `pbip-validator`) y no son fallos.

Tests de regresión añadidos para cada corrección:

- `test_missing_writer_reports_failure_not_silent_success`
- `test_runtime_check_does_not_claim_it_ran`
- `test_defaults_to_dry_run` (add_measure)
- `test_defaults_to_dry_run` (promote_in_pipeline, verifica `client.calls == []`)
- `test_apply_plan_defaults_to_dry_run`

---

## Lo que se verificó y sigue bien

- Cero `shell=True` / `eval` / `pickle` en todo `src/`.
- Path safety resiste symlink→`/etc`, traversal, `~/.ssh` y null byte.
- Cadena HMAC-SHA256 en el audit log, redacción de secretos y PII.
- `mypy --strict` limpio en 71 archivos.
- Instalación limpia desde PyPI funciona y reporta la versión correcta.
- `PBI_ORCHestrATOR_AUDIT_SECRET` (con `e` minúscula) es backward-compat
  **intencional**, documentado en CHANGELOG y release notes de v1.14.0.

---

## Pendiente (no bloqueante, para próximas iteraciones)

- Subir cobertura de `te_adapter.py` (51 %) y `step_executor.py` (56 %):
  son los módulos que ejecutan comandos reales.
- Revisar los 53 `except Exception`: varios convierten un fallo de red en
  "sin conflictos" silencioso.
- Implementar `confirm_each_step` como elicitación real en vez de placeholder.