# SPEC.md — powerbi-orchestrator-mcp

> Visión, arquitectura, stack y alcance del MVP ambicioso.
> Documento raíz. Las decisiones técnicas detalladas viven en
> [`docs/architecture.md`](./docs/architecture.md) y los specs modulares en
> [`specs/`](./specs/README.md).

**Status:** v1.0.0 released (2026-08-26). MVP done — 15/15 tools (12 MVP + 3 v1.1). Ver [`RELEASE-NOTES-v1.0.0.md`](./RELEASE-NOTES-v1.0.0.md) y [`docs/MVP-STATUS.md`](./docs/MVP-STATUS.md).
**Fecha:** 2026-08-21 (spec original); v1.0.0 released 2026-08-26
**Owner:** Bastian Berrios (@berriosb)
**Licencia:** MIT

---

## 1. Visión

Un único servidor MCP que unifica modelado semántico, autoría de reportes,
operaciones nube (Fabric / Power BI Service), validación y diseño de
visualización en Power BI. El servidor es **delgado y orquestador**: delega
lo ya resuelto a motores especializados (Microsoft oficial, Tabular Editor,
Super BI MCP, DAX Studio, skill de Microsoft para PBIR) e implementa
propiamente solo lo que nadie cubre bien (capa nube madura, capa de
visualización/UX con WCAG verificable, motor de planes con rollback).

### 1.1 Hipótesis de diseño

1. **Delegar > reimplementar.** Reimplementar TOM/TMDL/PBIR es absurdo y
   nunca alcanzará a Microsoft. El valor está en la orquestación, no en las
   primitivas.
2. **Una intención de negocio = un tool.** El LLM no debería llamar 500
   primitivas; debería llamar "rename esta columna propagando a todo" y el
   servidor lo descompone.
3. **Planes versionados en Git.** Cada cambio queda como un plan YAML que se
   commitea, se revisa y se rollbacks atómicamente.
4. **Validación continua, no final.** Cada step valida; fallar un step hace
   rollback del step anterior, no aborta todo.
5. **WCAG y UX son features de producto, no nice-to-have.** El 80% de los
   reportes en producción falla accesibilidad.

### 1.2 No-objetivos

- ❌ Reemplazar Power BI Desktop, Tabular Editor 3 o DAX Studio.
- ❌ Ser un IDE/modelador gráfico.
- ❌ Soportar Analysis Services clásico como target primario (solo compatibilidad).
- ❌ Hostear un SaaS multi-tenant (es local stdio).

---

## 2. Arquitectura (resumen)

6 capas. El servidor expone solo la **Capa 6 (orquestación)** como tools al
agente. Las otras 5 son internas.

```
┌─────────────────────────────────────────────────────────────────┐
│ AGENTE (Claude / Copilot / Hermes / OpenClaw)                   │
└────────────────────────────┬────────────────────────────────────┘
                             │ MCP (stdio)
┌────────────────────────────▼────────────────────────────────────┐
│ CAPA 6 · ORQUESTACIÓN (este servidor)                           │
│   26 tools de alto nivel + planner + rollback engine            │
└──┬───────────┬───────────┬───────────┬──────────────────────────┘
   │           │           │           │
   ▼           ▼           ▼           ▼
┌───────┐  ┌────────┐  ┌─────────┐  ┌─────────────┐
│ CAPA 1│  │ CAPA 2 │  │ CAPA 3  │  │ CAPA 5      │
│Modelo │  │Reporte │  │Nube     │  │Viz/UX       │
│       │  │        │  │Fabric   │  │             │
│DELEGA │  │DELEGA  │  │PROPIO   │  │PROPIO       │
│→ powerbi-│ → superbi│ → REST    │  │→ registry   │
│  modeling│   /skill │   + XMLA  │  │→ WCAG       │
│  -mcp   │   -for-  │   + Azure │  │→ suggester  │
│→ te CLI │   fabric │   Identity│  │→ layout     │
│→ dscmd  │→ pbip-   │           │  │→ theme      │
│         │  validator           │  │→ story-     │
│         │                      │  │  telling    │
└───────┘  └────────┘  └─────────┘  └─────────────┘
                  │
                  ▼
            ┌─────────────┐
            │ CAPA 4      │
            │Validación   │
            │             │
            │→ BPA (te)   │
            │→ DAX linter │
            │→ Regression │
            │→ WCAG audit │
            │→ Pre-deploy │
            │  gate       │
            └─────────────┘
```

