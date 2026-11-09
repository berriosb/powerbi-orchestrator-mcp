# Release Notes — v1.16.0

**Fecha:** 2026-10-08
**Tipo:** Minor — nueva funcionalidad (`confirm_each_step`) + correcciones

Cierra los puntos que quedaron abiertos tras la auditoría de v1.15.0.

---

## Nuevo: `confirm_each_step` realmente confirma

Hasta v1.15.0 el parámetro era un placeholder:

```python
confirm_each_step: bool = False,  # noqa: ARG001 — elicitation hook for v2
```

Es decir, se aceptaba pero **no hacía nada**. Un LLM que pasara
`confirm_each_step=True` creía estar aplicando un control por paso que en
realidad no existía.

Ahora `apply_plan` pide aprobación vía MCP elicitation antes de cada paso:

```
[2/3] REAL EXECUTION — run step 'rename-col'?
  engine=modeling action=column.update
Options: Yes, run this step, No, stop here
```

### Falla en cerrado (fail closed)

El paso **no** se ejecuta si:

- no hay cliente interactivo (sin contexto MCP);
- el cliente no soporta elicitation o el prompt falla;
- el usuario rechaza;
- la respuesta no es un sí explícito (`y`, `yes`, `s`, `si`, `sí`, `1`,
  `true`, `ok`, `go`, `run`). Una respuesta vaga como "maybe?" **no** cuenta
  como consentimiento.

Un flag de confirmación que ejecuta igual cuando no puede preguntar sería peor
que no tenerlo.

### Un rechazo no dispara rollback

Cuando el usuario detiene la ejecución, `apply_plan` devuelve
`result="stopped_by_user"` con los pasos ejecutados y los restantes marcados
`skipped`. **No** entra en la ruta de rollback: no hay nada que deshacer y
reportar `rolled_back` para pasos que nunca corrieron sería incorrecto.

### Rate limiter

`elicit()` acepta `bypass_rate_limit` para este bucle. El cooldown de 5 s
habría bloqueado la sesión interactiva: aprobar 3 pasos habría tardado 10 s y
la cuarta confirmación habría lanzado `ElicitationRateLimitError`.

---

## Correcciones: fallos silenciosos que parecían "estado limpio"

Cuatro helpers tragaban la excepción y devolvían un resultado vacío,
**making a broken Fabric API indistinguishable from an empty workspace**:

| Helper | Efecto del bug |
|---|---|
| `_enumerate_workspace_items` | Detección de conflictos contra un workspace con la API caída se reportaba como "deploy limpio" |
| `_snapshot_workspace` | Commit sin contenido, reportado como "nada que commitear" |
| `_enumerate_items` | Promoción que no mueve nada, sin indicar que el stage nunca se inspeccionó |

Ahora los tres reportan el fallo real vía `warnings`, incluyendo el tipo de
respuesta inesperada cuando la API devuelve algo que no es lista/dict.

También:

- **`set_sensitivity_labels`**: el `except Exception: pass` sobre el
  `audit_logger` hacía que una operación *bloqueada* pudiera no dejar rastro.
  Ahora se reporta.
- **`sync_git_to_workspace`**: un `try: pass` muerto dejaba el import de
  `pre_deploy_check` sin protección (la rama `except` era inalcanzable). El
  import ahora vive dentro del `try`.

---

## Cobertura de las rutas de escritura

Estas rutas mutan el modelo semántico en disco y estaban casi sin probar.

| Módulo | Antes | Ahora |
|---|---|---|
| `orchestrator/step_executor.py` | 56 % | **88 %** |
| `engines/te_adapter.py` | 51 % | **71 %** |
| **Total proyecto** | 86 % | **88 %** |

Nuevo `tests/unit/test_write_paths.py` (22 tests) cubre:

- create/update/delete de medidas persistiendo a disco en **PBISM** y **TMDL**;
- update de expresión y renombrado de medida;
- **fallo explícito** cuando la tabla destino no existe (no un no-op silencioso);
- round-trip de `snapshot` → mutación → `restore_snapshot`;
- modelo corrupto (JSON inválido) → no rompe el adapter;
- dispatch del report executor (`add_page`, `add_visual`, `update_visual`,
  `propagate_rename`, `validate_pbir`) y que `disconnect` siempre corre;
- los cuatro modos de fallo de la confirmación por paso.

---

## Verificación

- `pytest tests/` → **997 passed**, 11 skipped (los 11 siguen requiriendo
  binarios externos: `te`, `pbip-validator`)
- Cobertura: **88.19 %** (CI exige 80 %)
- `ruff check` → All checks passed
- `ruff format --check` → 130 files already formatted
- `mypy src tests --strict` → no issues, 129 source files
- `verify_mcp_server.py` → handshake real, 28 tools, `tools/call` OK
- Schema MCP de `apply_plan` verificado: expone `plan_id`, `dry_run`,
  `confirm_each_step`; `ctx` es inyectado por FastMCP y **no** se expone al
  cliente; `dry_run` mantiene `default: true`.