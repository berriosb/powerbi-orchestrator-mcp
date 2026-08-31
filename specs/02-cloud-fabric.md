# Spec: Cloud Fabric / Power BI Service (Capa 3)

> Capa 3 — implementación propia. REST Fabric + XMLA opcional + Azure Identity
> + audit log de operaciones nube.

**Status:** v0.1 (spec)
**Prioridad:** P0 — gap real, nadie lo tiene maduro
**Responsable:** codehak
**Depende de:** [`01-orchestrator.md`](./01-orchestrator.md) (para elicitation y audit)
**Habilita:** [`tools/deploy-to-workspace.md`](./tools/deploy-to-workspace.md), `run_refresh`, `promote_in_pipeline` (v2), `sync_git_to_workspace` (v3)
**Spec relacionado:** [`docs/architecture.md`](../docs/architecture.md) §2.4

---

## Cambios respecto a v0.0

N/A (spec inicial).

---

## 1. Objetivo

Exponer todas las operaciones nube de Fabric / Power BI Service que un agente
necesita para un flujo end-to-end de BI, con auth robusta, audit trail y
manejo de errores tipado.

**Métricas de éxito:**
- 100% de operaciones destructivas pasan por elicitation.
- 0 secretos en logs (verificable por test automatizado).
- Latencia p95 de `list_workspaces` <500ms (incluye auth).
- Retry exponencial exitoso en >95% de errores 429/5xx transitorios.

---

## 2. Componentes

### 2.1 `auth.py` — Azure Identity wrapper

```python
# src/cloud/auth.py
from azure.identity.aio import DefaultAzureCredential
from azure.core.exceptions import ClientAuthenticationError

class FabricCredential:
    def __init__(self, mode: AuthMode = AuthMode.INTERACTIVE):
        self._credential: DefaultAzureCredential | None = None
        self._scopes = [
            "https://analysis.windows.net/powerbi/api/.default",
            "https://api.fabric.microsoft.com/.default",
        ]

    async def get_token(self) -> str:
        """Returns bearer token. Refreshes transparently."""
        ...

    async def get_credential_for_scope(self, scope: Scope) -> str:
        """Read / Write / Admin tokens por separado."""
        ...
```

**Scopes soportados:**

| Scope | Token scopes | Herramientas que lo usan |
|-------|--------------|--------------------------|
| `READ` | `Dataset.Read.All` | list, get, getRefreshHistory |
| `WRITE` | + `Dataset.ReadWrite.All` | deploy, run_refresh, updateRefreshSchedule |
| `ADMIN` | + `*.Admin.*` (gate explícito) | set_sensitivity_labels, list tenant |

**Modo de auth:**

- `interactive` (default): `InteractiveBrowserCredential` con cache de token
  en `~/.azure/` (gestionado por Azure Identity SDK, nunca por nosotros).
- `service_principal`: lee `AZURE_CLIENT_ID` + `AZURE_TENANT_ID` +
  `AZURE_CLIENT_SECRET` (o cert path) de env vars.
- `managed_identity`: cuando corre en Azure (VMSS, Container Apps).

### 2.2 `fabric_client.py` — Cliente REST

```python
# src/cloud/fabric_client.py
import httpx

class FabricClient:
    def __init__(self, credential: FabricCredential, retry_policy: RetryPolicy):
        self._http = httpx.AsyncClient(
            base_url="https://api.fabric.microsoft.com/v1",
            timeout=httpx.Timeout(30.0, connect=5.0),
        )
        self._cred = credential
        self._retry = retry_policy

    async def get(self, path: str, **params) -> dict: ...
    async def post(self, path: str, json: dict) -> dict: ...
    async def patch(self, path: str, json: dict) -> dict: ...
    async def delete(self, path: str) -> None: ...
    async def post_long_running(self, path: str, json: dict, poll_until: str) -> dict: ...
```

**Retry policy:**

- Exponential backoff con jitter: 1s, 2s, 4s, 8s (máx 5 intentos).
- Retry en: 429 (con `Retry-After`), 502, 503, 504.
- No retry en: 400, 401, 403, 404.
- Circuit breaker: abre tras 5 errores 5xx consecutivos en 60s.

