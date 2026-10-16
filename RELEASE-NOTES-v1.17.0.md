# Release Notes — v1.17.0

**Fecha:** 2026-10-16
**Tipo:** Minor — hardening de seguridad para uso con datos reales

> Cambio incompatible: las tools de nube requieren `dry_run=False` para
> ejecutar de verdad. Ver "BREAKING CHANGE" más abajo.

Cierra la auditoría de preparación empresarial: los 3 bloqueadores críticos
y los 8 pendientes de severidad alta/media.

---

## ⚠️ BREAKING CHANGE: las tools de nube ya no ejecutan solas

`deploy_to_workspace` y `run_refresh` ahora tienen `dry_run=True` **por
defecto**. Antes, una sola llamada publicaba en el workspace de Fabric del
cliente y disparaba un refresh full, sin paso de aprobación.

```python
# antes: publicaba de verdad
await deploy_to_workspace(pbip_path="...", workspace_id="...")

# ahora: informa el plan y no llama a la API
await deploy_to_workspace(pbip_path="...", workspace_id="...")
# -> dry_run=True, planned_actions=[...]

# para ejecutar de verdad
await deploy_to_workspace(pbip_path="...", workspace_id="...", dry_run=False)
```

`run_refresh` devuelve `status="DryRun"` en vez de disparar el refresh.
`DeployResult` incorpora los campos `dry_run` y `planned_actions`.

Esto se aplica también a cualquier flujo automatizado: hay que pasar
`dry_run=False` explícitamente.

---

## Correcciones de seguridad

### 1. Los secretos ya no viajan al contexto del LLM

`client_secret` era un **argumento de tool MCP** en `deploy_to_workspace`,
`run_refresh` y `execute_dax_query`. Eso lo metía en el payload JSON-RPC:
historial de conversación, logs del cliente MCP y requests al proveedor del
modelo.

Se eliminó de las tres firmas. `AuthConfig.from_env_or_args` ya leía
`AZURE_CLIENT_SECRET`, así que el camino seguro por variable de entorno,
Key Vault o workload identity queda intacto.

### 2. Autorización por herramienta (antes solo documentada)

`transport.py` prometía una jerarquía `Tools.Read` / `Tools.Write` /
`Tools.Admin`, pero el enforcement era una variable global: un token de solo
lectura podía invocar cualquier tool mutante.

Nuevo `orchestrator/authorization.py` mapea las 28 tools a su scope y lo
aplica **antes** de ejecutar el cuerpo. Verificado: un token `Tools.Read`
recibe `ToolAuthorizationError` al llamar `run_refresh`.

Se omite cuando no hay identidad en el contexto, porque sobre stdio el
cliente MCP ya tiene acceso local completo.

### 3. Contención de escrituras

`path_safety` era una denylist y el allowlist (`PBI_WORKSPACE_ROOT`) era
opcional: sin esa variable no había contención. Y 7 tools que escribían en
disco no lo consultaban nunca.

Nuevo `validate_write_path`, que **siempre** exige contención y cae al
directorio de trabajo del proceso. Aplicado a `create_report_from_dataset`,
`create_semantic_model_from_schema`, `design_report_page_from_requirements`,
`edit_report_visual`, `run_dax_regression`, `setup_rls_and_roles` y
`commit_workspace_to_git`.

### 4. Auditoría real, no decorativa

El audit log solo registraba `apply_plan`, y solo en el camino de éxito. No
había respuesta a "¿quién desplegó qué y cuándo?" para ninguna operación
cloud — un control básico de SOC2 / ISO 27001.

- Nuevo `orchestrator/audit_guard.py` con el decorador `@audited`, aplicado
  a las **20 tools** que mutan estado o acceden a datos.
- Registra `success`, `failed`, `dry_run`, `partial` y `rolled_back`.
- Los argumentos se persisten hasheados (SHA-256) y las claves tipo secreto
  se redactan. Solo se guarda el **tipo** de excepción, nunca el mensaje.
- Un fallo al escribir la auditoría ya no se silencia: aparece como
  `_audit_warning` en la respuesta de la tool.
- `CloudAuditLog` (que tenía la redacción de PII) queda cableado: antes era
  código muerto.

### 5. La cadena HMAC ya detecta truncamiento

