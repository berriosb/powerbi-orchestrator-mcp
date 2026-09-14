# MVP STATUS — powerbi-orchestrator-mcp

> Estado de implementación vs specs. **Post-Sprint 14 = 26/26 tools**
> (12 MVP + 3 v1.1 + 8 v2 + 3 v3) per SPEC §6.1+§6.2+§6.3+§7. Latest
> release [`v1.7.0`](../RELEASE-NOTES-v1.7.0.md); hardening backlog closed.

**Leyenda:**
- ❌ No implementado
- 🟡 En progreso
- ✅ Implementado + verificado (tests passing)
- ⚠️ Implementado parcial / best-effort

---

## Estado por capa

| Capa | Cobertura | Estado |
|------|-----------|--------|
| 6 · Orquestación | 100% | ✅ completa (3 tools + planner + rollback + audit + elicitation) |
| 1 · Modeling | 80% | ✅ `powerbi-modeling-mcp` wired + `te` adapter (Sprint 14A, skeleton + subprocess modes) + `refactor_to_calculation_groups` + `create_semantic_model_from_schema` + `setup_rls_and_roles` |
| 2 · Report | 95% | ✅ `python_report` (built-in) + `superbi_mcp` (wired with python_report fallback) + `create_report_from_dataset` + `edit_report_visual` + `design_report_page_from_requirements` |
| 3 · Cloud | 100% | ✅ `fabric_client` con Fabric Items API + Power BI Service REST (datasets/refresh/schedule/take-over/history) + `refresh` + `audit_cloud` con redacción PII |
| 4 · Validación | 100% | ✅ `bpa_runner`, `dax_linter` (7 patterns), `dax_regression`, `model_diff`, `pre_deploy_gate`, `wcag_auditor`, `story_variance` |
| 5 · Viz/UX | 90% | ✅ `WcagAuditor` + `apply_theme_and_accessibility_rules` + `viz/visual_registry` (8 visual types) + `viz/visual_suggester` + `select_visuals_for_kpis` + `design_report_page_from_requirements` + `optimize_report_performance` + `audit_report_ux_and_storytelling` + `screenshot_report_pages` (best-effort SVG/JSON manifests) |

---

## v1.0.0 — MVP done (Sprints 1-8)

### Sprints ejecutados

- **Sprint 1** — Scaffold + orquestación (server, context, audit, elicitation, identifiers, plan_executions).
- **Sprint 2** — Planner (4 MVP templates) + RollbackEngine.
- **Sprint 3-4** — Engine contracts (errors, timeouts, exit_codes), `powerbi-modeling-mcp` adapter, `python_report`, `superbi_mcp`, selector.
- **Sprint 5** — Cross-engine dispatcher + integration test `safe_rename` end-to-end.
- **Sprint 6** — Capa 3 (cloud: auth, fabric_client con token bucket + circuit breaker, refresh + refresh_doctor, audit_cloud con redacción PII) + Capa 4 (validation: bpa, dax_linter con 7 anti-patterns, dax_regression, model_diff, pre_deploy_gate, accessibility/wcag_auditor).
- **Sprint 7** — 8 tools MVP adicionales: `audit_model_and_report`, `deploy_to_workspace`, `run_refresh`, `run_dax_regression`, `diff_models`, `pre_deploy_check`, `generate_data_dictionary`, `apply_theme_and_accessibility_rules`.
- **Sprint 8** — 3 tools v1.1: `add_measure_with_validation`, `create_report_from_dataset`, `edit_report_visual`.

### Tools MVP (12/12) ✅

| # | Tool | Estado |
|---|------|--------|
| 1 | `connect_target` | ✅ real, valida target_ref, detecta engines, crea session |
| 2 | `plan_change` | ✅ real, 4 templates MVP |
| 3 | `apply_plan` | ✅ real, heartbeat + rollback + audit log |
| 4 | `safe_rename` (via `plan_change` template) | ✅ end-to-end test |
| 5 | `audit_model_and_report` | ✅ composto BPA + lint + WCAG |
| 6 | `deploy_to_workspace` | ✅ pre-deploy + create_item + refresh schedule + initial refresh (real Fabric + PBI Service calls; mock=True short-circuits) |
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
- [x] `deploy_to_workspace` mock + real paths (create_item + schedule + initial refresh).
- [x] Audit log SQLite con HMAC chaining verificable.
- [x] Coverage de tests >80% en código de orquestación (capa 6) y validación (capa 4).
- [x] `mypy --strict` + `ruff check` limpios.