Detalle completo en [`docs/architecture.md`](./docs/architecture.md).
Cada capa tiene su spec modular en `specs/0X-*.md`.

---

## 3. Stack

### 3.1 Lenguaje y runtime

| Componente | Elección | Razón |
|------------|----------|-------|
| Lenguaje | **Python 3.11+** | Ecosistema Azure Identity maduro, subprocess robusto, mismo lenguaje que `fabric-rti-mcp` y `sulaiman013/powerbi-mcp` (interoperabilidad), packaging PyPI universal. |
| MCP SDK | `mcp[cli]>=1.0` (FastMCP) | Spec 2025-06-18, OAuth2 Resource Server, elicitation, resources, prompts. |
| Type safety | Pydantic v2 + mypy --strict | Schemas de tools auto-publicados; cero `Any` en outputs. |
| Async | `asyncio` + `httpx` | REST Fabric y subprocess concurrentes. |
| Auth | `azure-identity` `DefaultAzureCredential` | Chain: AzureCli → Environment (SPN) → ManagedIdentity → InteractiveBrowser. Cero secretos en disco. |

### 3.2 Distribución

- **PyPI**: `pip install powerbi-orchestrator-mcp` → comando `powerbi-orchestrator-mcp`.
- **Docker**: imagen cross-platform con subset offline (sin Desktop) para CI.
- **Binarios externos** (subprocess, version-pin):
  - `@microsoft/powerbi-modeling-mcp@<pinned>` vía `npx`.
  - `te` (Tabular Editor CLI) — binario self-contained.
  - `dscmd.exe` (DAX Studio CLI) — Windows only, optional.
  - `pbip-validator` — Python package de Microsoft (cuando exista).

### 3.3 Compatibilidad de clientes MCP

Compatible con cualquier cliente MCP stdio:

- VS Code + GitHub Copilot
- Claude Desktop / Claude Code
- OpenClaw
- Hermes (cualquier perfil)
- Cursor
- Cline

Config (igual en todos):

```json
{"mcpServers":{"powerbi-orchestrator-mcp":{
  "command":"powerbi-orchestrator-mcp",
  "args":["--start"],
  "env":{"PBI_AUTH_MODE":"interactive"}
}}}
```

---

## 4. Herramientas (alto nivel)

**26 tools de alto nivel** agrupados en 7 categorías. Cada tool devuelve
`structuredContent` (JSON Schema validado por Pydantic) y opcionalmente
`artifact` (screenshot, archivo generado).

### 4.1 Orquestación (3)
- `connect_target` — abre contexto (PBI Desktop / Fabric workspace / PBIP / .pbix), detecta engines disponibles y degrada con warning si falta alguno.
- `plan_change` — NL → plan YAML versionable con steps + rollback_steps + risk_score.
- `apply_plan` — ejecuta plan con checkpoints; cada step tiene rollback pre-calculado.

### 4.2 Modelo semántico (4)
- `create_semantic_model_from_schema` — scaffold TMDL desde spec JSON/YAML.
- `add_measure_with_validation` — añade measure con lint DAX + runtime check.
- `refactor_to_calculation_groups` — refactor measures candidatas con reconciliación de totales (v2).
- `setup_rls_and_roles` — crea roles RLS + matriz de prueba por rol (v2).

### 4.3 Reporte (3)
- `create_report_from_dataset` — scaffold PBIR mínimo viable desde dataset.
- `edit_report_visual` — edición determinística (tipo, fields, format, posición).
- `safe_rename` ⭐ — rename cross-engine (model + DAX + M + report bindings) con rollback atómico.

### 4.4 Nube Fabric / Service (5)
- `deploy_to_workspace` — publish PBIP a workspace con refresh + gateway + labels.
- `promote_in_pipeline` — dev→test→prod con quality gate (v2).
- `run_refresh` — refresh con manejo async + cancel + rollback si falla primera partición.
- `sync_git_to_workspace` / `commit_workspace_to_git` — Git integration bidireccional (v3).
- `set_sensitivity_labels` — governance en lote (v3).

### 4.5 Validación y testing (5)
- `audit_model_and_report` — auditoría integral: BPA + WCAG + lint + star-schema + naming.
- `run_dax_regression` — ejecuta suite de queries DAX vs baseline versionado.
- `diff_models` — diff legible entre dos modelos con clasificación breaking/non-breaking.
- `pre_deploy_check` — gate configurable con umbrales por check.
- `generate_data_dictionary` — Markdown/HTML con diagrama Mermaid + coverage score.

