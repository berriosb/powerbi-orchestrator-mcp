# Changelog

All notable changes to **powerbi-orchestrator-mcp** are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Note:** Per-release notes (with full PR lists, decisions, and rationale)
> live in the [`RELEASE-NOTES-vX.Y.Z.md`](./RELEASE-NOTES-vX.Y.Z.md) files
> at the repo root. This file is a condensed digest for quick lookup.

## [1.16.0] — 2026-10-08

### Added
- **`confirm_each_step` is now real per-step confirmation.** Previously a
  placeholder (`noqa: ARG001 — elicitation hook for v2`) that silently did
  nothing. It now asks the user to approve each step via MCP elicitation
  before it runs. It **fails closed**: with no interactive client, a declined
  prompt, or an answer that is not an explicit yes (`y`/`yes`/`si`/`ok`),
  the step is not run and the remaining steps are reported as skipped with
  `result="stopped_by_user"`. A decline is not treated as a failure, so it
  does not trigger the rollback path for steps that never executed.
  `elicit()` gained `bypass_rate_limit` for this loop, since the 5 s cooldown
  would otherwise stall an interactive confirmation session.

### Fixed
- **Silent API failures were reported as empty state.** Four helpers swallowed
  exceptions and returned an empty result, making a broken Fabric API
  indistinguishable from an empty workspace:
  `_enumerate_workspace_items`, `_snapshot_workspace` and `_enumerate_items` now
  surface the failure through `warnings` instead of pretending the target had
  no items (which, for `sync_git_to_workspace`, meant a failed conflict check
  looked like a clean deploy target).
- **`set_sensitivity_labels` swallowed audit-logger failures with `pass`**,
  meaning a *blocked* operation could leave no trace. Now reported.
- **Dead `try: pass` block in `sync_git_to_workspace`** left the
  `pre_deploy_check` import unprotected — the `except` branch was unreachable
  and the import now sits inside the `try`.

### Changed
- Test coverage of the model/report write paths raised: `step_executor.py`
  56 % → 88 %, `te_adapter.py` 51 % → 71 %, project total 86 % → 88 %.
  New `tests/unit/test_write_paths.py` covers measure create/update/delete,
  snapshot/restore round-trip, TMDL and PBISM persistence, and the report
  executor dispatch.

---

## [1.15.0] — 2026-10-08

### Security
- **Safe-by-default write tools**: `add_measure_with_validation`, `apply_plan` and `promote_in_pipeline` now default to `dry_run=True`. Executing against real files or real Fabric Deployment Pipeline stages (including prod) requires an explicit `dry_run=False`, so an LLM caller can no longer mutate state by omitting the flag. This is a behavior change: callers that relied on the previous implicit execution must now pass `dry_run=False`.

### Fixed
- **Silent no-op in `add_measure_with_validation`**: when no `measure_writer` is wired the tool returned `success=True` while writing nothing. Because an MCP client cannot inject a callable, this was reachable in every real MCP call — the tool reported a measure as created when no file was modified. It now returns `success=False` with an explicit "not persisted" message.
- **`runtime_check` false positive**: reported `ran=True` alongside an "unsupported" error, which reads as "validation ran and failed" to any caller. It now reports `ran=False` plus a `supported=False` marker.

### Changed
- **`ruff format` enforcement**: the entire tree is now formatted and CI runs `ruff format --check` alongside `ruff check`, closing a gap where 95 of 129 files drifted from the configured style with no CI signal.
- **Dependency auditing**: `pip-audit` added to the `verify` workflow and to the `dev` extra.

---

## [1.14.2] — 2026-10-07

