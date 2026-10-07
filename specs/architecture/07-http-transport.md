# Spec: HTTP Transport + Entra OAuth (Capa 0 — transport)

> Diseño del transporte HTTP para el servidor MCP, que reemplaza
> (u ofrece en paralelo a) el transporte stdio actual. Habilita
> deployments remotos: contenedor en cloud, multi-tenant, LLM
> clients que no soportan stdio, autenticación empresarial vía
> Entra ID (Azure AD).

**Status:** v0.1 (spec)
**Prioridad:** P3 — habilitador de deployments serios; no bloqueante
para v1.0 pero necesario antes de ofrecer el paquete como SaaS
interno
**Responsable:** Bastian Berrios
**Depende de:** [`SPEC.md`](../../SPEC.md) §6 (arquitectura 6 capas),
[`01-orchestrator.md`](../01-orchestrator.md) §2.6 (state machine)
**Habilita:** correr el orquestador en Azure Container Apps, AKS, o
VM expuesto detrás de API Management, con auth Entra ID

---

## Cambios respecto a v0.0

N/A (spec inicial).

---

## 1. Objetivo

Agregar un transporte HTTP al `FastMCP` server que:

1. Hable **Streamable HTTP** (MCP spec 2025-06+), con SSE como fallback
   deprecated.
3. Acepte tokens **Entra ID** (Azure AD) bearer en cada request.
4. Valide **audience** y **scope** del token antes de despachar al
   orquestador.
5. Mantenga **stdio** como transporte default y no rompa clientes
   existentes (Claude Desktop, VS Code, Cursor).

**Métricas de éxito:**

- Cliente MCP HTTP (ej. MCP Inspector, VS Code con HTTP transport
  preview) puede conectarse al endpoint, presentar token, recibir
  la lista de tools.
- Un request sin token o con token inválido retorna 401 con
  WWW-Authenticate header correcto.
- Un request con token válido pero scope insuficiente retorna 403.
- Latencia P95 del handshake < 200ms en LAN; el orquestador no
  añade overhead perceptible vs stdio.

---

## 2. MCP transports — qué elegir

### 2.1 Estado de la spec MCP

| Transport | Status | Notas |
|---|---|---|
| **stdio** | Estable, default actual | Solo local. Limitado a 1 cliente por proceso. |
| **SSE (Server-Sent Events)** | **Deprecated** en spec 2025-06-18 | Mantenido para retrocompat. No usar para greenfield. |
| **Streamable HTTP** | **Recomendado** desde spec 2025-06-18 | Reemplaza SSE. Single endpoint, eventos via SSE o response chunked. |

### 2.2 Decisión: Streamable HTTP

- Es lo que las versiones actuales de `mcp[cli]>=1.10` soportan
  nativamente (`FastMCP` tiene `transport="streamable-http"`).
- Single endpoint `POST /mcp` con respuestas JSON-RPC 2.0.
- Opcionalmente `GET /mcp` para abrir stream SSE de server-initiated
  notifications (elicitation, sampling).
- Compatible con MCP Inspector, future VS Code, Claude.ai cloud.

**SSE como fallback:** no implementar. Si un cliente solo soporta
SSE, es un bug del cliente — MCP spec 2025-06 es claro.

---

## 3. Authentication: Entra ID

### 3.1 Opciones evaluadas

| Opción | Pros | Contras |
|---|---|---|
| A. **Entra ID via MSAL Python** (`msal`) | Estándar Microsoft, soporta OAuth2 + OIDC + managed identity | Dependencia nueva (~5MB). Más código. |
| B. **Azure Identity SDK** (`azure-identity`) | Ya en deps (para Fabric). Reusa. | API más alto-nivel, menos control sobre el JWT validation. |
| C. **JWT validation manual con `PyJWT` + JWKS de Entra** | Cero deps nuevas, control total | Más código para mantener (key rotation, claim mapping). |
| D. **API Management delante del servidor** (MCP no ve el token) | El servidor MCP queda stateless, auth delegada | Costo de infra APIM, no resuelve single-tenant. |

### 3.2 Decisión recomendada: opción C (PyJWT + JWKS)

Justificación:

- El orquestador solo necesita **validar** tokens, no **emitirlos**
  ni manejar el flow OAuth2 (eso vive en APIM o un IdP externo).
- `PyJWT[crypto]` es ~50KB; mucho menos que `msal` o engrosar
  `azure-identity`.
- Permite control fino: audience, scope, issuer, exp, nbf, iat.
- JWKS endpoint de Entra: `https://login.microsoftonline.com/common/discovery/v2.0/keys`
  (o tenant-specific para single-tenant).

### 3.3 Claims a validar

