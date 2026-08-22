# IMPLEMENTATION PLAN v1.0 — powerbi-orchestrator-mcp

> Plan de 4 semanas para MVP ambicioso (12 tools). Semana a semana, con
> criterios de salida verificables.

**Fecha de inicio estimada:** cuando Bastian apruebe.
**Responsable:** codehak.
**Modelo de trabajo:** dev senior a tiempo completo + revisión diaria de Bastian.

---

## Semana 1 — Scaffold + Orquestación (Capa 6)

### Objetivo
Servidor arranca con 3 tools funcionales: `connect_target`, `plan_change`, `apply_plan`.
Elicitation operativa. Audit log con HMAC chain.

### Tasks

| # | Task | Tiempo | Dependencias |
|---|------|--------|--------------|
| 1.1 | Scaffold repo (pyproject.toml, src/, tests/, CI) | 2h | - |
| 1.2 | Implementar `server.py` con FastMCP + 3 tools dummy | 2h | 1.1 |
| 1.3 | Implementar `context.py` (SessionContext + SQLite WAL) | 3h | 1.2 |
| 1.4 | Implementar `elicitation.py` (MCP 2025-06-18 wrapper) | 3h | 1.2 |
| 1.5 | Implementar `audit.py` (SQLite + HMAC chain) | 4h | 1.3 |
| 1.6 | Implementar `planner.py` (templates MVP: safe_rename, audit, deploy, regression) | 6h | 1.2 |
| 1.7 | Implementar `rollback.py` (engine + steps) | 4h | 1.3 |
| 1.8 | Tests unitarios (pytest) | 4h | 1.3-1.7 |
| 1.9 | CI: ruff + mypy --strict + pytest + coverage | 2h | 1.8 |
| 1.10 | Implementar tools MVP `connect_target` | 3h | 1.3 |
| 1.11 | Implementar tools MVP `plan_change` + `apply_plan` | 4h | 1.5, 1.6, 1.7 |

**Total Semana 1:** ~37h.

### Criterios de salida

- [ ] `pip install -e .` funciona en Linux + macOS + Windows.
- [ ] `powerbi-orchestrator-mcp --start` arranca y registra 3 tools.
- [ ] `connect_target("pbip_folder", "./fixtures/test.pbip")` retorna
  engines_available + session_id.
- [ ] `plan_change(template="safe_rename", ...)` genera YAML válido.
- [ ] `apply_plan(plan_id, dry_run=true)` ejecuta sin tocar archivos.
- [ ] Audit log SQLite se puede verificar con `python -m orchestrator.audit verify`.
- [ ] CI pasa: ruff, mypy --strict, pytest con >80% coverage en `src/orchestrator/`.

---

## Semana 2 — Engines + safe_rename (Capas 1, 2, 6)

### Objetivo
`safe_rename` funcional end-to-end con rollback atómico. 4 engine adapters
implementados con fallback dinámico.

### Tasks

| # | Task | Tiempo | Dependencias |
|---|------|--------|--------------|
| 2.1 | Implementar `engines/base.py` (Protocols) | 2h | - |
| 2.2 | Implementar `engines/modeling_mcp.py` (subprocess) | 6h | 2.1 |
| 2.3 | Implementar `engines/te_cli.py` (subprocess) | 4h | 2.1 |
| 2.4 | Implementar `engines/superbi.py` (subprocess Windows) | 3h | 2.1 |
| 2.5 | Implementar `engines/pbip_validator.py` (subprocess) | 2h | 2.1 |
| 2.6 | Implementar `engines/dscmd.py` (subprocess Windows) | 2h | 2.1 |
| 2.7 | Implementar `engines/selector.py` (selección dinámica) | 4h | 2.2-2.6 |
| 2.8 | Implementar `tools/safe_rename.py` (orquestador) | 8h | 1.11, 2.7 |
| 2.9 | Fixture PBIP de prueba (`tests/fixtures/sample.pbip/`) | 3h | - |
| 2.10 | Tests integration safe_rename (mock + fixture) | 6h | 2.8, 2.9 |
| 2.11 | Tests rollback e2e | 4h | 2.8 |

**Total Semana 2:** ~44h.

### Criterios de salida

- [ ] 4 adapters implementados + selector dinámico funcional.
- [ ] `safe_rename` propaga a modelo + report bindings (sin M en MVP).
- [ ] Rollback atómico: si cualquier step falla, vuelve al estado pre-rename.
- [ ] Test e2e: rename + rollback verificado con fixture.
- [ ] Coverage >80% en `src/engines/`.
- [ ] Documentado: `docs/engines-setup.md` con cómo instalar cada engine.

---

## Semana 3 — Cloud + deploy (Capa 3)

### Objetivo
`deploy_to_workspace` + `run_refresh` funcionales con auth robusta, audit
cloud, y pre-deploy gate. Azure Identity funcionando.

### Tasks

| # | Task | Tiempo | Dependencias |
|---|------|--------|--------------|
| 3.1 | Implementar `cloud/auth.py` (Azure Identity async) | 4h | - |
| 3.2 | Implementar `cloud/fabric_client.py` (httpx async + retry) | 6h | 3.1 |
| 3.3 | Implementar `cloud/refresh.py` (async + cancel) | 4h | 3.2 |
| 3.4 | Implementar `cloud/audit_cloud.py` (integración con `audit.py`) | 2h | 3.2, 1.5 |
| 3.5 | Implementar `tools/deploy_to_workspace.py` | 6h | 3.2, 1.11 |
| 3.6 | Implementar `tools/run_refresh.py` | 3h | 3.3 |
| 3.7 | Implementar `cloud/refresh_doctor.py` (diagnóstico automático) | 4h | 3.3 |
| 3.8 | Tests con mock del REST API | 4h | 3.5, 3.6 |
| 3.9 | Smoke test opcional con credenciales reales | 2h | 3.5 |
| 3.10 | Documentar scopes SPN en `README.md` | 2h | 3.1 |