### 2.3 Endpoints cubiertos

#### Workspaces (Groups)

| Tool | Endpoint | Scopes | MVP |
|------|----------|--------|-----|
| `list_workspaces` | `GET /groups?$filter=...&$top=...` | READ | ✅ |
| `get_workspace` | `GET /groups/{groupId}` | READ | ✅ |
| `create_workspace` | `POST /groups` | WRITE | ❌ (v2) |
| `delete_workspace` | `DELETE /groups/{groupId}` | ADMIN | ❌ (v2) |

#### Items

| Tool | Endpoint | Scopes | MVP |
|------|----------|--------|-----|
| `list_items` | `GET /workspaces/{workspaceId}/items?type=...` | READ | ✅ |
| `get_item` | `GET /workspaces/{workspaceId}/items/{itemId}` | READ | ✅ |
| `create_item` | `POST /workspaces/{workspaceId}/items` | WRITE | ✅ (vía `deploy_to_workspace`) |
| `delete_item` | `DELETE /workspaces/{workspaceId}/items/{itemId}` | WRITE | ❌ (v2, requiere elicitation fuerte) |

#### Datasets

| Tool | Endpoint | Scopes | MVP |
|------|----------|--------|-----|
| `list_datasets` | `GET /groups/{groupId}/datasets` | READ | ✅ |
| `get_dataset` | `GET /groups/{groupId}/datasets/{datasetId}` | READ | ✅ |
| `trigger_refresh` | `POST /groups/{groupId}/datasets/{datasetId}/refreshes` | WRITE | ✅ (vía `run_refresh`) |
| `cancel_refresh` | `POST /groups/{groupId}/datasets/{datasetId}/refreshes/cancel` | WRITE | ✅ |
| `get_refresh_history` | `GET /groups/{groupId}/datasets/{datasetId}/refreshes` | READ | ✅ |
| `update_datasource` | `PATCH /groups/{groupId}/datasets/{datasetId}/datasources/{datasourceId}` | WRITE | ✅ |
| `update_refresh_schedule` | `PATCH /groups/{groupId}/datasets/{datasetId}/refreshSchedule` | WRITE | ✅ |
| `take_over_dataset` | `POST /groups/{groupId}/datasets/{datasetId}/takeover` | WRITE | ✅ |
| `execute_queries` | `POST /groups/{groupId}/datasets/{datasetId}/queries` | READ | ✅ |

#### Deployment Pipelines

| Tool | Endpoint | Scopes | MVP |
|------|----------|--------|-----|
| `list_pipelines` | `GET /pipelines` | READ | ❌ (v2) |
| `deploy_pipeline` | `POST /pipelines/{pipelineId}/deploy` | WRITE | ❌ (v2) |
| `get_pipeline_stage` | `GET /pipelines/{pipelineId}/stages/{stageId}` | READ | ❌ (v2) |

#### Git Integration

| Tool | Endpoint | Scopes | MVP |
|------|----------|--------|-----|
| `connect_git` | `POST /workspaces/{workspaceId}/git/connect` | WRITE | ❌ (v3) |
| `commit_to_git` | `POST /workspaces/{workspaceId}/git/commit` | WRITE | ❌ (v3) |
| `update_from_git` | `POST /workspaces/{workspaceId}/git/update` | WRITE | ❌ (v3) |
| `get_git_status` | `GET /workspaces/{workspaceId}/git/status` | READ | ❌ (v3) |

#### Labels (Admin)

| Tool | Endpoint | Scopes | MVP |
|------|----------|--------|-----|
| `set_sensitivity_label` | `POST /admin/items/labels/bulkSet` | ADMIN | ❌ (v3) |

#### Capacity / Gateways

| Tool | Endpoint | Scopes | MVP |
|------|----------|--------|-----|
| `list_capacities` | `GET /capacities` | READ | ❌ (v2) |
| `list_gateways` | `GET /gateways` | READ | ❌ (v2) |
| `bind_dataset_to_gateway` | `PATCH /groups/{groupId}/datasets/{datasetId}/datasources/{datasourceId}` | WRITE | ✅ |

### 2.4 `refresh.py` — Manejo async de refresh

