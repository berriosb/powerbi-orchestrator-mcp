# MVP STATUS — powerbi-orchestrator-mcp

> Estado de implementación vs specs. Última sync: 2026-08-21 (specs v0.1,
> código aún no escrito).

**Leyenda:**
- ❌ No implementado
- 🟡 En progreso
- ✅ Implementado + verificado (tests passing)
- ⚠️ Implementado parcial / best-effort

---

## Estado global

| Capa | Spec | Status código |
|------|------|---------------|
| 6 · Orquestación | [`../specs/01-orchestrator.md`](../specs/01-orchestrator.md) | ❌ |
| 3 · Cloud Fabric | [`../specs/02-cloud-fabric.md`](../specs/02-cloud-fabric.md) | ❌ |
| 4 · Validación | [`../specs/03-validation.md`](../specs/03-validation.md) | ❌ |
| 5 · Viz/UX | [`../specs/04-viz-ux.md`](../specs/04-viz-ux.md) | ❌ |
| 1-2 · Engines | [`../specs/05-engines-adapters.md`](../specs/05-engines-adapters.md) | ❌ |

## Tools MVP

| Tool | Spec | Status código |
|------|------|---------------|
| `connect_target` | [`../specs/01-orchestrator.md` §3.1](../specs/01-orchestrator.md) | ❌ |
| `plan_change` | [`../specs/01-orchestrator.md` §3.2](../specs/01-orchestrator.md) | ❌ |
| `apply_plan` | [`../specs/01-orchestrator.md` §3.3](../specs/01-orchestrator.md) | ❌ |
| `safe_rename` | [`../specs/tools/safe-rename.md`](../specs/tools/safe-rename.md) | ❌ |
| `audit_model_and_report` | [`../specs/tools/audit-model-and-report.md`](../specs/tools/audit-model-and-report.md) | ❌ |
| `deploy_to_workspace` | [`../specs/tools/deploy-to-workspace.md`](../specs/tools/deploy-to-workspace.md) | ❌ |
| `run_refresh` | [`../specs/02-cloud-fabric.md` §3](../specs/02-cloud-fabric.md) | ❌ |
| `run_dax_regression` | [`../specs/03-validation.md` §5](../specs/03-validation.md) | ❌ |
| `diff_models` | [`../specs/03-validation.md` §2.4](../specs/03-validation.md) | ❌ |
| `pre_deploy_check` | [`../specs/03-validation.md` §4](../specs/03-validation.md) | ❌ |
| `generate_data_dictionary` | ❌ sin spec dedicado (solo SPEC §4.5 una línea) | ❌ |
| `apply_theme_and_accessibility_rules` | [`../specs/04-viz-ux.md` §3](../specs/04-viz-ux.md) | ❌ |

## Tools MVP v1 — ubicación real de cada spec (corrección post-audit 2026-08-26)

El fix anterior (commit `3ad85c3`, 2026-08-21) agregó una sección "sin spec
dedicado" que quedó desactualizada tras esta auditoría. La realidad:

| Tool | Spec real | Estado |
|------|-----------|--------|
| `audit_model_and_report` | [`specs/tools/audit-model-and-report.md`](../specs/tools/audit-model-and-report.md) | ✅ spec dedicado completo |
| `safe_rename` | [`specs/tools/safe-rename.md`](../specs/tools/safe-rename.md) | ✅ spec dedicado completo |
| `deploy_to_workspace` | [`specs/tools/deploy-to-workspace.md`](../specs/tools/deploy-to-workspace.md) | ✅ spec dedicado completo |
| `connect_target` | [`specs/01-orchestrator.md` §3.1](../specs/01-orchestrator.md) | ✅ en spec por capa (orquestación) |
| `plan_change` | [`specs/01-orchestrator.md` §3.2](../specs/01-orchestrator.md) | ✅ en spec por capa (orquestación) |
| `apply_plan` | [`specs/01-orchestrator.md` §3.3](../specs/01-orchestrator.md) | ✅ en spec por capa (orquestación) |
| `run_refresh` | [`specs/02-cloud-fabric.md` §3](../specs/02-cloud-fabric.md) | ✅ en spec por capa (cloud) |
| `run_dax_regression` | [`specs/03-validation.md` §5](../specs/03-validation.md) | ✅ schema YAML completo en spec por capa |
| `diff_models` | [`specs/03-validation.md` §2.4](../specs/03-validation.md) | ✅ Pydantic models + breaking rules en spec por capa |
| `pre_deploy_check` | [`specs/03-validation.md` §4](../specs/03-validation.md) | ✅ schema YAML completo en spec por capa |
| `apply_theme_and_accessibility_rules` | [`specs/04-viz-ux.md` §3](../specs/04-viz-ux.md) | ✅ schema YAML completo en spec por capa |
| `generate_data_dictionary` | **❌ NO TIENE SPEC DEDICADO** | ⚠️ solo descripción de 1 línea en SPEC §4.5 |

