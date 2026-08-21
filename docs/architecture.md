# Architecture — powerbi-orchestrator

> Arquitectura técnica detallada. Diagramas, contratos cross-cutting,
> decisiones y trade-offs.

**Status:** v0.1
**Audiencia:** devs senior que van a implementar o mantener el server.

---

## 1. Vista de capas

```
┌──────────────────────────────────────────────────────────────────────┐
│                  AGENTE (Claude / Copilot / Hermes)                  │
│              prompt NL → secuencia de tool calls                     │
└───────────────────────────────�──────────────────────────────────────┘
                                │ JSON-RPC sobre stdio (MCP 2025-06-18)
┌───────────────────────────────▼──────────────────────────────────────┐
│  CAPA 6 · ORQUESTACIÓN                                               │
│  ─────────────────────────────────────────────────────────────────   │
│  Responsabilidad: recibir tool calls NL, descomponerlos en un        │
│  Plan versionable, ejecutarlo delegando a las capas internas,        │
│  validar entre steps y revertir atómicamente ante cualquier fallo.   │
│                                                                       │
│  Componentes:                                                         │
│    • server.py        — FastMCP entrypoint, registra tools           │
│    • planner.py       — NL → Plan (YAML declarativo)                 │
│    • context.py       — estado de sesión (target, engines, undo)     │
│    • audit.py         — log HMAC-chained a SQLite                    │
│    • tools/           — 35 tools de alto nivel                       │
│                                                                       │
│  NO implementa primitivas — solo delega y orquesta.                  │
└─────┬───────────────┬───────────────�─────────────────┬──────────────┘
      │ delegate      │ delegate      │ delegate        │ delegate
      ▼               ▼               ▼                 ▼
┌──────────┐    ┌──────────┐   ┌─────────────┐   ┌──────────────┐
│ C1 Modelo│    │ C2 Reporte│  │ C3 Nube     │   │ C5 Viz/UX    │
│          │    │           │  │             │   │              │
│ delegado │    │ delegado  │  │ PROPIO      │   │ PROPIO       │
│          │    │           │  │             │   │              │
│ → powerbi│    │ → superbi │  │ → REST API  │   │ → registry   │
│   -model-│    │   -mcp    │  │ → XMLA      │   │ → WCAG       │
│   ing-   │    │ → skills- │  │ → Azure     │   │ → suggester  │
│   mcp    │    │   for-    │  │   Identity  │   │ → layout     │
│ → te CLI │    │   fabric  │  │ → git       │   │ → theme      │
│ → dscmd  │    │ → pbip-   │  │   integr.   │   │ → story-     │
│          │    │   validator│ │             │   │   telling    │
└──────────┘    └──────────┘   └─────────────┘   └──────────────┘
                                  │
                                  ▼
                       ┌─────────────────────┐
                       │ C4 Validación       │
                       │                     │
                       │ PROPIA + delega     │
                       │                     │
                       │ → BPA (te)          │
                       │ → DAX linter propio │
                       │ → regression runner │
                       │ → WCAG parser       │
                       │ → pre-deploy gate   │
                       │ → model diff        │
                       └─────────────────────┘
```

---

## 2. Responsabilidades por capa

### 2.1 Capa 6 · Orquestación (este servidor)

**Inputs:** tool calls del agente (JSON-RPC).
**Outputs:** `structuredContent` (JSON Schema) + opcional `artifact` (path).

**Decisiones arquitectónicas:**

- **Planes YAML versionables**: cada `plan_change` produce un YAML que se puede
  commitear a Git y revisar en PR. Esto habilita `apply_plan` con audit trail
  y permite a un humano revisar antes de ejecutar.

- **Rollback atómico por step, no por plan**: si el step 3 de 7 falla,
  rollback del step 2 (no de los 7). El plan completo se mantiene; solo el
  `rollback_steps_executed` se llena.

- **Elicitation MCP 2025-06-18** para operaciones riesgosas. Server implementa
  el rol "server" de elicitation; el cliente decide si muestra UI o aborta.

- **Context de sesión persistente** dentro del proceso del server (no entre
  sesiones — eso lo resuelve el agente con su propio memory/vault). Permite
  cachear metadata del modelo conectado para no re-leer TMDL en cada tool.

- **Audit log HMAC-chained**: cada fila `audit_log` incluye `prev_hash` y
  `row_hash`. Cualquier tampering rompe la cadena y un verificador lo detecta.

### 2.2 Capa 1 · Modelo semántico (delegada)

**Engines:**

| Engine | Tipo | OS | Uso |
|--------|------|----|----|
| `powerbi-modeling-mcp` | MCP subprocess | Win/Mac/Linux | TOM/TMDL completo, conexión a PBI Desktop / Fabric / PBIP |
| `te` (Tabular Editor CLI) | Binario subprocess | Win/Mac/Linux | BPA, diff, test, refresh, deploy, VertiPaq |
| `dscmd.exe` (DAX Studio CLI) | Binario subprocess | Windows only | Trace FE/SE real, server timings |