```python
# src/cloud/refresh.py
class RefreshManager:
    async def run_refresh(
        self,
        workspace_id: str,
        dataset_id: str,
        refresh_type: RefreshType = RefreshType.FULL,
        commit_mode: CommitMode | None = None,
        tables: list[str] | None = None,
        partitions: list[str] | None = None,
        wait: bool = True,
        timeout_ms: int = 1800000,
    ) -> RefreshResult: ...
```

**Estados:**

```
[Idle] --trigger--> [InProgress]
[InProgress] --success--> [Completed]
[InProgress] --failure--> [Failed]
[InProgress] --user_cancel--> [Disabled]
[InProgress] --timeout--> [Timeout]
[InProgress] --cancel_api--> [Cancelled]
```

**Auto-rollback de partición:** si la primera partición falla, cancelar el
refresh completo y devolver info al usuario. NO intentamos reparar.

### 2.5 `audit_cloud.py` — Audit log específico de cloud

Cada operación REST se loguea con:

- timestamp ISO8601
- tool_name
- args_hash (sha256 de args serializados)
- target (workspace_id, item_id, etc.)
- http_method + endpoint
- http_status
- duration_ms
- request_id (header `x-ms-request-id` para correlación con logs Microsoft)

Se persiste en `audit_log` table (mismo schema que orquestador, ver
[`01-orchestrator.md`](./01-orchestrator.md) §2.4).

---

## 3. Tool MVP: `run_refresh`

```yaml
tool_name: run_refresh
input_schema:
  type: object
  required: [workspace_id, dataset_id]
  properties:
    workspace_id: {type: string}
    dataset_id: {type: string}
    refresh_type:
      type: string
      enum: ["full", "automatic", "data_only", "calculate", "clearValues"]
      default: "full"
    commit_mode:
      type: string
      enum: ["transactional", "partialBatch"]
    tables: {type: array, items: {type: string}}
    partitions: {type: array, items: {type: string}}
    wait: {type: boolean, default: true}
    timeout_ms: {type: integer, default: 1800000}
output_schema:
  type: object
  properties:
    refresh_id: {type: string}
    status:
      type: string
      enum: ["InProgress", "Completed", "Failed", "Cancelled", "Timeout"]
    duration_ms: {type: integer}
    errors:
      type: array
      items:
        type: object
        properties:
          code: {type: string}
          message: {type: string}
    rollback_performed: {type: boolean}
errors:
  - auth_required
  - insufficient_scope
  - workspace_not_found
  - dataset_not_found
  - refresh_already_in_progress
  - timeout
  - credentials_expired  # diagnosticado por refresh_doctor
```

---

## 4. Errores tipados

```python
class CloudAuthError(PBIOrchestratorError):
    """Token inválido, expirado, scopes insuficientes."""

class CloudAPIError(PBIOrchestratorError):
    code: str  # ej: "ItemNotFound", "CapacityNotAssigned"
    http_status: int
    remediation_hint: str

class RefreshTimeout(CloudAPIError):
    """Refresh no completó en timeout_ms."""

class CredentialsExpired(CloudAPIError):
    """Diagnosticar con refresh_doctor + sugerir fix."""
```

**Diagnóstico automático (`refresh_doctor`):**

- 401 → token expirado, refresh.
- 403 + `RequestDisallowedByTenant` → tenant admin bloqueó la feature.
- 403 + `CapacityNotAssigned` → workspace no está en capacity Premium/Fabric.
- 400 + `RefreshRequestThrottled` → backoff + retry.
- 400 + `InvalidRefreshRequest` (gateway) → gateway caído, sugerir verificar.
- 400 + `DM_GatewayOutOfMemory` → reducir particiones o subir capacity.

---

## 5. Cloud audit log (`audit_cloud.py`)

El `audit.py` general (en `01-orchestrator.md` §2.4) persiste operaciones
de orquestación con HMAC chain. Las operaciones **cloud** (REST contra
Fabric) tienen requisitos adicionales que justifican un módulo
dedicado: redacción de secrets más agresiva, integración con el contexto
de la sesión (workspace_id, item_id), y trazabilidad de `request_id`
que Fabric devuelve.

