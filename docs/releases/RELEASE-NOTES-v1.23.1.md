# Release Notes — v1.23.1

**Release date:** 2026-11-09

Patch release closing all findings from the v1.23.0 repository audit across plan execution, rollback, persistence, boundary enforcement, cloud proxy handling, and Docker/E2E verification.

## Security & Persistence

- **Interactive orphan execution reconciliation (`apply_plan`).** When `ORPHANED` executions are found from a prior crash, `apply_plan` elicits the caller (`rollback`, `manual`, or `abort`) per `specs/01-orchestrator.md` §2.8 instead of silently proceeding. `plan_executions._connect()` enforces `0700`/`0600` permissions (`ensure_private_dir`, `restrict_file`) and resolves `EXEC_DIR`/`EXEC_DB` dynamically.
- **Database-scoped `.audit_anchor`.** `AuditLog` binds `.audit_anchor` to `self._db_path.parent / ".audit_anchor"` instead of the module-level `AUDIT_DIR`.
- **`PlanStore` ownership, UTC ordering, and local count scoping.** `PlanStore.put()` records ISO UTC timestamps, rejects cross-owner overwrites (`PermissionError`), orders `list_all()` by `created_at DESC, rowid DESC`, and scopes `count()` to `LOCAL_IDENTITY` in stdio mode.
- **CLI workspace boundary enforcement.** `powerbi-orchestrator validate`, `inspect`, and `init` validate target paths via `_resolve_guarded`, `validate_safe_pbip_path`, and `validate_write_path`.

## Bug Fixes

- **`PlanOptions.auto_rollback` persistence and partial `artifacts_changed`.** `Plan` and `PlanStore` persist `options` / `auto_rollback`, `apply_plan` respects `auto_rollback=False`, and `ApplyResult.artifacts_changed` reports committed files from non-rolled-back steps on partial executions.
- **Failed steps excluded from `non_revertible_steps`.** `_rollback_plan` filters out failed steps and `RollbackEngine.execute_plan` marks `failed_step.id` as seen before scanning `executed_steps`.
- **`FabricClient` IPv6 `NO_PROXY` sanitization.** Bracketed IPv6 (`[::1]`) and IPv6 CIDR (`::1/128`, `fe80::/10`) entries in `NO_PROXY` are sanitized before initializing `httpx.AsyncClient`, preventing `httpx.InvalidURL` while preserving `HTTPS_PROXY`.
- **Readiness and health false positives.** `collect_readiness()` reports `read_boundary` as bounded when `PBI_WORKSPACE_ROOT` is set, `_log_startup_warnings` honors CLI `--transport http`, and `powerbi_health` checks `EXEC_DB.exists()` before querying SQLite.
- **Docker stdio E2E race and Dependabot `mcp` v2 loop.** `scripts/verify_docker_image.py` reads JSON-RPC responses line-by-line via `subprocess.Popen` before closing `stdin`, and `.github/dependabot.yml` pins `versions: [">= 2.0.0"]` under `ignore` for `mcp*`.

## Verification

- `1660 passed, 4 skipped`, coverage `89%`
- `ruff format --check`, `ruff check`, and `mypy --strict` clean across 183 files