### Fixed
- **Subprocess Engine Stdout Resilience**: Filter and buffer non-JSON lines (e.g. Node/NPM startup banners, warnings) to prevent premature reader loop crashes and handshake timeouts.
- **Subprocess RPC Dict Safety**: Replaced unsafe dict key deletion with `pop(request_id, None)` to eliminate potential race conditions during concurrent response processing.
- **BPA Runner Real Integration**: Replaced static mock BPA scoring with real `BpaRunner` invocation in `audit_model_and_report`, gracefully falling back with actionable warnings when Tabular Editor 2 is unavailable.
- **PBIP Path & Structure Verification**: Enforced strict validation of PBIP folder artifacts, eliminating false pass scores (previously 75.0) on arbitrary non-PBIP directories.
- **Audit Score Bounds & Dynamic Weighting**: Clamped `overall_score` strictly to `[0.0, 100.0]` and re-normalized score weighting dynamically across enabled checks.
- **Path Safety Workspace Allowlist**: Added `PBI_WORKSPACE_ROOT` directory allowlisting and protected sensitive user configuration directories.
- **Pre-Deploy Gate Filtering**: Connected `blocking_severities` parameter in `pre_deploy_check` to `PreDeployGate.evaluate()`.
- **Hermetic Test Isolation**: Centralized orchestrator home resolution in `paths.py` via `PBI_ORCHESTRATOR_HOME`, sandboxing all database writes during testing.
- **FastMCP Protocol Parity**: Explicitly updated underlying `mcp._mcp_server.version` to match server release version in MCP initialize handshakes.
- **Asynchronous PNG Rendering**: Offloaded CPU-bound report screenshot image rendering to `asyncio.to_thread` to keep the main event loop non-blocking.

---

## [1.14.1] — 2026-10-07

### Fixed
- **Strict Modeling Operation Reporting**: `InMemoryModelingAdapter` now returns `success=False` with clear diagnostics whenever an operation does not match any table, column, or measure, eliminating false-positive success reports on nonexistent objects.
- **Table Rename Support in Local PBIP Fallback**: Added full support for renaming tables in `InMemoryModelingAdapter` across semantic models (`definition.pbism`, `model.bim`, TMDL), relationships (`fromTable`, `toTable`), measure expressions (`Table[Column]`), and report visuals in `safe_rename`.
- **I/O Error Transparency**: Replaced broad exception swallowing with precise exception handling and explicit `OperationResult(success=False, error_message=...)` reporting on write failures (disk full, permissions).
- **Transport Auth Claim Union**: Unified `scp` (delegated) and `roles` (app-role) claims so tokens containing both do not ignore assigned application roles.
- **SQLite Connection Resource Cleanup**: Wrapped CLI verify and health check sqlite queries in `try ... finally: conn.close()` to prevent file descriptor leaks and database lock retention.

---

## [1.14.0] — 2026-10-07

### Fixed
- **PBIP Local Fallback Disk Mutations**: `InMemoryModelingAdapter` now inspects, updates, and saves semantic models on disk (`definition.pbism`, `model.bim`, TMDL) when `conn.target_ref` points to a local PBIP project. Column renames, measure expressions, and relationships are updated in lockstep with report visual bindings without requiring external modeling binaries.
- **Interactive Azure Authentication**: Enabled `InteractiveBrowserCredential` explicitly when `auth_mode="interactive"` under `azure-identity >= 1.19`, resolving authentication failure with browser flow.
- **Large DAX Response Support**: Increased `asyncio.StreamReader` buffer limit from 64KB to 16MB in `JsonRpcSubprocessEngine`, preventing crashes on queries returning large result sets.
- **HMAC Audit Key Normalization**: Canonicalized environment variable name to `PBI_ORCHESTRATOR_AUDIT_SECRET` while preserving backward compatibility with legacy casing.
- **Subprocess Hang and Deadlock Prevention**: Continuously drained subprocess `stderr` to a bounded ring buffer and implemented immediate pending RPC cancellation on premature subprocess termination (EOF).
- **Cross-Platform System Path Safety**: Expanded `validate_safe_pbip_path` to block Windows system directories (`Windows`, `System32`, `Program Files`) and UNC paths (`\\`).
- **Fabric Client Idempotency & Retry Hardening**: Restricted 502/503/504 retries to idempotent HTTP methods (`GET`, `PUT`, `DELETE`), parsed HTTP-date RFC 7231 `Retry-After` headers safely, and mapped 5xx gateway/proxy errors directly to `FabricAPIError`.
- **Credential Masking in Logs**: Masked `client_secret` in `AuthConfig.__repr__` to prevent accidental credential leakage in debug logs.