---

## Sprint 9-12 — v2 + v3 tools shipped

### Sprint 9 — ✅ DONE (release v1.1.0)
- `refactor_to_calculation_groups` ✅ regex-based skeleton detector (YTD/QTD/MTD/PY/YOY/MOM/QOQ/WOW/...) con `min_candidates` filter + dry-run + TE adapter seam.
- `select_visuals_for_kpis` ✅ rankea visuales via `viz/visual_suggester` (cardinality + audience + anti-recommendations).
- `design_report_page_from_requirements` ✅ NL brief → KPI extraction (regex, 4 patrones) → F-pattern layout → atomic write de `page.json` + theme.
- **Foundation nueva:** `viz/visual_registry.py` (8 visual types nativos estilo SQLBI) + `viz/visual_suggester.py` (algoritmo determinístico).

### Sprint 10 — ✅ DONE (release v1.2.0)
- `optimize_report_performance` ✅ heurístico (visual density, pie/donut, custom visuals, conditional-formatting). Score 0-100 + estimated load ms + per-page hotspots.
- `audit_report_ux_and_storytelling` ✅ heurístico cualitativo (hierarchy / density / narrative / mobile / cohesion) con strictness profiles.
- `screenshot_report_pages` ✅ best-effort: SVG wireframes + JSON manifests sin Desktop Bridge; PNG/PDF requiere superbi-mcp en Windows.

### Sprint 11 — ✅ DONE (release v1.3.0)
- `create_semantic_model_from_schema` ✅ TMDL scaffold desde spec YAML/JSON + validación Pydantic + dangling-reference check + atomic write.
- `setup_rls_and_roles` ✅ roles + members + test matrix ejecutable vía injected test_engine.
- `promote_in_pipeline` ✅ Fabric Deployment Pipeline stages + gates (pre_deploy + audit + dax_regression) + dry-run.

### Sprint 12 — ✅ DONE (release v1.5.0)
- `commit_workspace_to_git` ✅ snapshot Fabric workspace → local Git repo (one item per commit).
- `sync_git_to_workspace` ✅ deploy Git tree → workspace con conflict surfacing.
- `set_sensitivity_labels` ✅ Microsoft Purview bulk labels (admin scope gated + SHA-256 name redaction).

### Sprint 14 — ✅ DONE (release v1.7.0)
- TE modeling adapter concretado: `mode='skeleton'` por defecto, `mode='subprocess'` cuando TE binario está en PATH. Wired en `create_semantic_model_from_schema` y `refactor_to_calculation_groups`.
- Story variance analysis: comparación determinística byte-level de PNG screenshots para detectar regresiones visuales.

Total: **26/26 tools implementadas**, backlog de hardening cerrado.

---

## Sprint 13+ (post-v1.7 backlog)

Sprint 13 fue ocupado por hardening sin tools nuevas (cobertura, redactado PII, elicitation rate-limit, audit log integrity).

### Backlog v1.8+ (siguientes, opcional)
- Publicación en PyPI (ahora `pip install git+...`).
- Tests E2E con binaries reales (`te`, `dscmd`, `superbi-mcp`).
- Marketplace de page templates + plugin system para BPA rules custom.
- Remote transport (HTTP + Entra OAuth).
- Storytelling scoring con análisis de varianza real sobre telemetry.

---

## Cómo verificar localmente

```bash
pip install -e .
python scripts/verify_mcp_server.py    # fresh-install e2e check (26 tools)
pytest tests/                          # 665 unit + integration tests
mypy --strict src/powerbi_orchestrator_mcp
ruff check src/powerbi_orchestrator_mcp
```

Resultado esperado: 26 tools/list, 665/665 tests passing, mypy+ruff clean, exit code 0.