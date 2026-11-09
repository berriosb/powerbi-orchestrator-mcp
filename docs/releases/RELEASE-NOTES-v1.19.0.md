# Release Notes — v1.19.0

**Fecha:** 2026-10-20
**Tipo:** Minor — auditoría de preparación empresarial

> Cinco cambios incompatibles. Ver "BREAKING CHANGES" más abajo.

Una auditoría completa del proyecto (no una revisión superficial) encontró que
varias tools **reportaban éxito por trabajo que no habían hecho**. Este
release cierra los hallazgos bloqueantes y deja el proyecto apto para uso
supervisado por un data engineer, que es lo que su documentación ya prometía.

El hilo que recorre todos los cambios: *una herramienta que dice que validó
algo tiene que haberlo validado*.

---

## ⚠️ BREAKING CHANGES

### 1. Las sesiones HTTP quedan ligadas a la credencial que las abrió

El middleware publicaba los claims de Entra en `scope["entra_claims"]`, una
clave que el SDK de MCP nunca lee. El SDK liga una sesión a la credencial que
la abre comparando `authorization_context(scope["user"])` contra el principal
que la creó, y responde 404 cuando no coinciden. Sin `scope["user"]`, ambos
lados de la comparación eran `None`, la comparación nunca rechazaba, y
**cualquier principal autenticado podía unirse a la sesión de otro y heredar
sus scopes**.

Verificado contra la app FastMCP real: un token con scope `Tools.Read` entraba
en una sesión abierta con `Tools.Write` y ejecutaba la tool **como el
propietario de la sesión** (HTTP 200, identidad `user-a`). Con el arreglo, esa
misma llamada recibe 404.

Además, el check de scope por tool ahora se evalúa contra los claims de la
**llamada en curso**, no contra el contextvar que quedó congelado al abrir la
sesión.

### 2. Un paso de plan sin ejecutor falla en vez de devolverse como aplicado

Los ejecutores caían en `success=True` para cualquier acción que no
implementaran. Un plan podía reportarse como aplicado sin que hubiera corrido
nada.

Ahora, una acción sin implementer falla con el nombre de la acción y el
nombre del ejecutor. Las acciones que **sí** tenían una implementación real
detrás ahora la tienen de verdad:

- `pbip_validate_full` valida el PBIP en disco (antes devolvía éxito sin
  validar nada).
- `gather_metadata` / `run_checks_parallel` / `aggregate_score` ejecutan la
  auditoría real sobre el PBIP y devuelven el score en `details`.
- `create_snapshot` toma un snapshot de verdad y devuelve una ruta que
  existe (antes devolvía una ruta a un fichero que nunca se escribió, y el
  handle de rollback apuntaba ahí).
- Las acciones de nube (`workspace.publish_pbip`, `workspace.bind_gateway`,
  …) fallan con un mensaje que apunta a `deploy_to_workspace` y
  `run_refresh`, que son las tools que sí publican y programan refreshes.

### 3. `run_dax_regression` falla si no encuentra el baseline

El cargador de baseline se tragaba `FileNotFoundError` y `JSONDecodeError` y
devolvía un baseline vacío. La comparación contra cero queries daba
`passed=True`: una regresión que no comparó nada se reportaba como verde.

Ahora el baseline ausente o corrupto es un fallo explícito, y una query del
baseline sin filas esperadas se reporta como **no comparada** en vez de como
un fallo de recuento de filas.

El gate de `promote_in_pipeline` leía `result.max_drift_pct`, un atributo que
`RegressionResult` no tiene, así que siempre pasaba — incluso cuando la
regresión había fallado. Ahora gatea sobre `result.passed`.

### 4. `diff_models` compara PBIP reales

`diff_models` exigía un `inspector` inyectado para hacer trabajo: la tool
MCP exponía ese callable como parámetro, que un cliente no puede rellenar, así
que toda llamada real devolvía "0 cambios, 0 breaking" — un verde que se leía
como "estos dos modelos son idénticos".

