# Spec: Orquestación (Capa 6)

> Capa 6 — el corazón del servidor. Planner, connect_target, apply_plan,
> rollback engine, audit log, elicitation.

**Status:** v0.1 (spec)
**Prioridad:** P0 — bloqueante para todo lo demás
**Responsable:** codehak
**Depende de:** ninguna (es la capa raíz)
**Habilita:** todas las demás capas
**Spec relacionado:** [`docs/architecture.md`](../docs/architecture.md) §2.1

---

## Cambios respecto a v0.0

N/A (spec inicial).

---

## 1. Objetivo

Recibir tool calls del agente, descomponerlos en un **Plan YAML versionable**,
ejecutarlos delegando a las capas internas (1-5), validar entre steps y
**revertir atómicamente** ante cualquier fallo.

**Métricas de éxito:**
- Latencia p95 de `connect_target` <2s.
- Latencia p95 de `apply_plan` para plan de 5 steps <10s.
- 0 planes ejecutados sin elicitation cuando `risk_score > threshold`.
- Audit log verificable (HMAC chain intacta) en 100% de operaciones destructivas.

---

## 2. Componentes

### 2.1 `server.py` — FastMCP entrypoint

```python
# src/orchestrator/server.py
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("powerbi-orchestrator-mcp")

@mcp.tool()
async def connect_target(...) -> ConnectResult: ...
@mcp.tool()
async def plan_change(...) -> PlanResult: ...
@mcp.tool()
async def apply_plan(...) -> ApplyResult: ...

# ... 25 tools más

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

**Responsabilidad:** registrar tools, manejar JSON-RPC, delegar a módulos
internos, devolver `structuredContent` tipado.

### 2.2 `planner.py` — NL → Plan

**API:**

```python
class PlanBuilder:
    async def build(
        self,
        intent: str,
        target: Target,
        options: PlanOptions | None = None,
    ) -> Plan: ...

class Plan(BaseModel):
    id: str
    yaml: str  # serializable, Git-friendly
    steps: list[PlanStep]
    rollback_steps: list[PlanStep]  # en orden inverso
    risk_score: float  # 0.0 - 1.0
    estimated_changes: EstimatedChanges

class PlanStep(BaseModel):
    id: str
    engine: Literal["modeling", "report", "cloud", "viz", "validation"]
    action: str  # ej: "modeling.column.update", "report.rename_propagate"
    args: dict[str, Any]
    depends_on: list[str]  # step ids
    rollback_step: PlanStep | None
    validators: list[str]  # validator names a correr después

class PlanOptions(BaseModel):
    auto_rollback: bool = True
    max_impact_threshold: int = 50
    dry_run_first: bool = True
```

**Cómo genera el plan:**

1. Parsea `intent` (NL o structured args).
2. Llama a `context.get_active_target()` para saber qué motor usar.
3. Para tools conocidos (`safe_rename`, `audit_*`, `deploy_*`): el plan es
   fijo, viene de un template.
4. Para intents libres: usa un LLM local (NO OpenAI) para des componer.
   En MVP, NO se soporta intents libres arbitrarios — solo los 28 tools
   de alto nivel predefinidos.
5. Calcula `risk_score` heurístico: nº de archivos afectados × tipo de
   operación (rename bajo, drop alto).
6. Calcula `estimated_changes` consultando engines (ej: "rename propagará a
   12 medidas + 4 visuales").

### 2.3 `context.py` — Estado de sesión

```python
class SessionContext(BaseModel):
    session_id: str
    target: Target | None  # PBI Desktop / Fabric workspace / PBIP / .pbix
    engines_available: dict[str, EngineStatus]
    metadata_cache: dict[str, Any]  # model schema, visual registry, etc.
    undo_stack: list[UndoEntry]

class EngineStatus(BaseModel):
    name: str
    available: bool
    version: str | None
    reason_unavailable: str | None
```

**Persistencia:** SQLite (WAL mode) en `~/.powerbi-orchestrator-mcp/sessions/`.
Cache de metadata con TTL configurable (default 30 min).

### 2.4 `audit.py` — Log HMAC-chained

```sql
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_args_hash TEXT NOT NULL,
    target_id TEXT,
    result_status TEXT NOT NULL,  -- 'success' | 'rolled_back' | 'partial' | 'failed'
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
```

**HMAC chain:** `row_hash = HMAC(secret, prev_hash || id || timestamp || ...)`.

**Verificador:** `audit verify` CLI command — recorre la tabla, recalcula
hashes, reporta el primer break.

### 2.5 `rollback.py` — Engine de rollback

```python
class RollbackEngine:
    async def execute_step(self, step: PlanStep) -> RollbackResult: ...
    async def execute_plan(
        self,
        executed_steps: list[PlanStep],
        failed_step: PlanStep,
    ) -> RollbackResult: ...
