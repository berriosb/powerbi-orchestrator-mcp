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

# ... 23 tools más

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
   En MVP, NO se soporta intents libres arbitrarios — solo los 26 tools
   de alto nivel predefinidos (ver SPEC §4).
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

### 2.7 Identificadores (formatos canónicos)

Todos los IDs que emite o consume el orquestador siguen un formato
canónico. El objetivo es que sean **sortable, debuggeable, no colisionen
entre sesiones, y que un humano pueda parsearlos a ojo** desde un log o
un `git log`.

```python
# src/orchestrator/identifiers.py
import re
import uuid
from datetime import datetime, timezone

# Patrones regex (rechazan IDs malformados en inputs)
SESSION_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
PLAN_ID_PATTERN = re.compile(r"^plan_\d{4}-\d{2}-\d{2}_[A-Za-z0-9_-]{6,12}$")
STEP_ID_PATTERN = re.compile(r"^plan_\d{4}-\d{2}-\d{2}_[A-Za-z0-9_-]{6,12}:s\d+$")
TARGET_ID_PATTERN = re.compile(r"^(pbip_folder|pbix_file|fabric_workspace|pbi_desktop|xmla_endpoint):.+")
ROLLBACK_HANDLE_PATTERN = re.compile(r"^snap_\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z_[a-z0-9]{8}$")
EXECUTION_ID_PATTERN = re.compile(r"^exec_[a-f0-9]{16}$")

def new_session_id() -> str:
    """session_id: uuid4 hex sin guiones (32 chars)."""
    return uuid.uuid4().hex

def new_plan_id(created_at: datetime | None = None) -> str:
    """plan_id: 'plan_YYYY-MM-DD_<nanoid>'. Sortable + unique."""
    ts = (created_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    suffix = _nanoid(size=8)  # alphabet: [A-Za-z0-9_-]
    return f"plan_{ts}_{suffix}"

def new_step_id(plan_id: str, step_number: int) -> str:
    """step_id: '<plan_id>:s<n>'. Derivado del plan_id."""
    return f"{plan_id}:s{step_number}"

def make_target_id(target_type: str, target_ref: str) -> str:
    """target_id: '<target_type>:<ref>' para tipos addressable.
    Para PBIP/.pbix se hashea si el path contiene PII o es muy largo."""
    ...

def new_rollback_handle(snapshot_label: str) -> str:
    """rollback_handle: 'snap_<iso>_<hash8>'. Apunta a SnapshotHandle."""
    ...
```

**Tabla canónica:**

| Identificador | Formato | Ejemplo | Generado por | Persistido en |
|---------------|---------|---------|--------------|---------------|
| `session_id` | `^[a-f0-9]{32}$` (uuid hex) | `5f3a2b8c9d4e1f2a3b4c5d6e7f8a9b0c` | `new_session_id()` | `sessions` (SQLite), `SessionContext.session_id` |
| `plan_id` | `^plan_YYYY-MM-DD_<6-12 chars>$` | `plan_2026-08-21_xK3mN9pQ` | `new_plan_id()` | `plans` (SQLite), `Plan.id`, `apply_plan` input |
| `step_id` | `<plan_id>:s<n>` | `plan_2026-08-21_xK3mN9pQ:s3` | `new_step_id()` | `PlanStep.id`, logs, audit payload |
| `execution_id` | `^exec_[a-f0-9]{16}$` | `exec_5f3a2b8c9d4e1f2a` | `new_execution_id()` | `plan_executions` row PK |
| `target_id` | `<target_type>:<ref_o_hash>` | `pbip_folder:sha256:9f2a...` o `fabric_workspace:abc-123-def` | `make_target_id()` | `audit_log.target_id`, `SessionContext.target` |
| `rollback_handle` | `^snap_<iso>_<hash8>$` | `snap_2026-08-21T10-30-00Z_9f2a1c4e` | `new_rollback_handle()` | `ApplyResult.rollback_handle` |
| `snapshot_label` | string libre ≤64 chars, kebab-case | `pre-rename-2026-08-21` | humano o template | filesystem `~/.powerbi-orchestrator-mcp/snapshots/<label>.tar.zst` |