### 4.6 Visualización / UX (5)
- `apply_theme_and_accessibility_rules` — theme.json + WCAG (contraste + alt text + tab order).
- `design_report_page_from_requirements` — diseño completo de página desde brief NL (v2).
- `select_visuals_for_kpis` — recomendación de visuales según data shape + audiencia (v2).
- `optimize_report_performance` — análisis heurístico de performance sin ejecutar (v2).
- `audit_report_ux_and_storytelling` — auditoría cualitativa: jerarquía, densidad, narrativa (v2).

### 4.7 Documentación y observabilidad (2)
- `generate_data_dictionary` — ver 4.5.
- `screenshot_report_pages` — best-effort via Desktop Bridge (v2).

Detalle de cada tool en `specs/tools/`. **MVP incluye 12 tools** (las
listadas en §6.1; las demás marcadas con `(v2)` o `(v3)` son post-MVP).

---

## 5. Seguridad y autenticación

### 5.1 Modelo de autenticación

| Target | Método | Scopes mínimos |
|--------|--------|----------------|
| Fabric REST (lectura) | DefaultAzureCredential (SPN o Interactive) | `https://analysis.windows.net/powerbi/api/Dataset.Read.All` |
| Fabric REST (write) | SPN recomendado | + `Dataset.ReadWrite.All` |
| Fabric REST (admin) | SPN con rol admin separado | `https://api.fabric.microsoft.com/Admin.*` (gate explícito) |
| Power BI Desktop local | Loopback TCP (no auth) | n/a |
| PBIP / .pbix local | Filesystem | n/a |
| XMLA endpoint | SPN o AAD user | `Tenant.Read.All` |

### 5.2 Salvaguardas

- **Elicitation obligatoria** antes de:
  - conectar primer workspace
  - primer write a modelo
  - refresh en workspace de producción
  - delete de item
  - set de sensitivity label
  - promote en deployment pipeline
- **`--readonly` flag global** desactiva tools que escriben (espejo de `powerbi-modeling-mcp`).
- **Audit log SQLite con HMAC chaining** (cada fila firma la anterior → tamper-evident).
- **Sanitización de outputs**: redacción automática de tokens, connection strings, emails admin.
- **Warning explícito** cuando un DAX query devuelve >N filas (riesgo PII).
- **Secretos cero en disco**: usar siempre `DefaultAzureCredential`.

### 5.3 Rate limiting y cuotas

- Cliente REST con retry exponencial (429 → backoff).
- Caché de metadata entre tools del mismo session (model schema, visual registry).
- Elicitation rate-limited: máx 1 cada 5s para no spamear al usuario.

---

## 6. MVP ambicioso (12 tools MVP + 3 v1.1, 5 semanas)

### 6.1 Alcance MVP v1

**12 tools** que cubren los gaps reales:

| # | Tool | Capa |
|---|------|------|
| 1 | `connect_target` | 6 |
| 2 | `plan_change` | 6 |
| 3 | `apply_plan` | 6 |
| 4 | `safe_rename` | 1+2+4+6 |
| 5 | `audit_model_and_report` (subset: BPA + naming + dax_lint + accessibility) | 4+5 |
| 6 | `deploy_to_workspace` | 3 |
| 7 | `run_refresh` | 3 |
| 8 | `run_dax_regression` | 1+4 |
| 9 | `diff_models` | 1+4 |
| 10 | `pre_deploy_check` | 4 |
| 11 | `generate_data_dictionary` | 1 |
| 12 | `apply_theme_and_accessibility_rules` | 4+5 |

### 6.2 Lo que NO va en v1

**Tools v2 (semanas 5-8):**
- ❌ `design_report_page_from_requirements` completo (selector de visuales).
- ❌ `refactor_to_calculation_groups` con reconciliación total.
- ❌ `promote_in_pipeline` (dev→test→prod gates).
- ❌ `select_visuals_for_kpis` (recomendador).
- ❌ `audit_report_ux_and_storytelling` (heurístico).
- ❌ `optimize_report_performance` (análisis heurístico).
- ❌ `setup_rls_and_roles` (automatización de roles RLS).
- ❌ `create_semantic_model_from_schema` (scaffold desde spec).
- ❌ `screenshot_report_pages` (best-effort via Desktop Bridge).