### 5.1 Diseño

```python
# src/cloud/audit_cloud.py
from powerbi_orchestrator_mcp.orchestrator.audit import AuditLog, AuditEntry


class CloudAuditLog:
    """Wrapper sobre AuditLog para operaciones cloud.

    Reusa el audit log SQLite + HMAC chain del orquestador.
    Anade redacción específica de cloud + campos adicionales en payload.
    """

    def __init__(self, base_audit: AuditLog) -> None:
        self._audit = base_audit

    def record(
        self,
        operation: str,          # "workspace.list", "dataset.refresh", "deploy.create"
        args: dict[str, Any],    # argumentos del tool (ya redactados si PII)
        response_status: int,    # HTTP status code
        request_id: str | None,  # X-Microsoft-Request-Id de Fabric
        fabric_path: str,        # "/v1/workspaces/<id>/datasets/<id>/refreshes"
        workspace_id: str | None = None,
        item_id: str | None = None,
        dataset_id: str | None = None,
        effective_identity: dict | None = None,  # si RLS test
        result_status: str = "success",
        error_code: str | None = None,
        duration_ms: int = 0,
        extra_payload: dict | None = None,
    ) -> AuditEntry: ...

    def record_batch(self, operations: list[dict]) -> list[AuditEntry]: ...
```

### 5.2 Redacción obligatoria

Antes de persistir `args` y `extra_payload`, se aplica redacción:

| Pattern | Reemplazo | Razón |
|---------|-----------|-------|
| `Bearer [A-Za-z0-9._-]+` | `Bearer [REDACTED]` | JWT tokens |
| `code=[A-Za-z0-9_-]{20,}` (query params) | `code=[REDACTED]` | OAuth codes en URLs de refresh |
| `Server=[^;]+` (en connection strings) | `Server=[REDACTED]` | Connection strings |
| `Data Source=[^;]+` | `Data Source=[REDACTED]` | Idem |
| `Password=[^;]+` | `Password=[REDACTED]` | Credenciales |
| `eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+` | `[JWT_REDACTED]` | JWT completos fuera de headers |
| Emails en `effective_identity` | `<email_hash>` (sha256[:8]) | PII minimization |

**Test automatizado:** `tests/unit/test_audit_cloud_redaction.py` verifica
que 100 patrones conocidos (fixtures con samples de cada tipo) se
redactan correctamente. Si una redacción falla, el test falla y bloquea
el CI.

### 5.3 Payload schema

`payload_json` se serializa con este shape:

```python
class CloudAuditPayload(BaseModel):
    operation: str
    fabric_path: str
    request_id: str | None
    response_status: int
    workspace_id: str | None
    item_id: str | None
    dataset_id: str | None
    effective_identity_hash: str | None  # sha256[:8] del email, no el email
    error_code: str | None
    duration_ms: int
    extra: dict[str, Any]  # operation-specific (ej: refresh_type, partition count)
```

### 5.4 Integración con `audit.py` general

`CloudAuditLog.record()` llama internamente a `AuditLog.insert()` con:

- `tool_name`: `"cloud:" + operation` (ej: `"cloud:dataset.refresh"`)
- `target_id`: el `target_id` calculado según §2.7 de `01-orchestrator.md`
  para el tipo `fabric_workspace:<workspace_id>`.
- `result_status`: `"success"`, `"failed"`, `"declined"`, etc.
- `payload_json`: el `CloudAuditPayload` serializado + redactado.

Esto significa que **toda fila cloud aparece en el mismo `audit_log`
SQLite que las filas de orquestación**, mantiene la misma HMAC chain,
y `verify()` funciona transparentemente sobre todo.

### 5.5 Retention y querying

- Misma política de rotación que el audit log general (ver §2.10).
- Querying específico de cloud: `SELECT * FROM audit_log WHERE tool_name LIKE 'cloud:%'`
  indexado via `CREATE INDEX idx_audit_tool_name ON audit_log(tool_name)` (añadido al
  schema si no existe).
- Retention: opt-in via `PBI_AUDIT_CLOUD_RETENTION_DAYS` (default 365 días
  para filas cloud; las orquestación更重要 infinito hasta rotación manual).

