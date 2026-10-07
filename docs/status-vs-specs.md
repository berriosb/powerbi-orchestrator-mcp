# Status vs Specs — Release v1.11.0 (2026-10-01)

> Snapshot of what the codebase delivers vs what the specs require.
> Supersedes the post-Sprint 12 / post-Sprint 14 gap analysis.

**TL;DR:** v1.11.0 delivers **28 tools DONE** (12 MVP + 3 v1.1 + 9 v2 + 3 v3 + `powerbi_health` diagnóstico + `execute_dax_query` DAX en vivo), **9/9 acceptance criteria PASS**, **v2 y v3 milestones COMPLETE**, y enterprise hardening listo para producción.

---

## 1. SPEC §6.1 — 12 MVP tools

| # | Tool | Status |
|---|------|--------|
| 1 | `connect_target` | ✅ DONE |
| 2 | `plan_change` | ✅ DONE |
| 3 | `apply_plan` | ✅ DONE |
| 4 | `safe_rename` (via plan_change template) | ✅ DONE |
| 5 | `audit_model_and_report` | ✅ DONE |
| 6 | `deploy_to_workspace` | ✅ DONE |
| 7 | `run_refresh` | ✅ DONE |
| 8 | `run_dax_regression` | ✅ DONE |
| 9 | `diff_models` | ✅ DONE |
| 10 | `pre_deploy_check` | ✅ DONE |
| 11 | `generate_data_dictionary` | ✅ DONE |
| 12 | `apply_theme_and_accessibility_rules` | ✅ DONE |

**12/12 DONE.**

## 2. SPEC §6.3 — 3 v1.1 tools

| # | Tool | Status |
|---|------|--------|
| 1 | `add_measure_with_validation` | ✅ DONE |
| 2 | `create_report_from_dataset` | ✅ DONE |
| 3 | `edit_report_visual` | ✅ DONE |

**3/3 DONE. Total: 15/15 tools.**

## 3. SPEC §6.2 — 9 v2 tools

| # | Tool | Status |
|---|------|--------|
| 1 | `refactor_to_calculation_groups` | ✅ DONE (Sprint 9) |
| 2 | `select_visuals_for_kpis` | ✅ DONE (Sprint 9) |
| 3 | `design_report_page_from_requirements` | ✅ DONE (Sprint 9) |
| 4 | `optimize_report_performance` | ✅ DONE (Sprint 10 slice 1) |
| 5 | `audit_report_ux_and_storytelling` | ✅ DONE (Sprint 10 slice 2) |
| 6 | `screenshot_report_pages` | ✅ DONE (Sprint 10 slice 2 — placeholder/SVG mode) |
| 7 | `create_semantic_model_from_schema` | ✅ DONE (Sprint 11) |
| 8 | `setup_rls_and_roles` | ✅ DONE (Sprint 11) |
| 9 | `promote_in_pipeline` | ✅ DONE (Sprint 11) |

**9/9 v2 DONE (Sprints 9 + 10 + 11). Milestone v2 complete.**

## 4. SPEC §6.2 — 3 v3 tools (later roadmap)

| # | Tool | Status |
|---|------|--------|
| 1 | `commit_workspace_to_git` | ✅ DONE (Sprint 12) |
| 2 | `sync_git_to_workspace` | ✅ DONE (Sprint 12 — manual conflict mode) |
| 3 | `set_sensitivity_labels` | ✅ DONE (Sprint 12 — ADMIN scope gated) |

**3/3 v3 DONE (Sprint 12).**

## 5. Coverage by layer (Release v1.11.0)

```
                    Total   Done    Status
─────────────────────────────────────────────────────
MVP tools (§6.1):    12      12      ✅ 100%
v1.1 tools (§6.3):   3       3       ✅ 100%
v2 tools (§6.2):     9       9       ✅ 100% (Sprints 9-11)
v3 tools (§6.2):     3       3       ✅ 100% (Sprint 12)
Extensions:          2       2       ✅ 100% (powerbi_health, execute_dax_query)
─────────────────────────────────────────────────────
Tool coverage:       28      28      (100% exposed in FastMCP)
```

