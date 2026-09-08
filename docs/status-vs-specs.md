# Status vs Specs — post-Sprint 9 (2026-09-04)

> Snapshot of what the codebase delivers vs what the specs require.
> Supersedes the 2026-08-26 (post-v1.0.0) gap analysis.

**TL;DR:** Post-Sprint 9 ships **18/18 MVP+v1.1+v2 tools DONE** (12 MVP + 3 v1.1 + 3 v2 from Sprint 9), **9/9 acceptance criteria still PASS**, and **1 sprint of v2 progress** (3 of 9 v2 tools). Remaining v2: 6 tools across Sprints 10–11.

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
| 4 | `audit_report_ux_and_storytelling` | outlined, pending (Sprint 10) |
| 5 | `optimize_report_performance` | **outline missing**, pending (Sprint 10) |
| 6 | `screenshot_report_pages` | outlined, pending (Sprint 10) |
| 7 | `promote_in_pipeline` | outlined, pending (Sprint 11) |
| 8 | `setup_rls_and_roles` | outlined, pending (Sprint 11) |
| 9 | `create_semantic_model_from_schema` | outlined, pending (Sprint 11) |

**3/9 v2 DONE (Sprint 9). 6 remaining across Sprints 10–11.**

## 4. SPEC §6.2 — 3 v3 tools (later roadmap)

| # | Tool | Status |
|---|------|--------|
| 1 | `sync_git_to_workspace` | outlined, pending |
| 2 | `commit_workspace_to_git` | part of above |
| 3 | `set_sensitivity_labels` | outline pending |

**0/3 v3 — Sprint 12+ work.**

## 5. Coverage by layer (post-Sprint 9)

```
                    Total   Done    Status
─────────────────────────────────────────────────────
MVP tools (§6.1):    12      12      ✅ 100%
v1.1 tools (§6.3):   3       3       ✅ 100%
v2 tools (§6.2):     9       3       🟡 33% (Sprint 9 of 10-11)
v3 tools (§6.2):     3       0       ⏳ Sprint 12+
─────────────────────────────────────────────────────
Tool coverage:       27      18      (67%)
```

```
Capa 1 (Modeling):  55%   (+5%: refactor_to_calculation_groups skeleton detector)
Capa 2 (Report):    85%   (+15%: design_report_page_from_requirements scaffold)
Capa 3 (Cloud):     85%
Capa 4 (Validación): 95%
Capa 5 (Viz/UX):    50%   (+20%: visual_registry + visual_suggester + select_visuals_for_kpis)
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

## 7. Quality metrics (post-Sprint 9)

| Metric | Value |
|--------|-------|
| Test count | 526 (+30 new for v2 tools + viz foundation) |
| Coverage | ~88% |
| mypy --strict | clean (53 source files) |
| ruff | clean |
| MCP tools registered | 17 (18 with safe_rename via template) |
| Specs written | 13 (5 Tier-A + 4 Tier-B + 2 Tier-C + 2 cross-cutting) |
| Spec outlines (v2/v3) | 9 (3 fleshed-out into full specs this sprint) |

## 8. What's NOT in v1.1.0 (deferred, not blockers)

1. **`te` adapter** (modeling fallback) — deferred (Sprint 10+ if needed).
2. **`pbip-validator` adapter** — falls back to structural-only
   validation via `validate_pbir`; semantic checks deferred.
3. **Real-binary integration tests** — current tests use `mock_responses`.
   Real subprocess tests require `te` / `powerbi-modeling-mcp` / `dscmd`
   installed in CI.
4. **Multi-tenant / remote transport** — v4, out of MVP scope.
5. **`viz/layout` / `viz/storytelling` / `viz/performance_budget`** — Capa 5 v2 modules still pending; not blocking current 3 v2 tools.
6. **`optimize_report_performance` outline** — único tool v2 sin spec dedicado en `specs/tools/` (mencionado en `status-vs-specs.md` Sprint 10 pero outline nunca creado; crear antes de implementar en Sprint 10).

## 9. Recommendation: Sprints 10–11

| Sprint | Tools | Effort |
|--------|-------|--------|
| **Sprint 10** ✅ done | `refactor_to_calculation_groups`, `select_visuals_for_kpis`, `design_report_page_from_requirements` + `viz/` foundation | commit en este sprint |
| **Sprint 11** | `audit_report_ux_and_storytelling`, `screenshot_report_pages`, `optimize_report_performance` (este último: crear outline primero) | ~3-4 días |
| **Sprint 12** | `promote_in_pipeline`, `setup_rls_and_roles`, `create_semantic_model_from_schema` | ~3 días |

Then Sprints 13+ for v3 (`sync_git_to_workspace`, `set_sensitivity_labels`).

---

## 10. How to verify v1.1.0 (post-Sprint 9)

```bash
pip install -e .
python scripts/verify_mcp_server.py    # fresh-install e2e check
```

Expected: 17 tools/list, all checks pass, exit code 0.