### 5.6 Acceptance criteria

- [ ] Test con 100 patrones de secrets verifica redacción 100%.
- [ ] `CloudAuditLog.record` produce fila con `tool_name="cloud:<operation>"`.
- [ ] `verify()` de audit log funciona con filas mixtas (orchestration + cloud).
- [ ] Email en `effective_identity` aparece hasheado en `payload_json`, no en claro.
- [ ] Performance: 1000 `record()` consecutivos en <2s.

---

## 6. Concurrency limits y rate limiting

Fabric REST API tiene rate limits documentados (≈200 requests/min por
tenant; throttling 429 con header `Retry-After`). El orquestador
implementa **token bucket por sesión** + **circuit breaker** para no
excederlos y degradar con gracia bajo carga sostenida.

### 6.1 Configuración

```python
# src/cloud/concurrency.py
from dataclasses import dataclass

@dataclass(frozen=True)
class FabricRateLimitConfig:
    requests_per_minute: int = 200          # soft limit Fabric
    concurrent_requests: int = 10            # hard limit orquestador
    burst_capacity: int = 30                 # permite picos cortos
    circuit_breaker_threshold: int = 5       # errores 5xx consecutivos para abrir
    circuit_breaker_cooldown_s: int = 60     # cuánto permanece abierto
    circuit_breaker_half_open_requests: int = 3  # requests de prueba en half-open
```

**Overrides por env var:**

| Variable | Default | Efecto |
|----------|---------|--------|
| `PBI_FABRIC_RPM_LIMIT` | 200 | RPM por sesión |
| `PBI_FABRIC_CONCURRENT_LIMIT` | 10 | Requests paralelas |
| `PBI_FABRIC_CIRCUIT_BREAKER_THRESHOLD` | 5 | Errores 5xx consecutivos |
| `PBI_FABRIC_CIRCUIT_BREAKER_COOLDOWN_S` | 60 | Cooldown del circuit breaker |

### 6.2 Token bucket implementation

```python
class TokenBucket:
    """Thread-safe (asyncio.Lock) token bucket."""

    def __init__(self, rpm: int, burst: int) -> None:
        self._rpm = rpm
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, timeout_s: float = 30.0) -> bool:
        """Espera hasta tener un token. Retorna False si timeout."""
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                wait_s = (1.0 - self._tokens) / (self._rpm / 60.0)
                if wait_s > timeout_s:
                    return False
                self._lock.release()
                try:
                    await asyncio.sleep(wait_s)
                finally:
                    await self._lock.acquire()
```

### 6.3 Circuit breaker

```python
class CircuitBreakerState(str, Enum):
    CLOSED = "closed"          # normal
    OPEN = "open"              # failing fast
    HALF_OPEN = "half_open"    # probando si volvió


class CircuitBreaker:
    def __init__(self, threshold: int, cooldown_s: int, half_open_requests: int): ...
    async def call(self, fn: Callable[[], Awaitable[T]]) -> T: ...
    def state(self) -> CircuitBreakerState: ...
```

**Comportamiento:**

| State | Comportamiento |
|-------|----------------|
| `closed` | Normal; cuenta errores 5xx consecutivos. |
| `open` | Cualquier request retorna `CircuitBreakerOpenError` inmediatamente. Después de `cooldown_s`, transiciona a `half_open`. |
| `half_open` | Permite hasta `half_open_requests` requests. Si todas succeed → `closed`. Si alguna falla → `open` de nuevo. |

### 6.4 Comportamiento bajo presión

**Si el token bucket está saturado (espera >5s por token):**

- `apply_plan` con `wait_for_capacity=true` (default): espera hasta tener token.
- `apply_plan` con `wait_for_capacity=false`: elicita al usuario con opciones
  (esperar, abortar, reducir concurrency_limit y reintentar).

**Si el circuit breaker está `open`:**

- Toda request retorna `CircuitBreakerOpenError` con
  `remediation_hint="Fabric REST experimentando problemas. Retry en {cooldown_remaining}s"`.
- `apply_plan` en curso: elicita con opciones (esperar recovery, abort, rollback).