**Decisión arquitectónica adoptada (2026-08-26):** un tool MVP v1 puede
tener su spec dedicado en `specs/tools/` (si su lógica es ortogonal a una
capa) o vivir dentro del spec de la capa que lo implementa (si es parte
del dominio de esa capa). Esto evita proliferación de archivos y
duplicación de schemas. Ver [`specs/README.md` §Tools MVP v1 documentadas en specs por capa](../specs/README.md).

**Acción concreta:** crear spec dedicado para `generate_data_dictionary`
antes de arrancar Semana 4 (es una sola página con input/output schema;
referencias existentes: SPEC §4.5, workflow 01 §Fase 6, workflow 02 §Fase 6,
IMPLEMENTATION-PLAN §4.12).

## Workflows

| Workflow | Spec | Status código |
|----------|------|---------------|
| De CSV a publicado con RLS | [`../specs/workflows/01-from-csv-to-published-report.md`](../specs/workflows/01-from-csv-to-published-report.md) | ❌ |
| Refactor a calc groups | [`../specs/workflows/02-refactor-to-calc-groups.md`](../specs/workflows/02-refactor-to-calc-groups.md) | ❌ (workflow v2, depende de `refactor_to_calculation_groups`) |

---

## Specs pendientes de detalle (v2/v3)

| Tool | Versión | Spec |
|------|---------|------|
| `refactor_to_calculation_groups` | v2 | ❌ falta spec |
| `promote_in_pipeline` | v2 | ❌ falta spec |
| `design_report_page_from_requirements` | v2 | ❌ falta spec |
| `select_visuals_for_kpis` | v2 | ❌ falta spec |
| `audit_report_ux_and_storytelling` | v2 | ❌ falta spec |
| `setup_rls_and_roles` | v2 | ❌ falta spec |
| `create_semantic_model_from_schema` | v2 | ❌ falta spec |
| `sync_git_to_workspace` | v3 | ❌ falta spec |
| `screenshot_report_pages` | v2 (corrección: era v3) | ❌ falta spec |
| `set_sensitivity_labels` | v3 | ❌ falta spec |

---

## Roadmap de implementación

### Semana 1 (Scaffold + orquestación)
- [ ] Scaffold Python 3.11 + FastMCP + pyproject.toml.
- [ ] Implementar `server.py` con 3 tools dummy.
- [ ] Implementar `context.py` (SessionContext).
- [ ] Implementar `elicitation.py` wrapper MCP 2025-06-18.
- [ ] Implementar `audit.py` (SQLite HMAC chain).
- [ ] Tests unitarios con pytest.
- [ ] CI: ruff + mypy --strict + pytest.

### Semana 2 (Engines + safe_rename)
- [ ] Implementar `engines/base.py` (Protocol).
- [ ] Implementar `engines/modeling_mcp.py` (subprocess a `powerbi-modeling-mcp`).
- [ ] Implementar `engines/pbip_validator.py` (subprocess a `pbip-validator`).
- [ ] Implementar `engines/superbi.py` (subprocess a `superbi-mcp`, Windows).
- [ ] Implementar `engines/te_cli.py` (subprocess a `te`).
- [ ] Implementar `engines/selector.py` (selección dinámica).
- [ ] Implementar `tools/safe_rename.py` end-to-end.
- [ ] Fixture PBIP de prueba.
- [ ] Test e2e safe_rename con rollback.

### Semana 3 (Cloud + deploy)
- [ ] Implementar `cloud/auth.py` (Azure Identity).
- [ ] Implementar `cloud/fabric_client.py` (REST async).
- [ ] Implementar `cloud/refresh.py`.
- [ ] Implementar `cloud/audit_cloud.py`.
- [ ] Implementar `tools/deploy_to_workspace.py`.
- [ ] Implementar `tools/run_refresh.py`.
- [ ] Tests con mock + smoke opt-in.

