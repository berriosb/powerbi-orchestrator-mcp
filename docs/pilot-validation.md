# Validación piloto contra un workspace real

Guía para validar `powerbi-orchestrator-mcp` contra un tenant de Fabric o
Power BI antes de usarlo con datos de clientes.

**El punto de partida:** la suite de tests (1158 casos) nunca llama a una API
real. Toda ruta de nube se ejercita con dobles o en `dry_run`. Los fallos
típicos de una integración real —permisos del service principal, drift de la
forma de la respuesta, round-trip de DAX— **no** aparecen hasta que ejecutas
contra el servicio.

Esta guía cubre ese hueco. Hazla **siempre contra un workspace de pruebas
propio**, nunca contra el de un cliente.

---

## Fase 0 — Configuración

| Requisito | Detalle |
|---|---|
| Service principal | Registered app en el tenant objetivo |
| Scopes de API de Power BI | `https://analysis.microsoft.com/powerbi/api/.default` |
| Scopes de Fabric | `https://api.fabric.microsoft.com/.default` |
| Permiso en el tenant | Require admin consent para el SP |
| Acceso al workspace | Asignar el SP como Miembro o Contribuyente |

> El permiso del service principal en el **workspace** importa tanto como los
> scopes. Un SP con los scopes correctos pero sin membresía en el workspace
> recibe 403 en casi todas las llamadas.

Variables:

```bash
export PBI_AUTH_MODE=service_principal
export AZURE_TENANT_ID=<tenant-guid>
export AZURE_CLIENT_ID=<app-id>
export AZURE_CLIENT_SECRET=<secret>
```

Para probar con tu propia cuenta primero: `PBI_AUTH_MODE=interactive`.

---

## Fase 1 — Preflight automático (solo lectura)

```bash
pip install powerbi-orchestrator-mcp
python scripts/pilot_check.py --workspace-id <ws-id> --dataset-id <ds-id>
```

O, si clonaste el repo, con `.venv/bin/python`.

El script **nunca** crea, actualiza, refresca ni borra nada. Solo invoca
métodos de lectura, y eso está fijado por un test. Sale con código 1 si algo
falla, así que sirve como gate en CI.

Salida esperada si todo va bien:

```
[ok  ] auth.config: auth mode service_principal
[ok  ] auth.token: 7 workspace(s) visible to this identity
[ok  ] workspace.read: Data Sandbox
[ok  ] items.read: 12 item(s) in the workspace
[ok  ] datasets.read: 2 dataset(s) reachable
[ok  ] dataset.read: dataset <id> is readable
[ok  ] dax.query: a trivial DAX query returned 1 row(s)
[ok  ] dax.row_cap: row cap of 1000 left this response untouched
[ok  ] dax.response_shape: response shape matches expectations
[ok  ] refresh.history.read: 5 refresh record(s) visible
```

### Cómo leer los fallos

| Check que falla | Causa habitual | Acción |
|---|---|---|
| `auth.config` | Variables de entorno ausentes o mal nombradas | Revisar Fase 0 |
| `auth.token` | Credenciales inválidas, o el SP no tiene consent | Verificar secreto y admin consent |
| `workspace.read` | El SP no es miembro del workspace | Asignar membresía |
| `items.read` | Falta el scope de lectura de items | Otorgar el scope |
| `datasets.read` / `dataset.read` | Falta `Dataset.Read` | Otorgar el scope |
| `dax.query` | Dataset sin permiso de lectura, o XMLA bloqueado | Revisar permisos del dataset |
| `dax.row_cap` | **El dataset devuelve más de 1000 filas** | Subir `max_rows` conscientemente |
| `dax.response_shape` | La API respondió con otra forma | **Reportar como bug** |

`dax.response_shape` en fallo significa que el modelo interno quedó
desalineado con la API real. Es el único fallo que indica un defecto del
orquestador; todos los demás son de configuración.

### Repetirlo desde CI (opcional)

Existe el workflow `pilot-validation.yml`, que corre este mismo script y
**solo se dispara a mano**: nunca en push ni en pull requests, así que no
entra como check requerido ni bloquea merges. Por defecto es inerte —si los
secrets no están configurados, el job se salta con un aviso.

Para activarlo:

1. En *Settings → Environments*, crea un environment llamado
   `pilot-validation` (opcional, pero da un gate de aprobación antes de
   correr).
2. En *Settings → Secrets and variables → Actions*, agrega:
   - `PBI_PILOT_TENANT_ID`
   - `PBI_PILOT_CLIENT_ID`
   - `PBI_PILOT_CLIENT_SECRET`