**Por qué delegar**: reimplementar TOM es absurdo. La cobertura de
`powerbi-modeling-mcp` oficial + TE es imbatible y battle-tested.

### 2.3 Capa 2 · Reporte (delegada + coordinación)

**Engines:**

| Engine | Tipo | OS | Uso |
|--------|------|----|----|
| `superbi-mcp` (cyphonica) | MCP subprocess | Windows only | 490 tools; edición directa `.pbix` cerrado, PBIR completo, M, propagación de renames. License FSL (compatible con uso interno). |
| `skills-for-fabric/powerbi-report-authoring` (Microsoft) | Skill + `pbip-validator` CLI | Cross-platform | LLM escribe PBIR con guía; validador offline preflight. |
| Script propio (cross-platform) | Python | Cross-platform | Regex/JSON patches sobre PBIR cuando no hay skill ni Desktop. |

**Detección dinámica**: `connect_target` testea cada engine y elige el mejor
disponible. `superbi-mcp` > skill-for-fabric > script propio.

### 2.4 Capa 3 · Nube Fabric / Power BI Service (propia)

**Implementación:**

- Cliente REST sobre `httpx` async contra `https://api.fabric.microsoft.com/v1`.
- Subset XMLA opcional para refresh enhanced, deploy, scripting.
- `azure-identity` `DefaultAzureCredential` para auth.
- Retry exponencial con jitter; circuit breaker en errores 5xx sostenidos.

**Endpoints cubiertos** (lista completa en `specs/02-cloud-fabric.md`):

- Workspaces (CRUD + list + getGroups con filtros).
- Items (CRUD + list por tipo).
- Datasets (refresh, getRefreshHistory, cancelRefresh, takeOver, updateDatasource, updateRefreshSchedule).
- Deployment Pipelines (create, assign, deploy, getOperations).
- Git Integration (connect, commit, update, getStatus).
- Labels (admin bulk-set).
- Capacity / Gateways (lectura).

### 2.5 Capa 4 · Validación y testing (propia + delegación)

**Checks propios (Python puro):**

- `DAXLinter` — regex/AST-ish para anti-patterns (FILTER-not-ISFILTER, nested
  CALCULATE, `/` instead of DIVIDE, IFERROR, EARLIER, SUMMARIZE-for-aggregation,
  blank-suppressing `+ 0`, hallucinated function names).
- `WCAGAuditor` — parsea PBIR JSON, valida: alt text presente, tab order
  lógico, decorativos con orden -1, contraste declarado ≥4.5:1 (texto) o
  ≥3:1 (grande), marcadores en líneas, no color-only encoding.
- `ModelDiffer` — diff TMDL antes/después, clasifica breaking changes.
- `PreDeployGate` — evalúa findings contra umbrales configurables.

**Delegados:**

- `te bpa` con ruleset oficial Tabular Editor.
- `te test` para DAX regression.

### 2.6 Capa 5 · Visualización / UX (propia, diferenciador)

**Módulos:**

- `VisualRegistry` — JSON con 52+ tipos de visual nativos + custom certified,
  sus propiedades válidas, roles de datos aceptados, restricciones.
- `VisualSuggester` — dado KPI semantic type + data shape + audiencia,
  recomienda visual primario + alternativas + justificación.
- `LayoutOptimizer` — grid + alineación + jerarquía + gaps; soporte mobile.
- `ThemeGenerator` — genera `theme.json` PBIR-compatible con paleta validada
  (colorblind-safe: IBM / Okabe-Ito / Viridis).
- `StorytellingScorer` — heurístico: densidad, jerarquía visual, narrativa,
  mobile-readiness. Sin análisis de datos real hasta v3.
- `PerformanceBudget` — estima coste sin ejecutar: nº visuales/página,
  complejidad de measures referenciados, presencia de anti-patterns DAX.

---

## 3. Comunicación entre capas

```
tool_call → Capa 6 → Capa 1/2/3/4/5 → subprocess / httpx / internal
                                  ↓
                            resultado tipado (Pydantic)
                                  ↓
                            Capa 6 valida + agrega contexto
                                  ↓
                            structuredContent al agente
```

**Contratos:**

- Cada engine expone una interfaz Python tipada (`Protocol`).
- Inputs/outputs son Pydantic models compartidos (`src/models/`).
- Errores siguen jerarquía `PBIOrchestratorError` con `code` + `remediation_hint`.

---

## 4. Ciclo de vida de un plan

