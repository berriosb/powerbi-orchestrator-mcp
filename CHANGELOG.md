# Changelog

All notable changes to **powerbi-orchestrator-mcp** are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Note:** Per-release notes (with full PR lists, decisions, and rationale)
> live in the [`RELEASE-NOTES-vX.Y.Z.md`](./RELEASE-NOTES-vX.Y.Z.md) files
> at the repo root. This file is a condensed digest for quick lookup.

---

## [Unreleased]

### Added

- **Phase 1 (Sprint 16)** — Distribution & onboarding:
  - `examples/` directory: 3 reproducible workflows (`01-safe-rename`,
    `02-deploy-pbip`, `03-audit-then-fix`).
  - `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md` at repo root.
  - `docs/troubleshooting.md` — common errors with remediation.
- **Phase 2 (Sprint 16)** — Reliability & observability:
  - `powerbi_health` MCP tool (engine status, plan store stats, audit log size).
  - SQLite-backed `PlanExecutionStore` (replaces in-memory dict; survives restarts).
  - Structured logging via `structlog` for `apply_plan` + `_request` paths.

---

## [1.8.0] — 2026-09-14

### Added

- **`fabric_client` Power BI Service REST endpoints**: `create_item`,
  `delete_item`, `list_items`, `get_item`, `cancel_refresh`,
  `get_refresh_history`, `update_refresh_schedule`, `take_over_dataset`,
  `update_datasource`, `execute_queries`.
- PATCH method on `FabricClient` (was missing).
- Two httpx clients in `FabricClient` — one for Fabric Items API,
  one for Power BI Service REST — selected via `service="fabric"|"pbi"`.
- `deploy_to_workspace` real path: `create_item` (Fabric) →
  `update_refresh_schedule` → `refresh_dataset` (Power BI Service).

### Changed

- `superbi_mcp` adapter: documented `python_report` fallback as the
  intentional behavior (removed misleading `# TODO` markers).
- `MVP-STATUS.md` rewritten to reflect the actual post-Sprint 14 state
  (was lagging the code by ~3 sprints).
- Coverage: `optimize_report_performance` 28% → 100%,
  `audit_report_ux_and_storytelling` 33% → 100%, `bpa_runner` 55% → 100%,
  `engines/base.py` 69% → 92%, `engine_detector` 80% → 100%,
  `edit_report_visual` 81% → 100%. Total: 86% → 91%, 665 → 823 tests.

---

## [1.7.0] — 2026-09-04

### Added

- **Tabular Editor (TE) modeling adapter** — concrete `TabularEditorAdapter`
  with `mode='skeleton'` (default; synthetic success without spawning
  subprocess) and `mode='subprocess'` (real RPC when TE on PATH).
- **Story variance analysis** — deterministic PNG byte-comparison helper
  for visual regression between two screenshot directories.
- Hardening backlog closed (0 items remaining).

### Changed

- `create_semantic_model_from_schema` and `refactor_to_calculation_groups`
  delegate the TMDL write / calc-group persist to the TE adapter when
  injected; legacy callable writer path preserved for back-compat.

---

## [1.6.0] — 2026-09-04

Predecessor of v1.7.0; hardening sprint, no user-facing tools.

---

## [1.5.0] — 2026-09-04

### Added

- 3 v3 tools: `commit_workspace_to_git`, `sync_git_to_workspace`,
  `set_sensitivity_labels` (governance + Git integration).

---

## [1.4.0] — 2026-09-04

### Added

- 3 v2 tools (Sprint 11): `create_semantic_model_from_schema`,
  `setup_rls_and_roles`, `promote_in_pipeline`.

---

## [1.3.0] — 2026-09-04

### Added

- 3 v2 tools (Sprint 10): `optimize_report_performance`,
  `audit_report_ux_and_storytelling`, `screenshot_report_pages`
  (best-effort SVG wireframes + JSON manifests).

---

## [1.2.0] — 2026-09-04

### Added

- 3 v2 tools (Sprint 9): `refactor_to_calculation_groups`,
  `select_visuals_for_kpis`, `design_report_page_from_requirements`.

---

## [1.1.0] — 2026-09-04

### Added

- 3 v1.1 tools: `add_measure_with_validation`, `create_report_from_dataset`,
  `edit_report_visual` (SPEC §6.3).

---

## [1.0.0] — 2026-08-26

### Added

- 12 MVP tools: `connect_target`, `plan_change`, `apply_plan`,
  `safe_rename` (via `plan_change` template), `audit_model_and_report`,
  `deploy_to_workspace`, `run_refresh`, `run_dax_regression`,
  `diff_models`, `pre_deploy_check`, `generate_data_dictionary`,
  `apply_theme_and_accessibility_rules`.
- 4 MVP plan templates: `safe_rename`, `audit`, `deploy`, `dax_regression`.
- Engine adapters: `python_report` (built-in), `powerbi-modeling-mcp`,
  `superbi_mcp`, `te` (Sprint 14A).
- Cross-engine rollback engine with HMAC-chained audit log.
- `pbip-validator`, `te bpa`, `dax_linter` (7 anti-patterns),
  `dax_regression`, `model_diff`, `pre_deploy_gate` validation layer.
- WcagAuditor + WCAG re-audit on theme apply.
- Story variance analysis (Sprint 14B).

### Notes

- Beta status. No breaking changes to the public tool API since v1.0.0.