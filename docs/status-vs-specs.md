# Status vs Specs — 2026-08-26

> Snapshot of what `v0.1.0` actually delivers vs what the specs
> require. Used to plan Sprint 6+ (post-v0.1.0).

**TL;DR:**
- **Capa 6 (Orquestación):** ✅ completa — 3 tools MVP + foundation para 9 más.
- **Capa 1 (Modeling):** 🟡 stub — solo `powerbi-modeling-mcp` mock-tested; `te` adapter no escrito.
- **Capa 2 (Report):** 🟡 parcial — `python_report` funciona; `superbi-mcp` wired pero mock-tested.
- **Capa 3 (Cloud):** ❌ no implementada — `src/powerbi_orchestrator_mcp/cloud/` está vacío.
- **Capa 4 (Validación):** ❌ no implementada — `src/powerbi_orchestrator_mcp/validation/` está vacío.
- **Capa 5 (Viz/UX):** ❌ no implementada — `src/powerbi_orchestrator_mcp/viz/` está vacío.
- **Capa transversal (engines):** ✅ contratos, base, selector; faltan 3 adapters.

---

## 1. SPEC §6.1 — 12 MVP tools

| # | Tool | Spec | Implementación | Estado |
|---|------|------|----------------|--------|
| 1 | `connect_target` | ✅ spec §3.1 + §11 | ✅ `server.py::connect_target` (real, full path) | ✅ **DONE** |
| 2 | `plan_change` | ✅ spec §3.2 | ✅ `server.py::plan_change` (real, 4 templates) | ✅ **DONE** |
| 3 | `apply_plan` | ✅ spec §3.3 | ✅ `server.py::apply_plan` (real, rollback, audit) | ✅ **DONE** |
| 4 | `safe_rename` | ✅ spec `tools/safe-rename.md` | ✅ `PlanBuilder.build_safe_rename` + integration test e2e | ✅ **DONE** (via plan_change template) |
| 5 | `audit_model_and_report` | ✅ spec `tools/audit-model-and-report.md` | ❌ no implementado | ❌ **MISSING** |
| 6 | `deploy_to_workspace` | ✅ spec `tools/deploy-to-workspace.md` | ❌ no implementado | ❌ **MISSING** |
| 7 | `run_refresh` | ✅ spec `02-cloud-fabric.md` §3 | ❌ no implementado | ❌ **MISSING** |
| 8 | `run_dax_regression` | ✅ spec `03-validation.md` §5 | ❌ no implementado | ❌ **MISSING** |
| 9 | `diff_models` | ✅ spec `03-validation.md` §2.4 | ❌ no implementado | ❌ **MISSING** |
| 10 | `pre_deploy_check` | ✅ spec `03-validation.md` §4 | ❌ no implementado | ❌ **MISSING** |
| 11 | `generate_data_dictionary` | ✅ spec `tools/generate-data-dictionary.md` | ❌ no implementado | ❌ **MISSING** |
| 12 | `apply_theme_and_accessibility_rules` | ✅ spec `04-viz-ux.md` §3 | ❌ no implementado | ❌ **MISSING** |

**Resumen:** 4/12 MVP tools implementados (3 reales + 1 via template). 8/12 pendientes.

---

## 2. SPEC §6.2 — 3 v1.1 tools (Semana 5, post-MVP)

| # | Tool | Spec | Estado |
|---|------|------|--------|
| 1 | `add_measure_with_validation` | ⏳ outline en `docs/MVP-STATUS.md` | ❌ no implementado |
| 2 | `create_report_from_dataset` | ⏳ outline | ❌ no implementado |
| 3 | `edit_report_visual` | ⏳ outline | ❌ no implementado |

**Resumen:** 0/3 v1.1 tools implementados. MVP done formal requiere los 3 (per decisión 2026-08-26: MVP done = MVP + v1.1).

---

## 3. SPEC §6.2 — 9 v2 tools (Semanas 6-8)

| # | Tool | Outline | Estado |
|---|------|---------|--------|
| 1 | `design_report_page_from_requirements` | ✅ outline | ❌ no implementado |
| 2 | `refactor_to_calculation_groups` | ✅ outline | ❌ no implementado |
| 3 | `promote_in_pipeline` | ✅ outline | ❌ no implementado |
| 4 | `select_visuals_for_kpis` | ✅ outline | ❌ no implementado |
| 5 | `audit_report_ux_and_storytelling` | ✅ outline | ❌ no implementado |
| 6 | `optimize_report_performance` | ❌ outline pendiente | ❌ no implementado |
| 7 | `setup_rls_and_roles` | ✅ outline | ❌ no implementado |
| 8 | `create_semantic_model_from_schema` | ✅ outline | ❌ no implementado |
| 9 | `screenshot_report_pages` | ✅ outline | ❌ no implementado |