**Reglas:**

1. **`session_id` es uuid4 puro** (no incluye timestamp) — se mantiene
   backward compat con Semana 1 (commit `489f2cb` ya usa hex).
2. **`plan_id` incluye fecha** — permite que un `git log` ordenado
   cronológicamente muestre los plans en orden de creación sin parsear
   nada. Sufijo nanoid (no counter) — evita colisiones entre procesos
   paralelos del mismo dev.
3. **`step_id` derivado del `plan_id`** — no se almacena aparte; se
   computa. Garantiza que un step no puede existir sin su plan.
4. **`target_id` se hashea cuando el `target_ref` contiene PII o es
   demasiado largo** (>200 chars). El ref original se guarda en el
   `payload_json` del audit log (sanitizado), no en `target_id`. Esto
   evita loguear paths absolutos con nombres de usuario (ej.
   `C:\Users\jane.doe\...`) en logs centralizados.
5. **`rollback_handle` ≠ `snapshot_label`** — el handle es opaco y se usa
   en APIs; el label es legible y se usa en filesystem + git tags.
   Mapping 1:1 mantenido en `snapshots/` index.
6. **`execution_id` separado de `plan_id`** — un mismo plan puede
   ejecutarse múltiples veces (dry-runs, re-aplicaciones). El
   `execution_id` agrupa todas las filas de audit de una corrida.

**Validación en inputs:**

- `apply_plan(plan_id=...)` → si no matchea `PLAN_ID_PATTERN`, error
  `invalid_plan_id_format` antes de tocar la DB.
- `apply_plan(rollback_handle=...)` → validar `ROLLBACK_HANDLE_PATTERN`
  + verificar que existe en el índice de snapshots.

---

### 2.8 Crash recovery & plan state machine

El proceso del servidor puede morir en cualquier momento: Ctrl-C del dev,
OOM del runner, segfault del subprocess de un engine, `kill -9` desde CI.
Si muere entre el step 3 de 5 y su rollback, deja el filesystem y el
audit log en estado **parcial** — el siguiente `apply_plan` debe detectar
y reconciliar esto sin perder datos ni corromper el audit chain.

**Decisión de diseño (opción adoptada 2026-08-26):** detección +
elicitación al próximo `apply_plan`. No auto-rollback (riesgoso en
interactivo) ni solo-log (deja repos silenciosamente rotos).

**Modelo de estado:**

```python
class PlanExecutionState(str, Enum):
    PENDING = "pending"          # plan creado, apply_plan aún no llamado
    IN_PROGRESS = "in_progress"  # apply_plan ejecutando
    COMPLETED = "completed"      # todos los steps ok
    ROLLED_BACK = "rolled_back"  # falló un step + rollback completo
    PARTIAL = "partial"          # rollback falló a mitad
    ORPHANED = "orphaned"        # proceso murió durante IN_PROGRESS o PARTIAL
```

**Persistencia:** nueva tabla en la misma SQLite WAL que `audit_log`.

```sql
CREATE TABLE plan_executions (
    execution_id TEXT PRIMARY KEY,     -- format: ^exec_[a-f0-9]{16}$
    plan_id TEXT NOT NULL,
    plan_yaml TEXT NOT NULL,
    state TEXT NOT NULL,               -- PlanExecutionState value
    current_step_index INTEGER,        -- NULL si completado
    started_at TEXT NOT NULL,
    last_heartbeat_at TEXT NOT NULL,   -- updated cada step
    completed_at TEXT,
    result_status TEXT,                -- success | rolled_back | partial | failed
    target_id TEXT,
    FOREIGN KEY (plan_id) REFERENCES plans(plan_id)
);

CREATE INDEX idx_plan_executions_state ON plan_executions(state);
CREATE INDEX idx_plan_executions_heartbeat ON plan_executions(last_heartbeat_at);
```

**Heartbeat protocol:**

El `PlanExecutor` actualiza `last_heartbeat_at` antes de cada step y
después de completarlo. Esto permite distinguir:

- `in_progress` con heartbeat reciente (<60s) → ejecución activa, no tocar.
- `in_progress` con heartbeat viejo (>60s) → probablemente huérfana.

**Algoritmo de detección al startup:**

```python
async def reconcile_orphan_executions_on_boot() -> list[OrphanedExecution]:
    """Detecta ejecuciones abandonadas al arrancar el server."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
    orphans = db.execute("""
        SELECT execution_id, plan_id, state, current_step_index, started_at
        FROM plan_executions
        WHERE state IN ('in_progress', 'partial')
          AND last_heartbeat_at < ?
    """, cutoff.isoformat())
    for row in orphans:
        db.execute("""
            UPDATE plan_executions SET state = 'orphaned' WHERE execution_id = ?
        """, row['execution_id'])
    return orphans
```

**Comportamiento de `apply_plan` al encontrar un plan `orphaned`:**

```
[Server boot]
  └─ reconcile_orphan_executions_on_boot() → list[OrphanedExecution]

[Próximo apply_plan (cualquier plan_id) — incluyendo el del plan huérfano]
  ├─ Si hay orphans:
  │  ├─ elicitation(
  │  │    question="Se detectaron N ejecuciones abandonadas. ¿Qué hacer?",
  │  │    choices=[
  │  │      {label: "Reintentar rollback automático", value: "rollback"},
  │  │      {label: "Marcar como manuales (yo los reviso)", value: "manual"},
  │  │      {label: "Abortar (no ejecutar este apply_plan)", value: "abort"},
  │  │    ]
  │  │  )
  │  ├─ Si rollback: ejecutar RollbackEngine sobre cada orphan en orden inverso;
  │  │    cada rollback exitoso → state='rolled_back';
  │  │    cada rollback fallido → elicitation adicional con paths exactos.
  │  └─ Si manual: log warning + dejar state='orphaned' + continuar con el apply_plan.
  │
  └─ Si NO hay orphans (o el usuario eligió proceder): apply_plan normal.
```

**Reglas adicionales:**

1. **Idempotencia de `apply_plan`:** si `plan_id` ya tiene un
   `execution_id` con `state='completed'` y `dry_run=false`, retornar el
   `ApplyResult` cacheado (de `plan_executions.result_status` +
   `audit_log` rows) sin re-ejecutar. Si `dry_run=true`, siempre
   re-ejecutar (es lectura).

2. **Audit log durante un crash:** las filas ya committeadas siguen
   válidas (HMAC chain intacta). Las filas NO committeadas se pierden —
   aceptable, porque no representan una acción observable. El
   `last_heartbeat_at` no committeado también se pierde, pero el algoritmo
   de reconciliación usa `cutoff=60s` para tolerar esto.

3. **Snapshots huérfanos:** un snapshot queda en
   `~/.powerbi-orchestrator-mcp/snapshots/<label>.tar.zst` aunque el
   `apply_plan` haya terminado. Garbage collection: al boot, eliminar
   snapshots referenciados solo por executions `orphaned` con más de 30
   días. Snapshots referenciados por executions válidas: nunca eliminar.

4. **Multi-proceso (futuro, no MVP):** si en el futuro corren múltiples
   procesos del server, el heartbeat + `state=in_progress` permite
   coordination via SQLite locks. **MVP es single-process**, documentado
   como out-of-scope (ver §7).

5. **Testing del crash recovery:** test e2e con `subprocess.Popen` que
   arranca el server, ejecuta `apply_plan` de 3 steps, hace `kill -9`
   entre los steps, rearranca el server, verifica que la reconciliación
   detecta el orphan y ofrece las 3 opciones.

---

### 2.9 Elicitation: outcomes y flags globales

La elicitation (definida en §2.6) tiene 3 outcomes posibles y
múltiples flags globales que modifican el comportamiento de los tools.
Esta sección los formaliza.

#### 2.9.1 Outcomes de elicitation

