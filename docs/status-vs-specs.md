# Status vs Specs — 2026-08-26 (post-v1.0.0)

> Snapshot of what `v1.0.0` delivers vs what the specs require. This
> supersedes the earlier gap analysis from 2026-08-26 (pre-v0.1.0).

**TL;DR:** v1.0.0 ships **15/15 MVP+v1.1 tools DONE**, **9/9 acceptance
criteria PASS**, and **all critical gaps CLOSED**. The remaining work
is the v2 / v3 / hardening roadmap (Sprints 9+).

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

## 3. SPEC §6.2 — 9 v2 tools (post-v1.0.0 roadmap)

| # | Tool | Status |
|---|------|--------|
| 1 | `refactor_to_calculation_groups` | outlined, pending |
| 2 | `promote_in_pipeline` | outlined, pending |
| 3 | `design_report_page_from_requirements` | outlined, pending |
| 4 | `select_visuals_for_kpis` | outlined, pending |
| 5 | `audit_report_ux_and_storytelling` | outlined, pending |
| 6 | `optimize_report_performance` | outlined, pending |
| 7 | `setup_rls_and_roles` | outlined, pending |
| 8 | `create_semantic_model_from_schema` | outlined, pending |
| 9 | `screenshot_report_pages` | outlined, pending |

**0/9 v2 — Sprint 9-11 work.**

## 4. SPEC §6.2 — 3 v3 tools (later roadmap)

| # | Tool | Status |
|---|------|--------|
| 1 | `sync_git_to_workspace` | outlined, pending |
| 2 | `commit_workspace_to_git` | part of above |
| 3 | `set_sensitivity_labels` | outline pending |

**0/3 v3 — Sprint 12+ work.**

## 5. Coverage by layer (final, v1.0.0)

```
                    Total   Done    Status
─────────────────────────────────────────────────────
MVP tools (§6.1):    12      12      ✅ 100%
v1.1 tools (§6.3):   3       3       ✅ 100%
v2 tools (§6.2):     9       0       ⏳ Sprint 9-11
v3 tools (§6.2):     3       0       ⏳ Sprint 12+
─────────────────────────────────────────────────────
Tool coverage:       27      15      (56%)
```

```
Capa 1 (Modeling):  50%
Capa 2 (Report):    70%
Capa 3 (Cloud):     85%
Capa 4 (Validación): 95%
Capa 5 (Viz/UX):     30%
Capa 6 (Orquestación): 100%
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

## 7. Quality metrics (final)

| Metric | Value |
|--------|-------|
| Test count | 496 |
| Coverage | 86% |
| mypy --strict | clean (48 source files) |
| ruff | clean |
| MCP tools registered | 14 (15 with safe_rename via template) |
| Specs written | 13 (5 Tier-A + 4 Tier-B + 2 Tier-C + 2 cross-cutting) |
| Spec outlines (v2/v3) | 9 |

## 8. What's NOT in v1.0.0 (deferred, not blockers)

1. **`te` adapter** (modeling fallback) — Sprint 9+ when needed.
2. **`pbip-validator` adapter** — falls back to structural-only
   validation via `validate_pbir`; semantic checks deferred.
3. **`viz/visual_registry.py` + sibling modules** — Capa 5 modules
   that `select_visuals_for_kpis` (v2) and `design_report_page_from_requirements`
   (v2) consume. Out of v1.0.0 scope per spec §6.2.
4. **Real-binary integration tests** — current tests use `mock_responses`.
   Real subprocess tests require `te` / `powerbi-modeling-mcp` / `dscmd`
   installed in CI (post-v1.0.0).
5. **Multi-tenant / remote transport** — v4, out of MVP scope.
6. **`te` modeling_engine fallback chain** — currently empty; `te`
   adapter is Sprint 9+ work.

## 9. Recommendation: Sprint 9+ roadmap

Plan to ship v2 tools in 3 sprints (Sprints 9-11), each closing 3 tools:

| Sprint | Tools | Effort |
|--------|-------|--------|
| **Sprint 9** | `refactor_to_calculation_groups`, `select_visuals_for_kpis`, `design_report_page_from_requirements` | ~3-4 days (requires `viz/visual_registry.py` stub) |
| **Sprint 10** | `audit_report_ux_and_storytelling`, `optimize_report_performance`, `screenshot_report_pages` | ~3 days |
| **Sprint 11** | `promote_in_pipeline`, `setup_rls_and_roles`, `create_semantic_model_from_schema` | ~3 days |

Then Sprint 12+ for v3 (`sync_git_to_workspace`, `set_sensitivity_labels`).

---

## 10. How to verify v1.0.0

```bash
pip install powerbi-orchestrator-mcp==1.0.0
python scripts/verify_mcp_server.py    # fresh-install e2e check
```

Expected: 14 tools/list, all 8 checks pass, exit code 0.