```
Capa 0 (Transport):  100%   (Streamable HTTP + Entra ID OAuth)
Capa 1 (Modeling):   100%   (powerbi-modeling-mcp, TE adapter, TMDL scaffold)
Capa 2 (Report):     100%   (report_python PBIR parser/writer)
Capa 3 (Cloud):      100%   (REST Fabric, Git sync, Purview labels, DAX queries)
Capa 4 (Validación): 100%   (BPA, DAX linter, regression runner, WCAG)
Capa 5 (Viz/UX):     100%   (visual registry, suggester, layout, storytelling)
Capa 6 (Orquestación): 100% (planner, rollback engine, HMAC audit log)
```

## 6. Acceptance criteria SPEC §6.5 (9/9 ✅)

| # | Criterion | Status |
|---|-----------|--------|
| 1 | `pip install` cross-platform | ✅ |
| 2 | Config JSON en 3 clientes MCP | ✅ |
| 3 | Sub-flujo MVP workflow 01 end-to-end | ✅ |
| 4 | `safe_rename` propagation + rollback | ✅ |
| 5 | `audit_model_and_report` reproducible score | ✅ |
| 6 | `deploy_to_workspace` mock + real paths | ✅ |
| 7 | Audit log SQLite + HMAC chain | ✅ |
| 8 | Test coverage >80% in layers 4 + 6 | ✅ |
| 9 | `mypy --strict` + `ruff check` clean | ✅ |

## 7. Quality metrics (Release v1.11.0)

| Metric | Value |
|--------|-------|
| Test count | 905+ passed (26 e2e/binary tests gracefully skipped) |
| Coverage | ~90% |
| mypy --strict | clean (124 source files) |
| ruff | clean |
| MCP tools registered | 28 tools expuestas en FastMCP |
| Specs cross-cutting | 4 (transport, publish, e2e, supersede) sincronizados |
| Hardening backlog | 0 items pendientes ✅ |

## 8. Estado de ítems diferidos y notas de diseño

1. **`te` adapter** (modeling fallback) — ✅ IMPLEMENTADO en Sprint 14 (`te_adapter.py`).
2. **`pbip-validator` adapter** — Reemplazado por validador PBIR nativo (`validate_pbir` en `report_python.py`) debido a que Microsoft no ha publicado paquete oficial.
3. **Tests de integración con binarios reales** — Scaffold `tests/e2e/` y `.github/workflows/e2e-nightly.yml` listos; placeholders activos para activación opcional.
4. **Transporte remoto / HTTP** — ✅ IMPLEMENTADO en v1.10.0 (`--transport http`, validación de tokens Entra JWT con JWKS).
5. **Renderizado de imágenes** — ✅ `screenshot_report_pages` implementa SVG placeholder y PNG nativo (stdlib con compresión `zlib`). Desktop Bridge con captura interactiva reservado para roadmap futuro.
6. **Dataflows Gen2** — ✅ Soportado en sincronización Git.
7. **`execute_dax_query`** — ✅ IMPLEMENTADO en v1.11.0 vía REST API de Power BI Service / Fabric con soporte para impersonación RLS.

## 9. Resumen de releases

| Release | Hitos principales |
|---------|-------------------|
| v1.0.0  | MVP (12 tools) |
| v1.1.0  | Tools v1.1 (add_measure, create_report, edit_visual) |
| v1.2.0 - v1.4.0 | Tools v2 (calc groups, layout, UX audit, RLS, pipeline promote) |
| v1.5.0  | Tools v3 (git sync, commit, sensitivity labels) |
| v1.6.0  | Hardening: 3-way merge, Dataflows Gen2, pure-stdlib PNG |
| v1.7.0  | TE adapter + Story variance regression analysis |
| v1.8.0  | Fabric REST endpoints extendidos |
| v1.9.0  | Health tool, Plan store SQLite persistente, Dockerfile, onboarding |
| v1.10.0 | HTTP transport opt-in, Entra ID JWT auth, CI publish workflow, E2E scaffold |
| **v1.11.0** | Enterprise hardening (TMDL multipart), `execute_dax_query`, contextvars isolation |

---

## 10. Cómo verificar la instalación

```bash
pip install -e .
python scripts/verify_mcp_server.py
```

Expected: 28 tools/list expuestas, JSON-RPC stdio handshake correcto, exit code 0.