```python
class ElicitationOutcome(str, Enum):
    ACCEPT = "accept"     # usuario aprobó
    DECLINE = "decline"   # usuario rechazó activamente
    DISMISS = "dismiss"   # UI cerrada / timeout / abandono
```

| Outcome | Efecto en el tool | Audit log `result_status` |
|---------|-------------------|---------------------------|
| `accept` | Proceder con la operación solicitada | (depende del tool: `success`, `rolled_back`, etc.) |
| `decline` | Abortar tool; no side-effects; devolver contexto al agente para que decida alternativa | `declined` |
| `dismiss` | Mismo comportamiento que `decline`, semánticamente distinguible para distinguir "rechazo activo" de "timeout" | `dismissed` |

**Reglas:**

1. **`decline` es un resultado válido y esperado** — NO es un error. El
   audit log lo registra pero el tool retorna `success` desde el punto
   de vista del orquestador (cumplió su contrato: preguntó, recibió
   respuesta, actuó en consecuencia).

2. **`dismiss` ocurre cuando:**
   - El cliente MCP cierra la UI de elicitation sin responder.
   - El timeout interno de `_MIN_ELICIT_INTERVAL_S` se dispara (ver §2.6).
   - El proceso del cliente MCP muere mientras el server espera.

3. **Retry tras `decline`:** si el agente quiere re-intentar la misma
   operación, debe explícitamente invocarla de nuevo. El orquestador
   NO re-elicit automáticamente (sería molesto).