| Claim | Validación | Por qué |
|---|---|---|
| `iss` | Debe matchear `https://login.microsoftonline.com/<tenant-id>/v2.0` | Evitar tokens de otro tenant |
| `aud` | Debe matchear `api://powerbi-orchestrator-mcp` (configurable via env) | Evitar token-replay contra otro servicio |
| `exp` | < now() | Standard |
| `nbf` | ≤ now() | Standard |
| `iat` | < now() + skew | Standard |
| `tid` | Matchea tenant esperado | Single-tenant |
| `scp` o `roles` | Contiene scope requerido (`Tools.Read` / `Tools.Write`) | RBAC granular |
| `oid` | Logged en audit log | Para correlación |

### 3.4 Scopes (RBAC)

```text
Tools.Read    — listar tools, llamar tools de solo lectura
                 (connect_target, powerbi_health, audit_model_and_report)
Tools.Write   — ejecutar tools que mutan estado
                 (apply_plan, deploy_to_workspace, run_refresh,
                 create_semantic_model_from_schema, …)
Tools.Admin   — listar audit log, rotar HMAC key, etc.
```

Default: si el token no trae scope explícito, denegar (fail closed).

### 3.5 Token source

| Header | Caso |
|---|---|
| `Authorization: Bearer <jwt>` | Estándar |
| `X-Forwarded-Access-Token` | Cuando APIM está delante y mints un nuevo JWT después de validar sesión |

El servidor acepta ambos, prioriza `Authorization`.

---

## 4. Threat model

### 4.1 Actores

- **Atacante externo**: sin token válido. Puede hacer brute force
  contra el endpoint.
- **Cliente legítimo**: usuario con permisos Entra ID, scope
  asignado.
- **Cliente over-privileged**: usuario con scope `Tools.Write` que
  intenta acciones no autorizadas (e.g., deploy a workspace que no
  es suyo). El servidor DEBE chequear permisos a nivel de tool
  además del scope global.
- **Insider**: colaborador con acceso al repo que intenta plantar
  un backdoor en el código de auth. Mitigado por code review +
  signed wheels + audit log.

### 4.2 Amenazas

| Amenaza | Mitigación |
|---|---|
| Token replay | `exp` + `nbf` validation; rate limit por `oid` |
| Token theft desde logs | Redacción en structlog (ya cubierta por `06-engine-error-contracts.md` §6); secrets nunca en logs |
| Audience confusion (token de Graph API usado contra MCP) | Validar `aud` estricto |
| Brute force del endpoint | Rate limit via APIM o middleware |
| CSRF (Streamable HTTP no tiene cookies) | N/A — bearer-only |
| SSRF via DAX queries / file paths del PBIP | Ya cubierto por `validation/dax_linter.py` + path allowlist en `connect_target` |
| JWT alg=none attack | `PyJWT` rechaza `alg=none` por default; validar `alg` explícitamente contra whitelist `{RS256}` |
| JWKS endpoint comprometido | Cachear JWKS por ≤1h con refresh on `kid` miss; alertas de drift |

---

## 5. Client compatibility

| Cliente | Streamable HTTP | Entra ID | Notas |
|---|---|---|---|
| Claude Desktop | Preview, opt-in flag | No built-in | Workaround: tunnel vía ngrok + APIM delante |
| VS Code + Copilot | Preview (1.95+) | Sí (Microsoft Account) | Soporte nativo esperado |
| Cursor | No aún | No | Pendiente |
| Claude.ai cloud | Sí | Sí | MCP servers remotos en preview |
| MCP Inspector | Sí | Token manual | Ideal para dev |
| Custom clients | Sí si implementan MCP 2025-06 | Custom | SDKs disponibles en TS, Python, Go |

**Implicación:** durante el MVP del HTTP transport, el primary
cliente será MCP Inspector + Claude.ai cloud. Claude Desktop
queda en stdio (su default) hasta que Anthropic agregue HTTP
transport estable.

---

## 6. Migration strategy: stdio default, HTTP opt-in

```bash
# stdio (default, sin cambios)
powerbi-orchestrator-mcp --start

# HTTP (nuevo)
powerbi-orchestrator-mcp --transport http \
                         --http-host 0.0.0.0 \
                         --http-port 8000 \
                         --http-entra-tenant-id <tenant> \
                         --http-entra-audience api://powerbi-orchestrator-mcp
```

**No breaking changes:** clientes stdio existentes siguen funcionando
sin cambios. El flag `--transport` es nuevo.

**Config por env vars** (alternativa):

```bash
PBI_TRANSPORT=http
PBI_HTTP_HOST=0.0.0.0
PBI_HTTP_PORT=8000
PBI_ENTRA_TENANT_ID=...
PBI_ENTRA_AUDIENCE=api://...
PBI_ENTRA_REQUIRED_SCOPE=Tools.Write
```

---

## 7. Implementation sketch