**Tools v3 (semanas 9-12):**
- ❌ `sync_git_to_workspace` / `commit_workspace_to_git`.
- ❌ `set_sensitivity_labels` (governance).
- ❌ Storytelling scoring con análisis de varianza real.

> **Nota sobre `screenshot_report_pages`:** el spec inicial lo marcaba como
> v3; el fix post-audit (commit `3ad85c3`, 2026-08-21) lo reclasificó a
> v2 porque Desktop Bridge básico es viable en MVP tardío. Este spec §6.2
> ahora lo lista explícitamente en v2 (coherente con §4.7 y `specs/README.md`).
>
> **Nota sobre Desktop Bridge real (determinístico):** lo que SÍ queda v3
> es la versión "determinística con análisis de varianza real", que
> requiere telemetría de rendering. El best-effort via Desktop Bridge
> normal es v2.

### 6.3 Plan de 5 semanas (renegociado 2026-08-26)

El plan original era de 4 semanas (188h). Tras la auditoría de 2026-08-26
se renegoció a **5 semanas** (217h totales) para incluir la entrega de las
3 tools v1.1 (`add_measure_with_validation`, `create_report_from_dataset`,
`edit_report_visual`) sin las cuales el workflow 01 queda en ~70% en lugar
de ~90%. Ver `docs/MVP-STATUS.md` §Plan renegociado y
`docs/IMPLEMENTATION-PLAN-v1.0.md` Semana 5.

**Tools v1.1 (post-MVP, semana 5):**
- `add_measure_with_validation` — añade measure con lint DAX + runtime check.
- `create_report_from_dataset` — scaffold PBIR mínimo viable desde dataset.
- `edit_report_visual` — edición determinística (tipo, fields, format, posición).

> **Decisión 2026-08-26 (opción C):** estas 3 tools NO se promueven a
> §6.1 MVP para no romper el plan de 4 semanas. Viven como v1.1 — su spec
> dedicado y código se entregan en Semana 5 dedicada. Aceptar el plan de 5
> semanas en lugar de 4.
>
> **Decisión 2026-08-26 (Bastian):** ejecutar Semana 5 antes de declarar
> "MVP done" formalmente. Es decir, **MVP done = 12 tools MVP + 3 tools
> v1.1** (15 totales). Esto se refleja en los acceptance criteria de §6.5
> (Semana 4 se considera "MVP parcial" hasta cerrar Semana 5).

### 6.4 Plan original de 4 semanas (referencia histórica)

El plan detallado tarea por tarea está en `docs/IMPLEMENTATION-PLAN-v1.0.md`.
Resumen:

| Semana | Entregable | Capa |
|--------|-----------|------|
| 1 | Scaffold Python + FastMCP + 3 tools dummy + tests. Planner + apply_plan + connect_target funcionales con elicitation. | 6 |
| 2 | Engine adapters (powerbi-modeling-mcp subprocess + pbip-validator). `safe_rename` end-to-end cross-platform (model + report bindings, sin M). | 1+2+6 |
| 3 | Capa 3 cloud: REST Fabric (workspaces, datasets, refresh, items CRUD). `deploy_to_workspace` + `run_refresh` maduros. Azure Identity + elicitation + audit log. | 3 |
| 4 | Capa 4 validación: BPA via `te`, DAX linter propio, `audit_model_and_report` + `pre_deploy_check` + `run_dax_regression` + `diff_models`. `apply_theme_and_accessibility_rules`. | 4+5 |
| 5 (v1.1) | Specs + código de `add_measure_with_validation`, `create_report_from_dataset`, `edit_report_visual`. Tests e2e. | 1+2+6 |

### 6.5 Criterios de "MVP done"

> **Nota (post-audit 2026-08-21):** El workflow 01 tiene partes que dependen
> de tools v1.1/v2. Estos criterios validan el **sub-flujo MVP alcanzable**,
> no el showcase completo. Ver
> [`specs/workflows/01-from-csv-to-published-report.md` §9](./specs/workflows/01-from-csv-to-published-report.md)
> para el detalle.

- [ ] Instalación `pip install powerbi-orchestrator-mcp` funciona en Linux + macOS + Windows.
- [ ] Config JSON registrado en VS Code + Claude Desktop + OpenClaw sin errores.
- [ ] **Sub-flujo MVP del workflow 01 funciona end-to-end** con un PBIP de prueba:
  conectar → modelo (scaffold manual del agente) → medidas con lint → theme/WCAG → audit → pre-deploy → deploy → refresh → data dictionary.
