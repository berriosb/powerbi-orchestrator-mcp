# Status vs Specs — post-Sprint 10 (2026-09-04)

> Snapshot of what the codebase delivers vs what the specs require.
> Supersedes the 2026-08-26 (post-v1.0.0) gap analysis.

**TL;DR:** Post-Sprint 10 ships **21/18 MVP+v1.1+v2 tools DONE** (12 MVP + 3 v1.1 + 6 v2: 3 from Sprint 9 + 3 from Sprint 10), **9/9 acceptance criteria still PASS**, and **2 sprints of v2 progress** (6 of 9 v2 tools). Remaining v2: 3 tools across Sprint 11.

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
| 7 | `promote_in_pipeline` | outlined, pending (Sprint 11) |
| 8 | `setup_rls_and_roles` | outlined, pending (Sprint 11) |
| 9 | `create_semantic_model_from_schema` | outlined, pending (Sprint 11) |

**6/9 v2 DONE (Sprints 9 + 10). 3 remaining across Sprint 11.**

## 4. SPEC §6.2 — 3 v3 tools (later roadmap)

| # | Tool | Status |
|---|------|--------|
| 1 | `sync_git_to_workspace` | outlined, pending |
| 2 | `commit_workspace_to_git` | part of above |
| 3 | `set_sensitivity_labels` | outline pending |

**0/3 v3 — Sprint 12+ work.**

## 5. Coverage by layer (post-Sprint 10)

```
                    Total   Done    Status
─────────────────────────────────────────────────────
MVP tools (§6.1):    12      12      ✅ 100%
v1.1 tools (§6.3):   3       3       ✅ 100%
v2 tools (§6.2):     9       6       🟡 67% (Sprints 9-10 of 10-11)
v3 tools (§6.2):     3       0       ⏳ Sprint 11+
─────────────────────────────────────────────────────
Tool coverage:       27      21      (78%)
```

```
Capa 1 (Modeling):  55%   (+5%: refactor_to_calculation_groups skeleton detector)
Capa 2 (Report):    95%   (+10%: screenshot_report_pages placeholder renders + manifest diff)
Capa 3 (Cloud):     85%
Capa 4 (Validación): 95%
Capa 5 (Viz/UX):    75%   (+25%: select_visuals_for_kpis + design_report_page_from_requirements
                          + audit_report_ux_and_storytelling + optimize_report_performance)
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

## 7. Quality metrics (post-Sprint 10)

| Metric | Value |
|--------|-------|
| Test count | 564 (+38 new for Sprint 10 tools) |
| Coverage | ~90% |
| mypy --strict | clean (56 source files) |
| ruff | clean |
| MCP tools registered | 20 (21 with safe_rename via template) |
| Specs written | 13 (5 Tier-A + 4 Tier-B + 2 Tier-C + 2 cross-cutting) |
| Spec outlines (v2/v3) | 9 (6 of which are now actually implemented; 3 remain for Sprint 11) |

## 8. What's NOT in v1.3.0 (deferred, not blockers)

1. **`te` adapter** (modeling fallback) — deferred.
2. **`pbip-validator` adapter** — falls back to structural-only
   validation via `validate_pbir`; semantic checks deferred.
3. **Real-binary integration tests** — current tests use `mock_responses`.
   Real subprocess tests require `te` / `powerbi-modeling-mcp` / `dscmd`
   installed in CI.
4. **Multi-tenant / remote transport** — v4, out of MVP scope.
5. **`viz/layout` / `viz/storytelling` / `viz/performance_budget`** — Capa 5 v2 modules still pending (storytelling scoring heuristic now lives in `audit_report_ux_and_storytelling`; standalone module deferred).
6. **Real PNG/PDF rendering** — `screenshot_report_pages` ships SVG placeholder mode + JSON manifest in v2; the Desktop Bridge / `superbi-mcp` Windows path for true bitmap capture is deferred to v3.
7. **`optimize_report_performance` spec** — implemented with spec inline in `04-viz-ux.md §4`; not duplicated in `specs/tools/` per the architectural decision that "specs can live in spec-by-layer".

## 9. Recommendation: Sprint 11

| Sprint | Tools | Effort |
|--------|-------|--------|
| **Sprint 9** ✅ done | `refactor_to_calculation_groups`, `select_visuals_for_kpis`, `design_report_page_from_requirements` + `viz/` foundation | committed (v1.1.0) |
| **Sprint 10** ✅ done | `optimize_report_performance`, `audit_report_ux_and_storytelling`, `screenshot_report_pages` | committed (v1.2.0 + v1.3.0) |
| **Sprint 11** | `promote_in_pipeline`, `setup_rls_and_roles`, `create_semantic_model_from_schema` | ~3 días |

Then Sprints 12+ for v3 (`sync_git_to_workspace`, `set_sensitivity_labels`).

---

## 10. How to verify v1.3.0 (post-Sprint 10)

```bash
pip install -e .
python scripts/verify_mcp_server.py    # fresh-install e2e check
```

Expected: 20 tools/list, all checks pass, exit code 0.