`verify()` recomputaba la cadena desde el génesis, así que **borrar las
filas más recientes dejaba un prefijo autoconsistente que igual verificaba
como válido**. Reproducido antes del fix: 5 entradas → borrar 2 →
`valid=True, total_rows=3`.

Se agregó un archivo de ancla fuera de la base con la cola esperada de la
cadena. Ahora el borrado parcial y el borrado total se reportan como
truncamiento.

### 6. Permisos de las stores

`audit.db` y `plans.db` quedaban en `0o644` (legibles por todos). Ahora los
directorios quedan en `0o700` y las bases en `0o600`.

### 7. Inyección TMDL

Los nombres de tabla, columna, medida, relación y jerarquía se interpolaban
en el `definition.tmdl` sin validación, y ese archivo después lo sube
`deploy_to_workspace` a Fabric. Un nombre con salto de línea podía inyectar
directivas arbitrarias.

Ahora se rechazan nombres con saltos de línea, comillas, corchetes o
marcadores de comentario, con tope de 128 caracteres.

**Bonus:** los nombres con espacios ya generaban TMDL inválido
(`measure Total Sales`); ahora se citan correctamente como
`measure 'Total Sales'`.

### 8. Mínimo privilegio y error hygiene

- `FabricClient` pedía `Dataset.ReadWrite.All` para **todas** las
  peticiones, incluidas las de lectura. Ahora GET/HEAD/OPTIONS piden
  `READ_SCOPES` y solo las mutaciones piden `WRITE_SCOPES`.
- `execute_dax_query` no tenía tope de filas: una consulta sin `TOPN`
  podía volcar una tabla de clientes completa al contexto del modelo. Ahora
  devuelve máximo 1000 filas (techo 10 000) y marca el truncamiento.
- `FabricAPIError` embebía el body crudo de la API en el mensaje, que viaja
  al contexto del LLM. Ahora se trunca a 300 caracteres.

---

## Corrección de honestidad en la herramienta

### `run_dax_regression` ya no da verde falso

`query_executor` era un parámetro MCP que **un cliente MCP nunca puede
inyectar** (requiere un callable de Python). El ejecutor por defecto
devolvía listas vacías, así que la tool reportaba:

```python
{'passed': True, 'passed_queries': 1, 'diff_summary': 'OK'}   # 0 queries ejecutadas
```

Si se usaba como gate anti-regresión en CI, no servía para nada.

- Sin ejecutor ahora devuelve `executed=False`, `passed=False` y un
  mensaje explícito.
- Nuevo `_fabric_dax_executor`: pasando `workspace_id` y `dataset_id` la
  tool **sí ejecuta** el baseline contra el dataset real.

---

## Proceso

- `SECURITY.md` reescrito donde afirmaba controles inexistentes: la
  tabla de versiones estaba congelada en 1.10.x, y `--readonly`,
  `engines/versions.py` y la redacción PII "activa" no existían. Se agregó
  una sección **"Not implemented"** que declara los controles ausentes y
  que la cadena HMAC es tamper-evident, no tamper-proof.
- README: sección de defaults de seguridad con la tabla de cómo ejecutar
  cada tool de verdad.

## Verificación

- `pytest` — **1130 pasaron** (997 antes de este trabajo), cobertura **88%**
- `ruff check` + `ruff format --check` — limpios (137 archivos)
- `mypy src` — sin errores (73 archivos)
- `pip-audit --strict` — 0 vulnerabilidades
- `python -m build` — wheel construido
- `scripts/verify_mcp_server.py` — handshake MCP real, 28 tools
- Sin `.env` ni claves privadas en el árbol

Cada corrección tiene test de regresión: se añadieron 6 archivos de test
(`test_audit_guard.py`, `test_authorization.py`,
`test_write_path_containment.py`, `test_audit_anchor_and_permissions.py`,
`test_scope_and_error_hygiene.py`) más regresiones en
`test_server.py`, `test_tools.py` y `test_v3_tools.py`.

## Pendiente (no bloqueante)

- Sin columna de propietario en `plans.db`: cualquier usuario autenticado
  puede ejecutar un `plan_id` que conozca o adivine.
- `sync_git_to_workspace` sigue symlinks dentro del repo al leer blobs.
- `plugins.py` ejecuta cualquier `*.py` del directorio de plugins, sin
  verificación de firma.
- Variable de entorno con typo mantenida como "legacy":
  `PBI_ORCHestrATOR_AUDIT_SECRET`.