# MVP STATUS — powerbi-orchestrator-mcp

> Estado de implementación vs specs. **Post-Sprint 9 = 18/18 tools**
> (12 MVP + 3 v1.1 + 3 v2) per SPEC §6.1+§6.2+§6.3. Tag [`v1.0.0`](../git)
> creado; [`v1.1.0`](../RELEASE-NOTES-v1.1.0.md) pendiente de tag al cerrar Sprint 9.

**Leyenda:**
- ❌ No implementado
- 🟡 En progreso
- ✅ Implementado + verificado (tests passing)
- ⚠️ Implementado parcial / best-effort

---

## Estado por capa

| Capa | Cobertura | Estado |
|------|-----------|--------|
| 6 · Orquestación | 100% | ✅ completa |
| 1 · Modeling | 55% | ✅ `powerbi-modeling-mcp` wired (mock-tested) + `refactor_to_calculation_groups` pattern detector; `te` adapter pendiente Sprint 9+ |
| 2 · Report | 70% | ✅ `python_report` (built-in) + `superbi_mcp` (wired, mock-tested) + `design_report_page_from_requirements` |
| 3 · Cloud | 85% | ✅ `fabric_client`, `refresh`, `audit_cloud` con redacción obligatoria |
| 4 · Validación | 95% | ✅ `bpa_runner`, `dax_linter` (7 patterns), `dax_regression`, `model_diff`, `pre_deploy_gate` |
| 5 · Viz/UX | 50% | ✅ `WcagAuditor` + `apply_theme_and_accessibility_rules` + `viz/visual_registry` (8 visual types) + `viz/visual_suggester` (deterministic ranking) + `select_visuals_for_kpis`. Módulos `layout`/`storytelling`/`performance_budget` siguen v2 pendientes. |

---

## v1.0.0 — MVP done (Sprints 1-8)

### Sprints ejecutados

- **Sprint 1** — Scaffold + orquestación (server, context, audit, elicitation, identifiers, plan_executions).
- **Sprint 2** — Planner (4 MVP templates) + RollbackEngine.
- **Sprint 3-4** — Engine contracts (errors, timeouts, exit_codes), `powerbi-modeling-mcp` adapter, `python_report`, `superbi_mcp`, selector.
- **Sprint 5** — Cross-engine dispatcher + integration test `safe_rename` end-to-end.
- **Sprint 6** — Capa 3 (cloud: auth, fabric_client con token bucket + circuit breaker, refresh + refresh_doctor, audit_cloud con redacción PII) + Capa 4 (validation: bpa, dax_linter con 7 anti-patterns, dax_regression, model_diff, pre_deploy_gate, accessibility/wcag_auditor).
- **Sprint 7** — 8 tools MVP adicionales: `audit_model_and_report`, `deploy_to_workspace`, `run_refresh`, `run_dax_regression`, `diff_models`, `pre_deploy_check`, `generate_data_dictionary`, `apply_theme_and_accessibility_rules`.
- **Sprint 8** — 3 tools v1.1: `add_measure_with_validation` (lint gate + pluggable writer), `create_report_from_dataset` (PBIR scaffold idempotente), `edit_report_visual` (field-level merge + atomic write).

### Tools MVP (12/12) ✅

| # | Tool | Estado |
|---|------|--------|
| 1 | `connect_target` | ✅ real, valida target_ref, detecta engines, crea session |
| 2 | `plan_change` | ✅ real, 4 templates MVP |
| 3 | `apply_plan` | ✅ real, heartbeat + rollback + audit log |
| 4 | `safe_rename` (via `plan_change` template) | ✅ end-to-end test |
| 5 | `audit_model_and_report` | ✅ composto BPA + lint + WCAG |
| 6 | `deploy_to_workspace` | ✅ pre-deploy + publish + schedule (mock + real paths) |
| 7 | `run_refresh` | ✅ wraps `RefreshOrchestrator` |
| 8 | `run_dax_regression` | ✅ wraps `DaxRegressionRunner` (tolerance-based diff) |
| 9 | `diff_models` | ✅ wraps `ModelDiffer` (breaking/non-breaking classification) |
| 10 | `pre_deploy_check` | ✅ wraps `PreDeployGate` (3 profiles) |
| 11 | `generate_data_dictionary` | ✅ Markdown + Mermaid ER diagram + coverage score |
| 12 | `apply_theme_and_accessibility_rules` | ✅ Okabe-Ito 8-color + alt text backfill + WCAG re-audit |