3. Lanza el workflow desde *Actions → pilot-validation → Run workflow*,
   indicando `workspace_id`, `dataset_id` y marcando la casilla de
   confirmación.

Usa un service principal de **mínimo privilegio apuntando únicamente al
workspace de pruebas**. Aunque el script es de solo lectura, un token con
permisos amplios sobre un tenant equivocado es una posición incómoda de
mantener.

---

## Fase 2 — Escritura en PBIP (local, sin nube)

Nada de esto toca Fabric. Hazlo primero.

- [ ] `audit_model_and_report` sobre un PBIP real del cliente
- [ ] `generate_data_dictionary` sobre el mismo PBIP
- [ ] `create_semantic_model_from_schema` con `dry_run=False`, escribiendo en
      el workspace root, y **abre el `.tmdl` generado**: confirma que el
      servicio lo aceptará. Los nombres con espacios deben aparecer citados
      (`measure 'Total Sales'`).
- [ ] `edit_report_visual` sobre un visual existente
- [ ] Confirma que ningún archivo se escribió fuera de `PBI_WORKSPACE_ROOT`
      (intenta deliberadamente una ruta externa: debe rechazarse)

---

## Fase 3 — Escritura en la nube (con cautiously)

Solo con el workspace de pruebas. Estos pasos **sí mutan**.

1. **Publica un modelo de juguete** (2 tablas, 1 medida) con
   `deploy_to_workspace(dry_run=False)`.
   - [ ] El item aparece en el workspace
   - [ ] El dataset se puede consultar
   - [ ] La programación diaria quedó creada
2. **Dispara un refresh** con `run_refresh(dry_run=False)`.
   - [ ] El refresh completa con estado `Completed`
   - [ ] `run_dax_query` devuelve filas coherentes
3. **Prueba la reversión.**
   - [ ] Aplica un cambio de nombre de columna y confirma que
         `apply_plan` revierte si algo falla a mitad
4. **Auditoría.**
   - [ ] `python -m powerbi_orchestrator_mcp.orchestrator.audit verify`
         reporta cadena válida
   - [ ] Las entradas de los pasos 1 y 2 aparecen en la base

> Cada paso mutante se registra en el audit log. Si encuentras una operación
> que **no** aparece ahí, es un hallazgo: repórtalo.

---

## Fase 4 — Salmonamiento a producción

Solo cuando las fases anteriores pasaron completas.

- [ ] El preflight pasa contra el workspace del cliente
- [ ] Corriste **solo lectura** contra datos reales y el resultado fue el
      esperado por el cliente
- [ ] El cliente sabe que `dry_run=False` es un acto deliberado
- [ ] Hay un responsable de negocio aprobando cada publicación
- [ ] La auditoría se revisa periódicamente, no solo cuando algo falla

### Rollback

Si algo sale mal tras desplegar:

1. `apply_plan` con rollback automático, o revierte a mano con el
   `rollback_handle` que devuelve cada operación
2. Restaura la definición anterior del item
3. Revisa la cadena de auditoría para reconstruir qué pasó

---

## Criterios de salida

Promociona a producción solo si **todas** estas condiciones se cumplen:

- [ ] Fase 1 sin fallos
- [ ] Fases 2 y 3 completas sin errores silenciosos
- [ ] La auditoría registra cada operación mutante
- [ ] El service principal tiene el mínimo privilegio que necesitas
- [ ] Alguien del equipo sabe qué hace cada tool antes de habilitarla en el
      cliente MCP

---

## Notas de seguridad operativas

- **Nunca** pongas un secreto de producción en el contexto de un LLM. Las
  tools ya no aceptan `client_secret`; créalo por variable de entorno o
  workload identity.
- Si expones el transporte HTTP, exige `Tools.Write` para las tools
  mutantes. Un token `Tools.Read` no puede ejecutarlas, pero tampoco confíes
  en la configuración por defecto sin revisarla.
- Deja las stores (`~/.powerbi-orchestrator-mcp`) fuera de backups
  compartidos: contienen IDs de workspaces y el historial de operaciones.
- En Windows, `chmod` no aplica: restringe con ACL de NTFS.

## Si algo falla

Antes de reportar un bug, confirma:

1. Que el preflight pasa (si no, es configuración)
2. Que reproduces con un workspace de pruebas mínimo
3. Que no es una de las limitaciones conocidas de `SECURITY.md`
   ("Not implemented")

Esa información hace que el reporte sea accionable en lugar de genérico.