---

## [1.13.0] — 2026-10-07

### Fixed
- **Production Step Executors**: Implemented `ValidationStepExecutor`, `ModelingStepExecutor`, `ReportStepExecutor`, and `CloudStepExecutor` in `step_executor.py`, enabling complete, autonomous plan execution (`apply_plan`) across all standard intents without missing engine failures.
- **`dry_run` Safety Verification**: Enforced that `dry_run=True` validates that the required engine is registered before reporting success, eliminating misleading false positives on unregistered engines.
- **HTTP Multi-client Session Isolation**: Eliminated cross-tenant session leaks by preventing fallback to module-level globals when running under HTTP transport.
- **Path Traversal Guards**: Added `validate_safe_pbip_path` to reject system paths (`/etc`, `/proc`, `/sys`) across all tools accepting filesystem paths.
- **Data Dictionary Auto-inspection**: Connected `generate_data_dictionary` to automatically extract tables, columns, measures, and relationships from PBIP definitions (`definition.pbism`, `model.bim`) when no custom inspector is provided.
- **E2E Fixture Resolution**: Pointed `tests/e2e/conftest.py` to `sample.pbip` with automatic fixture generation if missing.
- **Plan Target Tracking**: Added `target` attribute to `Plan` and preserved target path in generated YAML.

---

## [1.12.0] — 2026-10-06

### Fixed
- **Subprocess MCP Handshake**: Implemented JSON-RPC MCP handshake (`initialize` request followed by `notifications/initialized`) in `JsonRpcSubprocessEngine` before sending `tools/call`.
- **`@microsoft/powerbi-modeling-mcp` Real Tool Mapping**: Aligned modeling adapter tool dispatch with the official Microsoft MCP server's 21 category tools (`table_operations`, `column_operations`, `measure_operations`, `relationship_operations`, `dax_query_operations`, `database_operations`), formatting calls with the required `{"request": {"operation": ...}}` payload structure and response normalization.
- **Engine Selector Fallback Chain**: Corrected engine candidate evaluation in `EngineSelector` by adding synchronous availability verification (`is_available`), allowing graceful degradation when preferred engines or binaries are unavailable.

---

## [1.11.0] — 2026-10-01

### Added
- **New Tool `execute_dax_query`**: Execute DAX queries against published Fabric / Power BI semantic models with optional RLS user impersonation.
- **Global `MCP_INSTRUCTIONS`**: System instructions embedded into FastMCP to guide LLM agents on optimal tool selection and workflows.
- **Resilient Argument Parsing**: `_parse_json_arg` support for both string and native object arguments across all JSON-accepting tools.
- **TMDL Multipart Publication**: Base64 multipart encoding in `deploy_to_workspace` for seamless semantic model deployment.
- **Session Isolation**: `contextvars` isolation for multi-tenant requests in HTTP and stdio transports.
- **Commercial FSL Guards**: `PBI_COMMERCIAL_MODE` and `PBI_DISABLE_FSL_ENGINES` flags.

### Changed
- **Tool Docstrings**: All 28 tools updated with natural-language intent guides ("Use this tool when the user asks to...").
- **MCP Tool Count**: Expanded from 27 to 28 tools.

---

## [1.10.0] — 2026-09-24

### Added

- **HTTP transport (opt-in).** New `--transport http` flag and env var
  `PBI_TRANSPORT=http` enable Streamable HTTP (MCP spec 2025-06+).
  Authentication via Entra ID JWT bearer tokens, validated against the
  tenant JWKS. New module `orchestrator/transport.py` exposes
  `validate_entra_token`, `parse_transport_args`, and
  `auth_middleware_factory`. Backward-compatible: stdio default
  unchanged. See [`RELEASE-NOTES-v1.10.0.md`](./RELEASE-NOTES-v1.10.0.md)
  and `specs/architecture/07-http-transport.md`.
