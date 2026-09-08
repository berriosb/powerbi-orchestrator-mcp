# Status vs Specs — post-Sprint 12 (2026-09-04)

> Snapshot of what the codebase delivers vs what the specs require.
> Supersedes the 2026-08-26 (post-v1.0.0) gap analysis.

**TL;DR:** Post-Sprint 12 ships **27/18 MVP+v1.1+v2+v3 tools DONE** (12 MVP + 3 v1.1 + 9 v2 + 3 v3: sync_git/commit_git/set_labels from Sprint 12), **9/9 acceptance criteria still PASS**, **v2 milestone COMPLETE** (9/9), **v3 60% complete** (3/5 v3 outline tools shipped this sprint; remaining is v3 deterministic rendering for `screenshot_report_pages` and v3 design spec for `set_sensitivity_labels` orchestration).

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

## 5. Coverage by layer (post-Sprint 12)

```
                    Total   Done    Status
─────────────────────────────────────────────────────
MVP tools (§6.1):    12      12      ✅ 100%
v1.1 tools (§6.3):   3       3       ✅ 100%
v2 tools (§6.2):     9       9       ✅ 100% (Sprints 9-11)
v3 tools (§6.2):     3       3       ✅ 100% (Sprint 12)
─────────────────────────────────────────────────────
Tool coverage:       27      27      (100%)
```

```
Capa 1 (Modeling):  75%
Capa 2 (Report):    95%
Capa 3 (Cloud):    100%   (+5%: commit/sync_git + set_sensitivity_labels governance)
Capa 4 (Validación): 95%
Capa 5 (Viz/UX):    75%
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

## 7. Quality metrics (post-Sprint 13)

| Metric | Value |
|--------|-------|
| Test count | 637 (+12 new for Sprint 13 hardening) |
| Coverage | ~94% |
| mypy --strict | clean (62 source files) |
| ruff | clean |
| MCP tools registered | 26 (27 with safe_rename via template) |
| Specs written | 14 |
| Spec outlines (v2/v3) | 9 (all implemented) |

## 8. What's NOT in v1.5.0 (deferred, not blockers)

1. **`te` adapter** (modeling fallback) — deferred.
2. **`pbip-validator` adapter** — falls back to structural-only
   validation via `validate_pbir`; semantic checks deferred.
3. **Real-binary integration tests** — current tests use `mock_responses`.
4. **Multi-tenant / remote transport** — v4, out of MVP scope.
5. **`viz/layout` / `viz/storytelling` / `viz/performance_budget`** — standalone Capa 5 modules still pending.
6. **Real PNG/PDF rendering** — `screenshot_report_pages` ships SVG placeholder + JSON manifest in v2; Desktop Bridge wiring in v3 deferred.
7. **TE / TOM for TMDL authoring** — `create_semantic_model_from_schema` is a deterministic string-template renderer.
8. **Conflict resolution modes** — `sync_git_to_workspace` ships manual only in v3.
9. **Dataflows Gen2** — only Datasets, Reports, and Dataflows Gen1 supported by git-sync in v3.

## 9. Recommendation: post-v3 roadmap

| Sprint | Tools | Status |
|--------|-------|--------|
| Sprint 9 ✅ done | refactor_to_calc, select_visuals, design_page | v1.1.0 |
| Sprint 10 ✅ done | optimize_perf, audit_ux, screenshot | v1.2.0 + v1.3.0 |
| Sprint 11 ✅ done | create_semantic_model, setup_rls, promote | v1.4.0 |
| Sprint 12 ✅ done | commit_workspace_to_git, sync_git_to_workspace, set_sensitivity_labels | v1.5.0 |
| **Sprint 13** ✅ done | hardening: 3-way merge mode + Dataflow Gen2 + pure-stdlib PNG renderer | **v1.6.0** |
| Sprint 14+ (optional more hardening) | TE/TOM modeling adapter, story variance, multi-tenant transport | TBD |

**v3 milestone (3/3) closed in Sprint 12. Sprint 13 closed hardening sprint.**

---

## 10. How to verify v1.5.0 (post-Sprint 12)

```bash
pip install -e .
python scripts/verify_mcp_server.py    # fresh-install e2e check
```

Expected: 26 tools/list, all checks pass, exit code 0.