```

**Reglas:**
- Rollback en orden inverso.
- Si un rollback step falla → `RollbackError` con paths exactos para acción
  manual (no abortar, devolver lo más útil posible).
- Rollback atómico por step, no por plan completo.

### 2.6 `elicitation.py` — Wrapper MCP 2025-06-18

```python
class ElicitationRequest(BaseModel):
    question: str
    choices: list[ElicitationChoice] | None  # None = texto libre
    multi_select: bool = False
    required: bool = True
    context: dict[str, Any]

async def elicit(
    request: ElicitationRequest,
) -> ElicitationResponse: ...
```

**Cuándo elicitar:**
- Primer connect a workspace.
- Primer write a modelo (independiente del target).
- Refresh en workspace marcado como `production`.
- Delete de item.
- Set de sensitivity label.
- Promote en deployment pipeline (dev→test→prod).
- `apply_plan` con `risk_score > 0.7`.

---

## 3. Tools MVP v1

### 3.1 `connect_target`

```yaml
tool_name: connect_target
input_schema:
  type: object
  properties:
    target_type:
      type: string
      enum: ["pbi_desktop", "fabric_workspace", "pbip_folder", "pbix_file"]
    target_ref: {type: string}
    auth_mode:
      type: string
      enum: ["interactive", "service_principal"]
      default: "interactive"
    tenant_id: {type: string}
output_schema:
  type: object
  properties:
    session_id: {type: string}
    engines_available:
      type: object
      additionalProperties:
        type: object
        properties:
          available: {type: boolean}
          version: {type: string}
          reason_unavailable: {type: string}
    warnings: {type: array, items: {type: string}}
errors:
  - target_not_found
  - auth_failed
  - no_engines_available
  - unsupported_os
```

**Flujo interno:**

1. Validar `target_ref` (path existe, workspace_id es UUID válido, etc.).
2. Detectar OS → qué engines son viables.
3. Para cada engine candidato (`powerbi-modeling-mcp`, `superbi-mcp`, `te`,
   `dscmd`, `pbip-validator`): intentar `--version` o equivalente, timeout 5s.
4. Si `target_type == fabric_workspace`: elicitar auth si primera vez.
5. Cargar metadata inicial (model schema si PBIP, capacity si workspace).
6. Devolver `session_id` para tools siguientes.

### 3.2 `plan_change`

```yaml
tool_name: plan_change
input_schema:
  type: object
  required: [intent]
  properties:
    intent: {type: string, description: "NL o template name como 'safe_rename'"}
    options:
      type: object
      properties:
        auto_rollback: {type: boolean, default: true}
        max_impact_threshold: {type: integer, default: 50}
        dry_run_first: {type: boolean, default: true}
output_schema:
  type: object
  properties:
    plan_id: {type: string}
    plan_yaml: {type: string, description: "Plan declarativo versionable"}
    steps: {type: array, items: {...}}
    risk_score: {type: number, minimum: 0, maximum: 1}
    estimated_changes:
      type: object
      properties:
        files_affected: {type: integer}
        measures_affected: {type: integer}
        visuals_affected: {type: integer}
        rollback_complexity: {type: string, enum: ["trivial", "moderate", "complex"]}
errors:
  - intent_unparseable
  - target_required  # si no hay session activa
  - risk_exceeds_threshold  # elicitation upstream
```

**Templates predefinidos (MVP):**

| Template | Args | Steps |
|----------|------|-------|
| `safe_rename` | `{old_path, new_path, scope}` | 1. snapshot, 2. modeling update, 3. report propagate, 4. validate, 5. rollback on fail |
| `audit` | `{target, checks[]}` | 1. gather metadata, 2. run checks parallel, 3. aggregate score |
| `deploy` | `{pbip_path, workspace_id, options}` | 1. pre-deploy gate, 2. create items, 3. bind gateway, 4. configure refresh, 5. refresh |
| `dax_regression` | `{baseline, queries}` | 1. load baseline, 2. execute queries parallel, 3. diff |

### 3.3 `apply_plan`

```yaml
tool_name: apply_plan
input_schema:
  type: object
  required: [plan_id]
  properties:
    plan_id: {type: string}
    dry_run: {type: boolean, default: false}
    confirm_each_step: {type: boolean, default: false}
output_schema:
  type: object
  properties:
    result:
      type: string
      enum: ["success", "rolled_back", "partial", "failed"]
    executed_steps: {type: array}
    failed_step: {...}
    rollback_steps_executed: {type: array}
    artifacts_changed: {type: array, items: {type: string}}
    rollback_handle: {type: string, description: "Para revertir manual post-success"}
errors:
  - plan_not_found
  - precondition_failed
  - rollback_failed  # con paths para acción manual
  - elicitation_required
```

**Flujo interno:**

```
1. Validar plan_id existe y target está conectado.
2. Si dry_run → ejecutar steps sin writes, devolver diff estimado.
3. Si confirm_each_step → elicitation por step.
4. Crear snapshot: git tag (si es repo) + tar backup del PBIP a ~/.powerbi-orchestrator-mcp/snapshots/.
5. Para cada step en orden:
   a. Ejecutar via engine adapter.
   b. Capturar changed_files.
   c. Correr validators definidos en step.
   d. Si validator falla → ejecutar rollback_step, devolver partial.