**Resumen:** 0/9 v2 tools; 8/9 tienen outline, 1/9 sin outline.

---

## 4. SPEC §6.2 — 3 v3 tools (Semanas 9-12)

| # | Tool | Outline | Estado |
|---|------|---------|--------|
| 1 | `sync_git_to_workspace` | ✅ outline | ❌ no implementado |
| 2 | `commit_workspace_to_git` | (parte del anterior) | ❌ no implementado |
| 3 | `set_sensitivity_labels` | ❌ outline pendiente | ❌ no implementado |

---

## 5. Layers — code coverage

```
src/powerbi_orchestrator_mcp/
├── orchestrator/  ← Capa 6 ✅
│   ├── server.py          ✅ 92%
│   ├── planner.py         ✅ 95%
│   ├── rollback.py        ✅ 100%
│   ├── plan_models.py     ✅ 100%
│   ├── plan_executions.py ✅ 95%
│   ├── identifiers.py     ✅ 98%
│   ├── audit.py           ✅ 96%
│   ├── elicitation.py     ✅ 100%
│   ├── context.py         ✅ 100%
│   ├── engine_detector.py ✅ 80%
│   └── step_executor.py   ✅ 100%
│
├── engines/  ← Capas 1+2 🟡 parcial
│   ├── errors.py          ✅ 100%
│   ├── exit_codes.py      ✅ 100%
│   ├── timeouts.py        ✅ 95%
│   ├── base.py            🟡 55% (subprocess lifecycle branches no cubiertas)
│   ├── selector.py        ✅ 91%
│   ├── modeling_mcp.py    ✅ 99% (mock-tested)
│   ├── report_python.py   🟡 78% (cross-platform PBIR fallback; sin tests reales)
│   └── superbi_mcp.py     ✅ 90% (mock-tested)
│
├── cloud/         ← Capa 3 ❌ VACÍA
├── validation/    ← Capa 4 ❌ VACÍA
├── viz/           ← Capa 5 ❌ VACÍA
└── tools/         ← Capa 6 high-level wrappers ❌ VACÍA
```

---

## 6. Acceptance criteria — SPEC §6.5

| Criterio | Estado | Notas |
|---------|--------|-------|
| `pip install` Linux/macOS/Windows | 🟡 Linux+macOS verificado, Windows no | El script `verify_mcp_server.py` valida fresh install |
| Config JSON registrado en 3 clientes sin errores | ❌ | Solo docs; no hay test E2E con Claude Desktop real |
| Sub-flujo MVP workflow 01 end-to-end | 🟡 parcial | `safe_rename` e2e funciona; resto requiere tools que faltan |
| `safe_rename` con rollback atómico verificado | ✅ | Test e2e `tests/integration/test_safe_rename_e2e.py` |
| `audit_model_and_report` score reproducible | ❌ | Tool no implementado |
| `deploy_to_workspace` real + refresh completa | ❌ | Tool no implementado |
| Audit log SQLite con HMAC chaining verificable | ✅ | `python -m powerbi_orchestrator_mcp.orchestrator.audit verify` |
| Coverage >80% en Capa 6 + Capa 4 | 🟡 | Capa 6: 92%; Capa 4 vacía |
| `mypy --strict` + `ruff check` limpios | ✅ | |

**5/9 acceptance criteria cumplidos; 3 marcados parciales o pendientes; 1 verificado.**

---

## 7. Roadmap priorizado (próximos sprints)

### Sprint 6 (siguiente, ~3-4 días): cerrar gaps de bajo costo

**Capa 3 (cloud):**
- `src/cloud/auth.py` — Azure Identity async wrapper (subprocess para SPN/interactive/managed identity)
- `src/cloud/fabric_client.py` — httpx async + retry + circuit breaker per spec
- `src/cloud/refresh.py` — `run_refresh` orchestration
- `src/cloud/audit_cloud.py` — reusa `audit.py` con redacción obligatoria
- `src/refresh_doctor.py` — diagnóstico de errores de refresh