Ahora la tool lee los dos PBIP del disco (formato JSON `definition.pbism` /
`model.bim` y formato TMDL) a través del nuevo módulo
`validation.model_metadata`. El differ además compara **columnas**, y las
ruta por las reglas de ruptura: un cambio de tipo de columna ahora se
reporta como breaking en vez de ignorarse.

El parámetro `inspector` desaparece del esquema de la tool.

### 5. `apply_plan` informa del rollback que ocurrió

`apply_plan` descartaba el estado del rollback y siempre reportaba
`rolled_back`, y renderizaba cada entrada del rollback con `status: "ok"`,
incluidas las que habían fallado.

Ahora el resultado distingue `rolled_back`, `rollback_partial` (parte de los
reverts tuvo éxito) y `rollback_incomplete` (los propios reverts fallaron), y
cada entrada lleva su estado real. Los pasos que se ejecutaron sin paso de
rollback se reportan como `not_revertible` en vez de descartarse en silencio.

---

## 🔒 Security

- **Redacción del audit log por clave de payload.** La redacción solo
  buscaba *patrones* dentro de valores string, así que un valor guardado bajo
  una clave que nombra un secreto se guardaba en claro cuando no tenía
  patrón que casar — una contraseña corta, una API key pelada. Ahora el valor
  se sustituye entero. Además se cubren los secretos con forma de URL o de
  connection string que faltaban (`access_token=`, `pwd=`, `AccountKey=`,
  `sig=`).
- **Validación de identificadores en las rutas REST de Fabric.** httpx
  colapsa los segmentos con punto, así que un `workspace_id` de
  `../../v1.0/myorg/admin/users` enviaba el token del orquestador a un
  endpoint que el llamador nunca nombró. Ahora `workspace_id`, `dataset_id`,
  `item_id` y `datasource_id` se validan en el cliente.
- **Las variables de credenciales documentadas ahora se leen.**
  `DOCKER.md` y `docker-compose.yml` indican fijar `PBI_AUTH_MODE`,
  `PBI_TENANT_ID`, `PBI_CLIENT_ID` y `PBI_CLIENT_SECRET`, pero el código solo
  leía los nombres `AZURE_*`. Un contenedor configurado así autenticaba en
  silencio como lo que encontrara `DefaultAzureCredential` — otra identidad,
  normalmente más amplia. Los nombres `PBI_*` tienen prioridad sobre `AZURE_*`;
  los argumentos explícitos ganan sobre ambos. `PBI_LOG_LEVEL` también se
  respeta.
- **El allowlist de Host del transporte HTTP sigue al bind address.** El SDK
  congela un allowlist solo-loopback al crear el servidor, así que un servidor
  ligado a una dirección de contenedor respondía 421 a todos sus clientes. La
  protección contra DNS rebinding sigue activa; solo crece el allowlist. Se
  puede acotar con `--http-allowed-host` / `PBI_HTTP_ALLOWED_HOSTS`.

---

## ✨ Changed

- `auth_mode` pasa a ser opcional en las tools que lo aceptaban: si no se
  pasa, se resuelve `PBI_AUTH_MODE` y, si no está, `interactive`.
- `AuditResult` gana `score_is_partial` y `skipped_checks`. Un score
  computado sobre un subconjunto de checks se marca como parcial y nombra los
  checks que no corrieron.
- `RegressionResult` gana `compared_queries`; `QueryDiff` gana `compared`.
- `StepOutcome` gana `details`, que `apply_plan` ahora devuelve por paso.
- Se documentan en `SECURITY.md` los controles que **no** existen: el audit
  log es *fail-open*, y la clave HMAC vive junto a la base de datos si no se
  define `PBI_ORCHESTRATOR_AUDIT_SECRET`.

---

## Verification

1231 tests pasando (desde 1200), 31 nuevos. `ruff check`, `ruff format
--check` y `mypy --strict` limpios; cobertura 88 %.

Cada arreglo crítico tiene un test de regresión que falla sin el arreglo. El
del binding de identidad se verificó desactivando el arreglo: el token ajeno
recuperaba la sesión y ejecutaba la tool como `user-a`.