6. Emitir audit_log entry con HMAC.
7. Devolver resultado + rollback_handle.
```

---

## 4. State machine

```
[no_session] --connect_target--> [session_active]
[session_active] --plan_change--> [plan_ready]
[plan_ready] --apply_plan(success)--> [session_active, last_plan_id]
[plan_ready] --apply_plan(rollback)--> [session_active, last_error]
[session_active] --connect_target(new)--> [session_active, new_target]
[session_active] --disconnect--> [no_session]
```

---

## 5. Plan YAML format

```yaml
# plan_id: plan_2026-08-21_abc123
# generated_by: powerbi-orchestrator-mcp v0.1.0
# target: pbip:./out/sales.pbip
# created_at: 2026-08-21T10:30:00Z
# risk_score: 0.3
metadata:
  intent: "renombrar Customer[ID] → CustomerKey"
  template: safe_rename
  auto_rollback: true

target:
  type: pbip_folder
  ref: ./out/sales.pbip

steps:
  - id: snapshot
    engine: validation
    action: create_snapshot
    args:
      target: ./out/sales.pbip
      label: pre-rename-2026-08-21
    rollback_step:
      id: snapshot-restore
      engine: validation
      action: restore_snapshot
      args:
        label: pre-rename-2026-08-21

  - id: rename-model
    engine: modeling
    action: column.update
    args:
      table: Customer
      column: ID
      new_name: CustomerKey
    depends_on: [snapshot]
    validators: [pbip_validate_model]
    rollback_step:
      id: rename-model-revert
      engine: modeling
      action: column.update
      args:
        table: Customer
        column: CustomerKey
        new_name: ID

  - id: rename-report-bindings
    engine: report
    action: propagate_rename
    args:
      old_path: Customer[ID]
      new_path: Customer[CustomerKey]
      scope: report_bindings
    depends_on: [rename-model]
    validators: [pbir_validate]
    rollback_step:
      id: rename-report-bindings-revert
      engine: report
      action: propagate_rename
      args:
        old_path: Customer[CustomerKey]
        new_path: Customer[ID]
        scope: report_bindings

  - id: validate-all
    engine: validation
    action: pbip_validate_full
    args: {}
    depends_on: [rename-report-bindings]
```

---

## 6. Acceptance criteria

- [ ] `connect_target` detecta correctamente 4 tipos de target y reporta engines disponibles.
- [ ] `connect_target` elicita auth cuando es primera conexión a Fabric.
- [ ] `plan_change` genera YAML válido para los 4 templates MVP.
- [ ] `plan_change` calcula `risk_score` y elicita si >0.7.
- [ ] `apply_plan` ejecuta steps en orden, con rollback automático si uno falla.
- [ ] `apply_plan` deja `rollback_handle` válido incluso en success.
- [ ] Audit log SQLite tiene HMAC chain verificable por CLI `audit verify`.
- [ ] Context de sesión persiste durante vida del proceso server.
- [ ] Elicitation MCP 2025-06-18 funciona con VS Code + Claude Desktop + OpenClaw.
- [ ] `connect_target` retorna <2s p95 en target PBIP local.

## 7. Out of scope (MVP)

- ❌ Multi-session simultáneo (un proceso = una sesión).
- ❌ Persistencia entre sesiones (el agente guarda su propio memory).
- ❌ Remote transport (HTTP) → v4.
- ❌ UI propia para elicitation (depende del cliente MCP).
- ❌ Intents NL libres arbitrarios (solo los 28 tools predefinidos).

## 8. Riesgos

| Riesgo | Mitigación |
|--------|-----------|
| Plan YAML malformado al commiteo a Git | Schema Pydantic estricto + pre-commit hook `python -m orchestrator.plan validate`. |
| Rollback falla parcialmente | `RollbackError` con paths exactos + audit log con `result_status=partial`. |
| Elicitation spamea al usuario | Rate limit interno: máx 1 cada 5s; agrupar cuando sea posible. |
| Audit log crece sin límite | Rotación diaria + compresión; opt-in push a Log Analytics. |
| Snapshot de PBIP grande ocupa disco | Comprimir con zstd; retentar 7 días; warning si >1GB. |

## 9. Specs relacionados

- [`docs/architecture.md`](../docs/architecture.md) §2.1
- [`specs/02-cloud-fabric.md`](./02-cloud-fabric.md) — capa 3
- [`specs/03-validation.md`](./03-validation.md) — capa 4
- [`specs/04-viz-ux.md`](./04-viz-ux.md) — capa 5
- [`specs/05-engines-adapters.md`](./05-engines-adapters.md) — capa 1/2
- [`specs/tools/safe-rename.md`](./tools/safe-rename.md) — primer usuario de apply_plan