### Semana 4 (Validación + viz/UX)
- [ ] Implementar `validation/bpa_runner.py`.
- [ ] Implementar `validation/dax_linter.py`.
- [ ] Implementar `validation/dax_regression.py`.
- [ ] Implementar `validation/model_diff.py`.
- [ ] Implementar `validation/accessibility/`.
- [ ] Implementar `validation/pre_deploy_gate.py`.
- [ ] Implementar `viz/visual_registry.py`.
- [ ] Implementar `viz/theme.py`.
- [ ] Implementar `viz/performance_budget.py`.
- [ ] Implementar `tools/audit_model_and_report.py`.
- [ ] Implementar `tools/apply_theme_and_accessibility_rules.py`.
- [ ] Implementar `tools/generate_data_dictionary.py`.
- [ ] Tests integration.
- [ ] Docs: README + AGENTS.md update.
- [ ] Release v0.1.0 en PyPI + Docker.

---

## Criterios de "MVP done" (acceptance del [`SPEC.md`](../SPEC.md) §6.4)

- [ ] Instalación `pip install powerbi-orchestrator-mcp` funciona en Linux + macOS + Windows.
- [ ] Config JSON registrado en VS Code + Claude Desktop + OpenClaw sin errores.
- [ ] Workflow 1 (de CSV a reporte publicado con RLS) funciona end-to-end con un PBIP de prueba.
- [ ] `safe_rename` propaga a modelo + DAX + report bindings con rollback atómico verificado por test.
- [ ] `audit_model_and_report` devuelve score reproducible sobre el PBIP de prueba.
- [ ] `deploy_to_workspace` publica a un workspace real y refresh completa.
- [ ] Audit log SQLite con HMAC chaining verificable.
- [ ] Coverage de tests >80% en código de orquestación (capa 6) y validación (capa 4).
- [ ] `mypy --strict` limpio. `ruff check` limpio.

---

## Riesgos (top 14)

| # | Riesgo | Mitigación | Status |
|---|--------|-----------|--------|
| 1 | `powerbi-modeling-mcp` cambia API | Adapter + pin + tests contract | 🟡 spec'd |
| 2 | Cloud REST sin live test | Mocks + smoke opt-in | 🟡 spec'd |
| 3 | LLM escribe PBIR inválido | Validar + rollback atómico | 🟡 spec'd |
| 4 | Windows-only deps limitan Linux | Documentar matriz OS×feature | 🟡 spec'd |
| 5 | Agente publica a workspace equivocado | Elicitation + `--allow-prod` + audit | 🟡 spec'd |
| 6 | Plan YAML malformado en Git | Pydantic strict + pre-commit | 🟡 spec'd |
| 7 | Rollback falla parcialmente | `RollbackError` con paths | 🟡 spec'd |
| 8 | Elicitation spamea al usuario | Rate limit 1 cada 5s | 🟡 spec'd |
| 9 | Audit log crece sin límite | Rotación diaria + opt-in Log Analytics | 🟡 spec'd |
| 10 | Snapshot de PBIP grande | Comprimir zstd + retentar 7 días | 🟡 spec'd |
| 11 | Token expira mid-operation | Refresh transparente Azure Identity | 🟡 spec'd |
| 12 | Scope insuficiente SPN | Documentar + elicitar | 🟡 spec'd |
| 13 | API rate limit | Retry + circuit breaker | 🟡 spec'd |
| 14 | Credenciales refresh expiradas | refresh_doctor automático | 🟡 spec'd |

---

## Métricas de éxito

### Adopción (3 meses post-release)
- 50+ stars en GitHub.
- 5+ contributors externos.
- 10+ organizaciones usándolo en CI.
- 1+ case study público.

### Calidad técnica
- 0 secretos committed en history.
- Coverage >80% en capas 4, 5 y 6.
- Latencia p95 de `safe_rename` <10s para 100 measures.
- Latencia p95 de `audit_model_and_report` <30s modelo mediano.

### Diferenciación
- 3+ features que **ningún otro MCP** tiene juntos.
- 1+ paper/talk presentando la arquitectura.

---

> **Próximo paso cuando Bastian apruebe:** arrancar Semana 1.
> Antes: validar specs y resolver dudas con Bastian.