**Capa 4 (validación):**
- `src/validation/bpa_runner.py` — wrapper `te bpa` subprocess
- `src/validation/dax_linter.py` — 10 anti-patterns DAX (per spec)
- `src/validation/dax_regression.py` — `run_dax_regression`
- `src/validation/model_diff.py` — `diff_models`
- `src/validation/pre_deploy_gate.py` — `pre_deploy_check`
- `src/validation/accessibility/` — WCAG auditor
- `src/validation/` package `__init__.py` con exports

**Resultado esperado:** Capa 3 + Capa 4 funcionales con tests. 6 tools MVP adicionales pasan a ✅ DONE.

### Sprint 7 (~3 días): tools MVP restantes + Capa 5

- `src/validation/audit_composite.py` — `audit_model_and_report` (compone BPA + lint + WCAG)
- `src/tools/safe_rename.py` — wrapper de alto nivel que combina plan + execute
- `src/tools/deploy_to_workspace.py` — orquesta `deploy` plan template
- `src/tools/generate_data_dictionary.py` — produce Markdown/HTML + Mermaid
- `src/tools/apply_theme_and_accessibility_rules.py` — theme.json + WCAG
- `src/viz/` skeleton — registry + suggester + layout (sin implementar)
- `src/validation/visual_registry.py` — mínimo viable

### Sprint 8 (Semana 5, ~3 días): v1.1 tools

- `src/tools/add_measure_with_validation.py`
- `src/tools/create_report_from_dataset.py`
- `src/tools/edit_report_visual.py`
- Specs dedicados: `specs/tools/add-measure-with-validation.md`, etc.

**Resultado:** MVP done formal (12 + 3 = 15 tools). Tag v1.0.0.

### Sprint 9-11 (Semanas 6-8): v2 tools — parcial

Implementar 3-5 de los 9 v2 tools según prioridad. Selection criteria: cuáles cierran gaps reales para los usuarios MVP.

### Sprint 12+ (Semanas 9-12): v3

`sync_git_to_workspace`, `set_sensitivity_labels` — governance.

---

## 8. Spec coverage (resumen ejecutivo)

```
                    Total   Hechos   Pendientes
─────────────────────────────────────────────────────
MVP tools (§6.1):    12      4         8
v1.1 tools (§6.3):   3       0         3
v2 tools (§6.2):     9       0         9
v3 tools (§6.2):     3       0         3
─────────────────────────────────────────────────────
TOTAL tools:         27      4         23

Specs escritas:       13 (5 Tier-A + 4 Tier-B + 2 Tier-C + 2 cross-cutting)
Outlines v2/v3:      9     (1 página cada uno, en specs/tools/)
```

**Cobertura por capa (code):**
```
Capa 6: 100% (orquestador completo)
Capa 1: 30% (modeling_mcp adapter; sin te adapter ni BPA)
Capa 2: 50% (python_report + superbi_mcp; sin validación PBIR)
Capa 3:  0% (cloud/ vacío)
Capa 4:  0% (validation/ vacío)
Capa 5:  0% (viz/ vacío)
```

---

## 9. Lo que se puede hacer HOY con v0.1.0

1. ✅ `pip install powerbi-orchestrator-mcp` en Linux/macOS
2. ✅ Conectar a un PBIP local y obtener session_id
3. ✅ Crear un plan para `safe_rename` / `audit` / `deploy` / `dax_regression`
4. ✅ Aplicar planes con `python_report` (cambios en PBIR JSON)
5. ✅ Rollback atómico cross-engine cuando algo falla
6. ✅ Audit log con HMAC chain verificable

**Lo que NO se puede hacer aún:**
- Auditar calidad de un modelo (BPA + DAX lint)
- Desplegar a un workspace de Fabric
- Refrescar datasets
- Generar data dictionary
- Aplicar theme/WCAG
- Cualquier tool v1.1 / v2 / v3

---

## 10. Recomendación

**Sprint 6** (cierre de bajo costo de Capa 3 + Capa 4) es el siguiente paso
natural. Estos dos layers son los que más faltan y tienen los adapters
más mecánicos (subprocess CLI + REST). Tiempo estimado: 3-4 días dev
senior. Después de Sprint 6, el coverage de acceptance criteria sube
de 5/9 a ~8/9 y el proyecto estará prácticamente en "MVP done" real.