**Métricas expuestas** (en `/metrics` Prometheus cuando esté habilitado, o en
`structlog` events):

```
fabric_requests_inflight          # gauge
fabric_requests_total             # counter (labels: operation, status_class)
fabric_429_total                  # counter
fabric_5xx_total                  # counter
fabric_circuit_breaker_state      # gauge (0=closed, 1=half_open, 2=open)
fabric_token_bucket_wait_ms       # histogram (cuánto espera cada request)
```

### 6.5 Excepciones: long-running operations

`run_refresh` y `cancel_refresh` son **long-running** (pueden tardar
minutos). NO cuentan contra el token bucket principal; usan una
conexión dedicada que polling-ea el status sin consumir budget.

```python
LONG_RUNNING_OPERATIONS = {
    "dataset.refresh",
    "dataset.cancel_refresh",
    "pipeline.deploy",
    "git.commit_to_workspace",
}
```

Estas operaciones tienen su propio budget reducido (default 5/min) y
timeout explícito por tool.

### 6.6 Acceptance criteria

- [ ] 1000 requests rápidas (sin throttling) completan sin errores.
- [ ] Simulación de 250 RPM durante 60s: ≤5 requests reciben 429 (resto pasan).
- [ ] Circuit breaker abre tras 5 errores 5xx consecutivos; cierra tras cooldown exitoso.
- [ ] `CircuitBreakerOpenError` retorna con `remediation_hint` clara.
- [ ] Métricas Prometheus exportadas (o structlog events).
- [ ] Test con mock de Fabric retornando 429: orquestador respeta `Retry-After`.

---

## 7. Acceptance criteria

- [ ] Auth funciona con interactive + SPN + managed identity.
- [ ] Token cache: nunca pedir re-auth dentro de la misma sesión.
- [ ] Elicitation obligatoria para write operations en workspaces con
  tag `production: true` en metadata local.
- [ ] Retry exponencial: cubre 429, 502, 503, 504 con backoff correcto.
- [ ] Circuit breaker: abre tras 5 errores 5xx consecutivos.
- [ ] `run_refresh` con `wait=true` completa el ciclo async hasta
  Completed/Failed/Cancelled.
- [ ] `cancel_refresh` cancela un refresh InProgress y devuelve status.
- [ ] `execute_queries` con `EffectiveIdentity` para RLS testing funciona.
- [ ] `take_over_dataset` recupera datasets huérfanos.
- [ ] Audit log: 100% de operaciones cloud logueadas con HMAC chain.
- [ ] 0 secretos en logs (test automatizado busca patterns de token).
- [ ] Latencia p95 `list_workspaces` <500ms.

## 8. Out of scope (MVP)

- ❌ Crear / borrar workspaces (v2).
- ❌ Deployment Pipelines completos (v2).
- ❌ Git Integration (v3).
- ❌ Sensitivity labels admin (v3).
- ❌ Capacity management (v2).
- ❌ Gateway management detallado (solo bind básico en MVP).
- ❌ Dataflow Gen2 / Notebook operations (no son target de MVP).
- ❌ Real-time / Push datasets.

## 9. Riesgos

| Riesgo | Mitigación |
|--------|-----------|
| Token expira mid-operation | Refresh transparente de Azure Identity; reintentar UNA vez si 401. |
| Scope insuficiente en SPN | Documentar scopes mínimos por tool en README; elicitation previa si falta. |
| API rate limit | Retry exponencial + circuit breaker + token bucket (ver §6). |
| Refresh timeout inesperado | `cancel_refresh` + rollback de partición + audit log `partial`. |
| Tenant policy bloquea operación | Elicitation con remediation clara ("ask tenant admin to enable X"). |
| Refresh credentials expired | `refresh_doctor` automático + sugerencia de fix. |
| Circuit breaker abierto por outage externo | Elicitación al usuario; `apply_plan` espera recovery o aborta. |

## 10. Specs relacionados

- [`01-orchestrator.md`](./01-orchestrator.md) — elicitation + audit + crash recovery
- [`tools/deploy-to-workspace.md`](./tools/deploy-to-workspace.md) — usuario principal
- [`docs/architecture.md`](../docs/architecture.md) §2.4 + §5 (security)