**Total Semana 3:** ~37h.

### Criterios de salida

- [ ] Auth funciona con interactive + SPN.
- [ ] `deploy_to_workspace` publica PBIP a workspace real con refresh schedule.
- [ ] `run_refresh` con `wait=true` completa el ciclo.
- [ ] Pre-deploy gate corre antes de cualquier write.
- [ ] Elicitation para workspaces tagged `production`.
- [ ] Audit log incluye operaciones cloud.
- [ ] `refresh_doctor` diagnostica correctamente los 5 errores comunes.
- [ ] 0 secretos en logs (test automatizado).

---

## Semana 4 — Validación + viz/UX + release (Capas 4, 5)

### Objetivo
Audit completo + theme/WCAG + dictionary. Release v0.1.0 en PyPI + Docker.

### Tasks

| # | Task | Tiempo | Dependencias |
|---|------|--------|--------------|
| 4.1 | Implementar `validation/bpa_runner.py` (wrapper `te bpa`) | 3h | 2.3 |
| 4.2 | Implementar `validation/dax_linter.py` (10 anti-patterns) | 6h | - |
| 4.3 | Implementar `validation/dax_regression.py` | 6h | 2.2 |
| 4.4 | Implementar `validation/model_diff.py` | 4h | - |
| 4.5 | Implementar `validation/accessibility/` (WCAG AA) | 6h | - |
| 4.6 | Implementar `validation/pre_deploy_gate.py` | 3h | 4.1-4.5 |
| 4.7 | Implementar `viz/visual_registry.py` (52+ visuales) | 6h | - |
| 4.8 | Implementar `viz/theme.py` (palette + WCAG contrast) | 4h | 4.5 |
| 4.9 | Implementar `viz/performance_budget.py` | 4h | - |
| 4.10 | Implementar `tools/audit_model_and_report.py` | 4h | 4.1-4.9 |
| 4.11 | Implementar `tools/apply_theme_and_accessibility_rules.py` | 4h | 4.5, 4.8 |
| 4.12 | Implementar `tools/generate_data_dictionary.py` | 4h | 2.2 |
| 4.13 | Tests integration completos | 6h | 4.10-4.12 |
| 4.14 | Workflow 1 e2e (de CSV a publicado con RLS) | 4h | 2.8, 3.5, 4.10 |
| 4.15 | Release PyPI + Docker image | 3h | 4.13 |
| 4.16 | README + docs finales | 3h | 4.15 |

**Total Semana 4:** ~70h.

### Criterios de salida (= MVP done)

- [ ] 12 tools MVP implementados.
- [ ] Workflow 1 funciona end-to-end.
- [ ] Tests pasan con coverage >80% en capas 4, 5, 6.
- [ ] `mypy --strict` + `ruff check` limpios.
- [ ] `pip install powerbi-orchestrator-mcp` funciona en 3 OS.
- [ ] Docker image `powerbi-orchestrator-mcp:0.1.0` publicada.
- [ ] README con quickstart + scopes SPN + ejemplos.
- [ ] GitHub release v0.1.0 con changelog.

---

## Riesgos del plan

| # | Riesgo | Mitigación |
|---|--------|-----------|
| 1 | Semana 2 se atrasa por complejidad de subprocess engines | Día 6: revisar; si atrasado, simplificar MVP sin Super BI (Windows fallback) y documentar. |
| 2 | Azure Identity tiene issues con SPN en CI | Mock exhaustivo + smoke opt-in; documentar setup manual. |
| 3 | WCAG auditor incompleto (false negatives) | Documentar como heurístico; Desktop reload sigue siendo source of truth. |
| 4 | Tests integration lentos (>5 min total) | Marcarlos como `@pytest.mark.slow`; correr solo en nightly. |
| 5 | Release PyPI falla por permissions | Configurar token desde día 1; test en TestPyPI primero. |

---

## Hitos de revisión con Bastian

| Día | Hito |
|-----|------|
| Lunes | Inicio Semana 1. |
| Viernes Semana 1 | Demo: 3 tools funcionales + audit log verificado. |
| Miércoles Semana 2 | Checkpoint: engines integrados. |
| Viernes Semana 2 | Demo: `safe_rename` con rollback verificado. |
| Miércoles Semana 3 | Checkpoint: deploy end-to-end. |
| Viernes Semana 3 | Demo: `deploy_to_workspace` real a workspace dev. |
| Miércoles Semana 4 | Checkpoint: audit completo funcionando. |
| Viernes Semana 4 | Release v0.1.0. |

---

## Recursos

- **Documentación de apoyo:** docs.microsoft.com REST API, MCP spec 2025-06-18,
  Power BI REST API reference, SQLBI articles.
- **Fixtures:** crear PBIP de muestra con 3 tablas, 5 medidas, 2 visuales.
- **SPN de test:** configurar en día 1 con scopes mínimos.

---

> **Después del MVP:** v2 añade `refactor_to_calculation_groups` completo,
> `design_report_page_from_requirements`, y Super BI integration.