- [ ] `safe_rename` propaga a modelo + DAX + report bindings con rollback atómico verificado por test.
- [ ] `audit_model_and_report` devuelve score reproducible sobre el PBIP de prueba.
- [ ] `deploy_to_workspace` publica a un workspace real y refresh completa.
- [ ] Audit log SQLite con HMAC chaining verificable.
- [ ] Coverage de tests >80% en código de orquestación (capa 6) y validación (capa 4). Métrica complementaria en §9.2 incluye capa 5 (viz/UX) por su criticidad para WCAG.
- [ ] `mypy --strict` limpio. `ruff check` limpio.

---

## 7. Roadmap post-MVP

### v2 (semanas 5-8)
- `design_report_page_from_requirements` con selector de visuales.
- `select_visuals_for_kpis`.
- `audit_report_ux_and_storytelling` (sin screenshots).
- `optimize_report_performance` (análisis heurístico).
- `screenshot_report_pages` (best-effort via Desktop Bridge básico).
- `setup_rls_and_roles` (automatización + matriz de prueba).
- `create_semantic_model_from_schema` (scaffold desde YAML/JSON spec).
- `refactor_to_calculation_groups` con reconciliación total.
- `promote_in_pipeline` con quality gate.
- Super BI MCP integration cuando hay Windows.

### v3 (semanas 9-12)
- `sync_git_to_workspace` / `commit_workspace_to_git`.
- `set_sensitivity_labels` (governance).
- `screenshot_report_pages` robusto via Desktop Bridge.
- Storytelling scoring con análisis de varianza sobre datos reales.
- Marketplace de page templates.
- Plugin system para checks custom (orgs meten BPA rules propias).

### v4+ (backlog)
- Remote transport (HTTP + Entra OAuth).
- Multi-tenant (un orchestrator que sirve a varios workspaces).
- Caching distribuido (Redis) para metadata de modelos grandes.
- Web UI para auditar sin agente.

---

## 8. Riesgos (top 5)

| # | Riesgo | Mitigación |
|---|--------|-----------|
| 1 | `powerbi-modeling-mcp` cambia API (es preview) | Adapter delgado + pin version npm + tests de contract + fallback a `te` CLI. |
| 2 | Cloud REST paths sin live test | Mocks en CI + smoke opt-in con credenciales del user. |
| 3 | LLM escribe PBIR inválido | Validar con `pbip-validator` después de cada write + rollback atómico por step. |
| 4 | Windows-only dependencies (TOM/ADOMD) limitan Linux users | Documentar matriz OS×feature; subset offline cross-platform. |
| 5 | Agente publica a workspace equivocado | Elicitation obligatoria en writes a prod; `--allow-prod` flag; audit log. |

Riesgos completos (14) en [`docs/MVP-STATUS.md` §Riesgos](./docs/MVP-STATUS.md).

---

## 9. Métricas de éxito

### Adopción (3 meses post-release)
- 50+ stars en GitHub.
- 5+ contributors externos.
- 10+ organizaciones usándolo en CI.
- Primer case study público de un equipo reemplazando flujo manual con `powerbi-orchestrator-mcp`.

### Calidad técnica
- 0 secretos committed en history (verificado por CI).
- Coverage >80% en capas 4, 5 y 6.
- Latencia p95 de `safe_rename` <10s para un modelo de 100 measures.
- Latencia p95 de `audit_model_and_report` <30s para modelo mediano.

### Diferenciación
- 3+ features que **ningún otro MCP** tiene juntos (cross-engine rename + cloud mature + WCAG + regression).
- 1+ paper o talk presentando la arquitectura.

---

## 10. Specs relacionados

- [`docs/architecture.md`](./docs/architecture.md) — arquitectura detallada con diagramas.
- [`specs/README.md`](./specs/README.md) — índice de specs modulares.
- [`docs/IMPLEMENTATION-PLAN-v1.0.md`](./docs/IMPLEMENTATION-PLAN-v1.0.md) — roadmap semana a semana.
- [`docs/MVP-STATUS.md`](./docs/MVP-STATUS.md) — qué está implementado.

---

> **Nota:** Este documento es la fuente de verdad de la visión. Los specs
> modulares en `specs/` pueden ser más detallados por feature; este SPEC.md
> no debería contradecirlos — si lo hace, gana el spec y se actualiza SPEC.md.