- **Dependency:** `pyjwt[crypto]>=2.8.0` (~50KB) required by the HTTP
  transport auth path.
- **PyPI `project_urls`:** Homepage, Repository, Issues, Changelog,
  Releases — visible in the project page sidebar.
- **Release infrastructure (CI, not user-facing):**
  - `.github/workflows/publish.yml` — automated publish on tag push or
    manual dispatch; TestPyPI gate; `pypi-production` environment with
    required reviewers. Secrets (`PYPI_API_TOKEN`,
    `TEST_PYPI_API_TOKEN`) not yet configured.
  - `.github/workflows/e2e-nightly.yml` — nightly + on-PR E2E tests
    with real engine binaries pinned by SHA256. Placeholder hashes
    until first real run; self-skips while placeholders are in place.
- **E2E test scaffold:** `tests/e2e/` with 5 scenarios across 2 test
  files. Tests skip gracefully when engines are absent.

### Changed

- **Docs:** 4 new cross-cutting specs land as part of the v0.3 audit —
  `specs/release/supersede-policy.md`, `specs/ci/publish-workflow.md`,
  `specs/qa/e2e-testing-strategy.md`, `specs/architecture/07-http-transport.md`.
  See `specs/README.md` "Cambios v0.3 (audit 2026-09-24)".
- **README:** tool count drift fix "26 → 27" across 5 places
  (including `examples/README.md`).
- **Test suite:** 903 passed (+21 from `test_http_transport.py`),
  11 skipped (e2e binaries absent). Coverage 90.85%.

### Notes

- No breaking changes. `pip install --upgrade` is a no-op at the
  binary level for stdio users.
- The HTTP transport middleware isn't yet wired into FastMCP's
  request lifecycle — the validation function is exported and tested,
  but unauthenticated HTTP requests are not yet rejected by the
  server itself. Custom deployment adapters can call
  `auth_middleware_factory` directly. Wiring lands in v1.11.
- This release was made via the v1.9.x manual `twine upload` pattern.
  Future releases should use the new `publish.yml` once secrets are
  configured.

## [1.9.1] — 2026-09-24

### Changed

- **README on PyPI:** `Estado actual (v1.9.0)` section now reflects ✅
  publication on PyPI instead of marking it as a pending item. Link
  to release notes corrected from `v1.8.0` → `v1.9.0`. First-time
  users on https://pypi.org/project/powerbi-orchestrator-mcp/ now
  see a description that matches reality.
- **docs/MVP-STATUS.md:** PyPI publication moved out of the optional
  backlog into a new "Hecho en v1.9.0" section.

### Notes

- Doc-only patch release. No code changes. No new tools. No dependency
  bumps. Re-install is a no-op at the binary level (same wheel content
  modulo the README). Safe to upgrade in place.
- See [`RELEASE-NOTES-v1.9.1.md`](./RELEASE-NOTES-v1.9.1.md).

---

## [1.9.0] — 2026-09-14

### Added

- **Sprint 16 — product-readiness pass:**
  - **Phase 1 (distribution + onboarding)**: `examples/` directory with 3 reproducible workflows (`01-safe-rename`, `02-deploy-pbip`, `03-audit-then-fix`); `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md` at repo root; `docs/troubleshooting.md` with common errors and remediation.
  - **Phase 2 (reliability + observability)**: `powerbi_health` MCP tool (engine status, plan store stats, audit log size); SQLite-backed `PlanExecutionStore` (replaces in-memory dict, survives restarts); structured logging via `structlog` for `apply_plan` + `_request` paths.

See [`RELEASE-NOTES-v1.9.0.md`](./RELEASE-NOTES-v1.9.0.md) for full details, decisions, and rationale.

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