4. **`decline` con side-effects parciales:** si una elicitación ocurre
   en el medio de un `apply_plan` (ej. elicitar "este step específico
   es riesgoso, ¿continuar?") y el usuario responde `decline`, se
   ejecuta el rollback del step actual pero NO de los steps anteriores
   (esos ya pasaron). El `apply_plan` overall retorna
   `result_status=rolled_back`.

#### 2.9.2 Flags globales

Tres flags a nivel del proceso del server, configurables via CLI args
del binario `powerbi-orchestrator-mcp`:

| Flag | Default | Efecto |
|------|---------|--------|
| `--readonly` | off | Desactiva tools que escriben al modelo o filesystem. `run_refresh` requiere `--allow-refresh` adicional. |
| `--allow-refresh` | off | Opt-in específico para permitir `run_refresh` aunque `--readonly` esté activo. Independiente. |
| `--allow-prod` | off | Opt-in para escrituras a workspaces tagged `production`. Sin este flag, tools que tocan prod elicitan SIEMPRE y abortan si el usuario declina. |

**Interacción entre flags:**

```
--readonly    --allow-refresh    --allow-prod    Comportamiento
─────────────────────────────────────────────────────────────
off           off                off             Default. Todo permitido (con elicitation normal).
off           off                on              Escrituras a prod permitidas sin elicitation. Resto normal.
off           on                 *               Idem default + run_refresh siempre permitido.
on            off                *               Read-only TOTAL. run_refresh bloqueado.
on            on                 *               Read-only modelo + filesystem, pero run_refresh permitido.
on            *                  on              Idem anterior + prod writes permitidas sin elicitation.
```

**Por qué dos flags separados (`--readonly` y `--allow-prod`):**

- `--readonly` es **técnico**: para CI / sandbox donde nada debe cambiar.
- `--allow-prod` es **político**: en un dev local con sesión admin, querés saltarte la fricción de elicitation para prod.

#### 2.9.3 Comportamiento por tool bajo `--readonly`

| Tool | `--readonly` activo (sin `--allow-refresh`) | Con `--allow-refresh` |
|------|---------------------------------------------|----------------------|
| `connect_target` | ✅ permitido (read-only por naturaleza) | ✅ |
| `plan_change` | ✅ permitido (no side-effects) | ✅ |
| `apply_plan(dry_run=true)` | ✅ permitido | ✅ |
| `apply_plan(dry_run=false)` | ❌ `read_only_mode` error antes de invocar | ❌ |
| `safe_rename` | ❌ `read_only_mode` | ❌ |
| `audit_model_and_report` | ✅ permitido | ✅ |
| `deploy_to_workspace` | ❌ `read_only_mode` | ❌ |
| `run_refresh` | ❌ `read_only_mode` | ✅ permitido |
| `run_dax_regression` | ✅ permitido | ✅ |
| `diff_models` | ✅ permitido | ✅ |
| `pre_deploy_check` | ✅ permitido (read-only) | ✅ |
| `generate_data_dictionary` | ✅ permitido (read-only) | ✅ |
| `apply_theme_and_accessibility_rules` | ❌ `read_only_mode` | ❌ |

**Reglas:**

1. `--readonly` es **opt-in del proceso**, no se cambia mid-sesión. Para
   activarlo/desactivarlo hay que rearrancar el server. Esto evita
   foot-guns donde un agente cambia el flag y accidentalmente escribe.

2. El flag se anuncia en cada `ConnectResult` (`flags.readonly`,
   `flags.allow_refresh`, `flags.allow_prod`) para que el agente sepa
   qué puede esperar.

3. El intento de usar un tool bloqueado retorna el error tipado
   `ReadOnlyModeError` con `remediation_hint` apuntando al flag CLI.

#### 2.9.4 Acceptance criteria

- [ ] `decline` en elicitación de un tool no side-effectful: el tool retorna sin error, `result_status=declined` en audit.
- [ ] `decline` en elicitación de un step dentro de `apply_plan`: rollback del step actual, overall `result_status=rolled_back`.
- [ ] `--readonly` bloquea `apply_plan(dry_run=false)` con `ReadOnlyModeError`.
- [ ] `--readonly` + `--allow-refresh` permite `run_refresh` pero bloquea `deploy_to_workspace`.
- [ ] Test e2e: cliente mock que cierra la elicitation sin responder → outcome `dismiss`.

---

### 2.10 Audit key rotation (CLI manual)

Decisión adoptada 2026-08-26: rotación **manual via CLI command**, no
automática. Razón: la re-firma de todo el histórico en una transacción
es delicada; queremos que un humano la dispare conscientemente.

```bash
python -m powerbi_orchestrator_mcp.orchestrator.audit rotate-key \
    [--reason "quarterly-rotation"] \
    [--no-verify]   # solo para tests; saltar el verify post-rotación
```

**Flujo interno:**

```python
def rotate_audit_key(reason: str | None = None) -> RotateKeyResult:
    """Re-firma todo el audit log con una nueva HMAC key atómicamente."""
    old_key = _get_hmac_key()
    new_key = secrets.token_bytes(32)

    # 1. Backup de la key vieja
    backup_path = AUDIT_DIR / f".audit_key.old.{int(time.time())}"
    backup_path.write_bytes(old_key)
    os.chmod(backup_path, 0o600)

    # 2. Transacción: re-firmar todas las filas
    with _audit_transaction() as conn:
        old_log = conn.execute("SELECT * FROM audit_log ORDER BY id ASC").fetchall()
        for row in old_log:
            new_row_hash = _compute_row_hash(
                new_key, row['prev_hash'], row['id'], row['timestamp'], row['_rest']
            )
            conn.execute(
                "UPDATE audit_log SET row_hash = ? WHERE id = ?",
                (new_row_hash, row['id'])
            )

        # 3. Escribir la nueva key (al final, después del UPDATE exitoso)
        key_path = AUDIT_DIR / ".audit_key"
        key_path.write_bytes(new_key)
        os.chmod(key_path, 0o600)

        # 4. Insertar audit log entry de la rotación misma (con la nueva key)
        conn.execute("""
            INSERT INTO audit_log (timestamp, tool_name, tool_args_hash,
                target_id, result_status, prev_hash, row_hash, payload_json)
            VALUES (?, 'audit.rotate_key', ?, NULL, 'success', ?, ?, ?)
        """, (...))

    # 5. Verificación post-rotación
    log = AuditLog()
    result = log.verify()
    if not result.valid:
        # ROLLBACK: restaurar key vieja + revertir UPDATE (log es WAL, recover del WAL)
        raise AuditRotationFailed(result.first_bad_row, result.error_message)

    return RotateKeyResult(
        rotated_at=datetime.now(timezone.utc),
        rows_resigned=len(old_log),
        backup_path=str(backup_path),
        reason=reason,
    )
```

**Reglas:**

1. **Permisos del archivo de key:** siempre `0o600` (owner read/write
   only). En Windows: ACL que solo dé acceso al usuario actual.

2. **Atomicidad:** la transacción SQLite WAL garantiza que si el proceso
   muere a mitad de la rotación, o todas las filas están con la key
   nueva, o ninguna (recovery del WAL rollback).

3. **Verificación obligatoria post-rotación:** si `verify()` falla,
   restaurar la key vieja desde `backup_path` y reportar error. La
   opción `--no-verify` existe solo para tests.

4. **Frecuencia recomendada:** cada 6-12 meses, o ante sospecha de
   compromiso. No automática. Documentar en `docs/engines-setup.md`.

5. **Backup retention:** las keys viejas (`.audit_key.old.*`) se
   mantienen hasta que el usuario las borre manualmente. Esto permite
   rollback manual si se descubre un problema días después.

6. **Audit log de la rotación:** la rotación misma deja un entry en
   `audit_log` con `tool_name=audit.rotate_key` y el `reason` en
   `payload_json`. Esto es parte de la cadena HMAC con la nueva key.

7. **Concurrencia:** durante la rotación, el server NO acepta nuevas
   operaciones (lock exclusivo via SQLite WAL). El cliente recibe
   `service_unavailable_retry` y debe reintentar tras 1s.

**`RotateKeyResult` schema:**

```python
class RotateKeyResult(BaseModel):
    rotated_at: datetime
    rows_resigned: int
    backup_path: str
    reason: str | None
    verification_passed: bool
```

**Acceptance criteria:**

- [ ] Comando `rotate-key` ejecuta end-to-end sobre un audit log con 100+ filas.
- [ ] Si el proceso muere a mitad: al rearrancar, `verify()` reporta la cadena con la key vieja (recovery SQLite WAL).
- [ ] Test con `--no-verify`: la rotación completa pero el siguiente `verify()` falla porque se introdujo un row mal firmado.
- [ ] Permisos del archivo de key: 0o600 en Linux/Mac; verificar via ACL en Windows.
- [ ] Audit log contiene entry de la rotación con `reason` correcto.

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

### 5.1 Plan YAML schema versioning

Todo `Plan` que produce `plan_change` lleva un header `plan_schema_version`
que permite forward/backward compatibility entre versiones del servidor.
El objetivo: un plan commiteado a Git en junio con servidor v0.2 debe
poder ser cargado, o rechazado con error claro, cuando el servidor es
v0.5 dos meses después.

#### 5.1.1 Header obligatorio

```yaml
# plan_id: plan_2026-08-21_xK3mN9pQ
# generated_by: powerbi-orchestrator-mcp v0.1.0
# target: pbip:./out/sales.pbip
# created_at: 2026-08-21T10:30:00Z
# risk_score: 0.3
# plan_schema_version: "0.1.0"   # <-- NUEVO header obligatorio
metadata:
  intent: "renombrar Customer[ID] → CustomerKey"
  template: safe_rename
  auto_rollback: true
# ... resto del plan ...
```

#### 5.1.2 Política de aceptación

```python
# src/orchestrator/plan/versioning.py
from dataclasses import dataclass
from typing import Final

SCHEMA_VERSION: Final[str] = "0.1.0"  # actualizado por bump version
MIN_SUPPORTED: Final[str] = "0.1.0"  # plans más viejos: rechazar
MAX_SUPPORTED: Final[str] = "0.1.0"  # plans más nuevos: rechazar (forward incompat)


@dataclass(frozen=True)
class VersionCompat:
    server_version: str
    min_supported: str
    max_supported: str

    def accepts(self, plan_version: str) -> tuple[bool, str | None]:
        """Retorna (accepted, reason). reason es None si accepted=True."""
        if not _is_valid_semver(plan_version):
            return False, f"plan_schema_version '{plan_version}' not valid semver"
        if _lt(plan_version, self.min_supported):
            return False, (
                f"plan_schema_version {plan_version} < min_supported "
                f"{self.min_supported}. Upgrade the server or use an older "
                f"server to run this plan."
            )
        if _gt(plan_version, self.max_supported):
            return False, (
                f"plan_schema_version {plan_version} > max_supported "
                f"{self.max_supported}. Either upgrade the server (recommended) "
                f"or regenerate the plan with `plan_change`."
            )
        return True, None
```

#### 5.1.3 Defaults MVP

En MVP, `min_supported == max_supported == current_version` (rango
estrictamente igual). Esto significa:

- Plan escrito con v0.1.0 → solo lo carga el servidor v0.1.0.
- Servidor v0.2.0 rechaza planes v0.1.0 → el usuario debe `plan_change`
  de nuevo para regenerar el plan con el schema actual.

**Razón MVP-first:** evita implementar transformaciones automáticas
entre versiones antes de tener siquiera 2 versiones distintas en
producción. Cuando llegue v0.2.0 (post-MVP), se ampliará el rango.

#### 5.1.4 Plan sin header

Si un plan cargado desde Git (o pasado manualmente) no tiene
`plan_schema_version` → rechazado con elicitación:

```
El plan cargado no tiene header plan_schema_version.
Esto puede deberse a:
  (a) Fue generado con una versión pre-0.1.0 del servidor
  (b) Fue editado a mano y el header fue borrado
Opciones:
  [Regenerar con plan_change]
  [Forzar carga como v0.1.0 (asumir compatibilidad — bajo tu responsabilidad)]
  [Abortar]
```

**Reglas:**

1. La opción "forzar carga" deja un audit log entry con
   `payload_json.forced_load=true` y `result_status=success_with_warning`
   para que quede explícito.

2. **NO se hace transformación automática de campos.** Si el schema
   cambia entre versiones (ej: nuevo campo obligatorio `rollback_strategy`
   en v0.2.0), un plan v0.1.0 sin ese campo seguirá siendo rechazado
   aunque esté dentro del rango.

#### 5.1.5 Bumping el schema version

Reglas para bumpear `SCHEMA_VERSION` (en PR con ADR):

| Tipo de cambio | Bump | Razón |
|-----------------|------|-------|
| Añadir campo opcional con default | patch (`0.1.0` → `0.1.1`) | Backward compatible |
| Añadir campo opcional sin default | minor (`0.1.0` → `0.2.0`) | Plan v0.2.0 sin ese campo aún carga en v0.2.0 (default del server) |
| Añadir campo obligatorio | major (`0.1.0` → `1.0.0`) | Planes viejos no cargan |
| Renombrar campo | major | Planes viejos no cargan |
| Cambiar semántica de campo | major | Planes viejos pueden ejecutarse incorrectamente |
| Cambiar exit code de un engine | patch (no afecta plan schema) | No requiere bump |

**Proceso:**

1. PR con cambios + bump en `SCHEMA_VERSION`.
2. ADR documentando el bump y la migración esperada.
3. Si major: actualizar `MIN_SUPPORTED` (si se quiere aceptar versiones
   más viejas) y `MAX_SUPPORTED`.
4. Tests: casos de carga para `min_supported`, `max_supported`, version
   intermedia, version malformada, version sin header.

#### 5.1.6 Acceptance criteria

- [ ] Plan con `plan_schema_version == SCHEMA_VERSION` se acepta.
- [ ] Plan con version < MIN_SUPPORTED se rechaza con `plan_not_supported`.
- [ ] Plan con version > MAX_SUPPORTED se rechaza con `plan_not_supported`.
- [ ] Plan sin header elicita con las 3 opciones.
- [ ] Opción "forzar carga" deja audit log entry con `forced_load=true`.
- [ ] Test e2e: bump de `SCHEMA_VERSION` en test, verifica que planes viejos se rechazan.

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
- ❌ Intents NL libres arbitrarios (solo los 26 tools predefinidos, ver SPEC §4).

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
