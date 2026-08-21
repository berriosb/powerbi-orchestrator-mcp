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

## 5. Acceptance criteria

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

## 6. Out of scope (MVP)

- ❌ Crear / borrar workspaces (v2).
- ❌ Deployment Pipelines completos (v2).
- ❌ Git Integration (v3).
- ❌ Sensitivity labels admin (v3).
- ❌ Capacity management (v2).
- ❌ Gateway management detallado (solo bind básico en MVP).
- ❌ Dataflow Gen2 / Notebook operations (no son target de MVP).
- ❌ Real-time / Push datasets.

## 7. Riesgos

| Riesgo | Mitigación |
|--------|-----------|
| Token expira mid-operation | Refresh transparente de Azure Identity; reintentar UNA vez si 401. |
| Scope insuficiente en SPN | Documentar scopes mínimos por tool en README; elicitation previa si falta. |
| API rate limit | Retry exponencial + circuit breaker; warning al usuario si sostenido. |
| Refresh timeout inesperado | `cancel_refresh` + rollback de partición + audit log `partial`. |
| Tenant policy bloquea operación | Elicitation con remediation clara ("ask tenant admin to enable X"). |
| Refresh credentials expired | `refresh_doctor` automático + sugerencia de fix. |

## 8. Specs relacionados

- [`01-orchestrator.md`](./01-orchestrator.md) — elicitation + audit
- [`tools/deploy-to-workspace.md`](./tools/deploy-to-workspace.md) — usuario principal
- [`docs/architecture.md`](../docs/architecture.md) §2.4 + §5 (security)