### Tools v1.1 (3/3) ✅

| # | Tool | Estado |
|---|------|--------|
| 1 | `add_measure_with_validation` | ✅ lint gate + pluggable writer |
| 2 | `create_report_from_dataset` | ✅ scaffold PBIR idempotente |
| 3 | `edit_report_visual` | ✅ field-level merge + atomic write |

### Acceptance criteria SPEC §6.5 (9/9)

- [x] Instalación `pip install powerbi-orchestrator-mcp` funciona en Linux + macOS + Windows.
- [x] Config JSON registrado en VS Code + Claude Desktop + OpenClaw sin errores.
- [x] Sub-flujo MVP workflow 01 funciona end-to-end.
- [x] `safe_rename` propaga a modelo + DAX + report bindings con rollback atómico verificado por test.
- [x] `audit_model_and_report` devuelve score reproducible sobre el PBIP de prueba.
- [x] `deploy_to_workspace` mock + real paths (publish + schedule + initial refresh).
- [x] Audit log SQLite con HMAC chaining verificable.
- [x] Coverage de tests >80% en código de orquestación (capa 6) y validación (capa 4).
- [x] `mypy --strict` + `ruff check` limpios.

---

## Sprint 9-11 (roadmap v2)

Sprints 9-11 cubren los 9 tools v2 (todos con outline de 1 página
en [`specs/tools/`](../specs/tools/)):

**Sprint 9 — ✅ DONE (release v1.1.0)**
- `refactor_to_calculation_groups` ✅ regex-based skeleton detector (YTD/QTD/MTD/PY/YOY/MOM/QOQ/WOW/...) con `min_candidates` filter + dry-run.
- `select_visuals_for_kpis` ✅ rankea visuales via `viz/visual_suggester` (cardinality + audience + anti-recommendations).
- `design_report_page_from_requirements` ✅ NL brief → KPI extraction (regex, 4 patrones) → F-pattern layout → atomic write de `page.json` + theme.
- **Foundation nueva:** `viz/visual_registry.py` (8 visual types nativos estilo SQLBI) + `viz/visual_suggester.py` (algoritmo determinístico).

**Sprint 10 — pendiente** (`audit_report_ux_and_storytelling`, `optimize_report_performance`, `screenshot_report_pages`; ~3 días de dev senior).

**Sprint 11 — pendiente** (`promote_in_pipeline`, `setup_rls_and_roles`, `create_semantic_model_from_schema`; ~3 días).

> ℹ️ `optimize_report_performance` no tiene spec dedicado en `specs/tools/` todavía — outline pendiente de crear antes de implementar.

Total restante v2: **6 tools** después de cerrar Sprint 9. Estimado 1-1.5 semanas de dev senior.

## Sprint 12+ (roadmap v3)

- `sync_git_to_workspace` / `commit_workspace_to_git` (FSL-friendly).
- `set_sensitivity_labels` (governance; outline pendiente).
- Storytelling con análisis de varianza real (requiere telemetry).

## Hardening continuo (no-bloqueante para v1.0.0)

- Real-binary integration tests (cuando `te` / `powerbi-modeling-mcp` /
  `dscmd` se instalen en CI).
- `viz/visual_registry.py` mínimo (necesario para `select_visuals_for_kpis` v2).
- Multi-tenant / remote transport (v4, fuera del MVP).

---

## Cómo verificar localmente

```bash
pip install -e .
python scripts/verify_mcp_server.py    # fresh-install e2e check (17 tools)
pytest tests/                          # 526 unit + integration tests
mypy --strict src/powerbi_orchestrator_mcp
ruff check src/powerbi_orchestrator_mcp
```

Resultado esperado: 17 tools/list, 526/526 tests passing, mypy+ruff clean, exit code 0.