```python
# src/powerbi_orchestrator_mcp/orchestrator/server.py
from mcp.server.fastmcp import FastMCP
import jwt
from jwt import PyJWKClient

def create_server(transport: str = "stdio", **http_kwargs) -> None:
    server = FastMCP("powerbi-orchestrator-mcp")

    if transport == "http":
        # Set up auth middleware
        jwks_client = PyJWKClient(
            f"https://login.microsoftonline.com/{http_kwargs['entra_tenant_id']}/discovery/v2.0/keys"
        )

        @server.middleware
        async def auth_middleware(request, call_next):
            token = _extract_bearer(request)
            if not token:
                return Response(
                    status=401,
                    headers={"WWW-Authenticate": 'Bearer realm="powerbi-orchestrator-mcp"'},
                )
            try:
                signing_key = jwks_client.get_signing_key_from_jwt(token)
                claims = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["RS256"],
                    audience=http_kwargs["entra_audience"],
                    issuer=f"https://login.microsoftonline.com/{http_kwargs['entra_tenant_id']}/v2.0",
                )
            except jwt.PyJWTError as e:
                return Response(status=401, body=str(e))

            # Attach to request context for tool handlers
            request.state.claims = claims
            return await call_next(request)

        server.settings.host = http_kwargs.get("host", "127.0.0.1")
        server.settings.port = http_kwargs.get("port", 8000)
        server.run(transport="streamable-http")
    else:
        server.run(transport="stdio")
```

---

## 8. Acceptance criteria

- [ ] Flag `--transport http` arranca el server en HTTP.
- [ ] Server habla Streamable HTTP spec-compliant (MCP 2025-06+).
- [ ] Sin token: retorna 401 con `WWW-Authenticate: Bearer`.
- [ ] Token con scope insuficiente: retorna 403.
- [ ] Token con `aud` incorrecto: retorna 401.
- [ ] Token expirado: retorna 401.
- [ ] Audit log registra `oid` del caller en cada tool invocation.
- [ ] JWKS cacheado por ≤1h, refresh on `kid` miss.
- [ ] Test E2E del handshake con token válido contra MCP Inspector.
- [ ] Documentación en `docs/http-transport.md` con ejemplos curl.
- [ ] Threat model revisado y aceptado por el maintainer.
- [ ] `pyproject.toml` agrega `pyjwt[crypto]>=2.8` a dependencies.

---

## 9. Out of scope (MVP del HTTP transport)

- ❌ Single Sign-On flow propio (deferir a APIM o librería externa).
- ❌ Refresh tokens / session management (stateless por JWT).
- ❌ Multi-tenant data isolation más allá del scope (cada tool
  chequea el PBIP/workspace pasado; no asume tenant implícito).
- ❌ Streaming de output de subprocess via SSE (cubierto por NDJSON
  a stdout del engine; el orquestador solo agrega JSON final).
- ❌ CORS policy (asumimos que corre detrás de APIM que lo maneja).
- ❌ TLS termination (asumimos que el reverse proxy / APIM lo
  maneja; el server HTTP habla plaintext en localhost).

---

## 10. Riesgos

| Riesgo | Mitigación |
|---|---|
| FastMCP cambia la API de middlewares entre versiones | Pin `mcp>=1.10,<2.0` (ya en deps). Test de contrato en `tests/integration/test_http_transport.py`. |
| Token validation overhead afecta latencia | Cachear `oid → claims` por 60s en memoria; JWKS por 1h. |
| JWKS endpoint de Entra down | Cachear último JWKS válido hasta 24h; fail-closed después (denegar nuevos tokens, no cortar sesiones activas). |
| Streamable HTTP spec cambia antes de estabilizar | Pin versión de MCP spec en docs; test contra MCP Inspector de la misma versión. |
| Cliente (e.g. Claude Desktop) no soporta HTTP | Mantener stdio como default; documentar workarounds (APIM, ngrok). |
| Token de scope `Tools.Write` permite deploy a workspace cualquiera | Tool-level check: `deploy_to_workspace` verifica que el `workspace_id` está en la lista de workspaces del `oid`. |

---

## 11. Specs relacionados

- [`01-orchestrator.md`](../01-orchestrator.md) §2.6 — state machine
  del orquestador; los handlers HTTP deben respetar las mismas
  invariantes.
- [`02-cloud-fabric.md`](../02-cloud-fabric.md) §5 — cloud audit log;
  el HTTP transport debe loggear el `oid` además de los campos
  existentes.
- [`06-engine-error-contracts.md`](../06-engine-error-contracts.md) —
  errores que los adapters emiten; el HTTP layer debe traducirlos a
  códigos HTTP correctos (400 vs 500).
- [`docs/architecture.md`](../../docs/architecture.md) §2 — diagrama
  de capas; este spec agrega la "Capa 0" (transport).