```
1. plan_change(intent="renombrar Customer[ID] → CustomerKey")
   └─ planner.PlanBuilder.run()
      ├─ detectar target activo (de context)
      ├─ buscar objetos candidatos via engine (C1)
      ├─ calcular impact: deps en DAX, refs en M, bindings en PBIR
      ├─ estimar risk_score
      └─ devolver Plan YAML + rollback_steps

2. elicitation (si risk_score > threshold)
   └─ MCP 2025-06-18 elicitation.request → cliente → usuario

3. apply_plan(plan_id, dry_run=true | false)
   └─ orchestrator.PlanExecutor.run()
      ├─ snapshot: git tag + tar backup PBIP
      ├─ para cada step:
      │  ├─ ejecutar via engine adapter
      │  ├─ capturar changed_files
      │  ├─ ejecutar validators (C4)
      │  └─ si falla → ejecutar rollback_step + abort
      ├─ emitir audit_log entry
      └─ devolver result + artifacts
```

---

## 5. Manejo de errores

### 5.1 Jerarquía

```python
class PBIOrchestratorError(Exception):
    code: str
    message: str
    remediation_hint: str

class ModelingEngineError(PBIOrchestratorError): ...
class ReportEngineError(PBIOrchestratorError): ...
class CloudAuthError(PBIOrchestratorError): ...
class CloudAPIError(PBIOrchestratorError): ...
class ValidationError(PBIOrchestratorError): ...
class VizRuleViolation(PBIOrchestratorError): ...
class RollbackError(PBIOrchestratorError): ...
```

### 5.2 Sanitización de outputs

- Redacción automática de: tokens, connection strings, emails admin.
- Warning explícito cuando un DAX query devuelve >1000 filas.
- Audit log separado de logs de aplicación (no se mezcla PII con debugging).

---

## 6. Performance

- **Cache de metadata** entre tools del mismo session (model schema, visual registry).
- **Subprocess async** con `asyncio.create_subprocess_exec` (no bloquea event loop).
- **Sampling** en `audit_model_and_report` para modelos >500 measures
  (configurable, default 100% pero warning si >30s).
- **Streaming** para outputs grandes: `structuredContent` se emite completo,
  pero `artifact` (ej: data dictionary HTML) se escribe a path y se devuelve
  solo la referencia.

---

## 7. Seguridad

### 7.1 Threat model

| Amenaza | Mitigación |
|---------|-----------|
| Agente publica a workspace equivocado | Elicitation obligatoria en writes a prod; `--allow-prod` flag. |
| LLM filtra PII via DAX query | Warning automático >N filas; opt-in para devolver resultados. |
| Token leakage en logs | Sanitización + audit log separado. |
| Rollback fallido | `RollbackError` → elicitation pidiendo acción manual con paths exactos. |
| Model tampering | Audit log HMAC-chained; verificador detecta cambios. |

### 7.2 Defense in depth

1. **Elicitation** (user-in-the-loop).
2. **`--readonly` flag** global.
3. **`--allow-prod` flag** para escrituras a producción (separado).
4. **Audit log HMAC-chained** + opt-in push a Log Analytics.
5. **Scope mínimo** por SPN (lectura, write, admin separados).
6. **Output sanitization**.

---

## 8. Observabilidad

- `structlog` JSON logs con `trace_id` correlacionable con span del agente.
- Cada tool emite `tool_invocation{tool, duration_ms, result, target_id}`.
- Audit log SQLite separado de logs de aplicación.
- Endpoint `/metrics` Prometheus (solo en modo HTTP).

---

## 9. Decisiones arquitectónicas (ADRs)

| ADR | Decisión | Trade-off |
|-----|----------|-----------|
| ADR-001 | Python 3.11+ sobre TypeScript/Node | Ecosistema Azure Identity + interoperabilidad con MCPs Python existentes. |
| ADR-002 | Subprocess engines sobre reimplementación | Mantenibilidad > control fino. |
| ADR-003 | stdio sobre HTTP como default | Compatibilidad universal MCP; HTTP opt-in para v4. |
| ADR-004 | Pydantic v2 para todos los schemas | Type safety + auto-publish en FastMCP. |
| ADR-005 | Audit log SQLite HMAC-chained | Tamper-evident sin blockchain; portable. |
| ADR-006 | Elicitation MCP 2025-06-18 | User-in-the-loop estandarizado vs custom prompts. |
| ADR-007 | Plan YAML versionable | Git-friendly + reviewable vs JSON binario. |
| ADR-008 | Cross-platform subset offline | Acepta que TOM/ADOMD live requiere Windows; documentar matriz. |

---

## 10. Diagramas adicionales

Diagramas en [`docs/diagrams/`](./diagrams/) (por crear en implementación):

- `arquitectura-6-capas.png`
- `flujo-plan-apply-rollback.png`
- `auth-chain.png`
- `audit-log-hmac.png`
