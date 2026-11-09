# Release v1.14.1 — Strict Modeling Operation Reporting and Table Rename Support

> Release notes for `powerbi-orchestrator-mcp` version `1.14.1`.
> Resolves false-positive success reporting when objects do not exist, adds complete table rename support in local PBIP fallback mode, guarantees explicit I/O failure reporting, unites Entra ID token auth claims, and prevents SQLite connection descriptor leaks.
>
> Status: Beta. Total MCP tools: 28.

---

## What's new

### 1. Strict Modeling Operation Reporting (No False Success)

- `InMemoryModelingAdapter.update_column`, `create_measure`, `update_measure`, and `delete_measure` now strictly return `OperationResult(success=False, error_message=...)` whenever an operation target cannot be found in the semantic model files on disk.
- Prevents false-positive success reports (`success=True`) when renaming or modifying nonexistent columns, measures, or tables.
- Guarantees that `apply_plan` cleanly fails and aborts before mutating report visuals, preventing desynchronization and corruption of local PBIP projects.

### 2. Full Table Rename Support in Local PBIP Fallback

- `InMemoryModelingAdapter` now supports renaming tables across semantic model files (`definition.pbism`, `model.bim`, TMDL).
- Automatically updates all dependent measure DAX expressions referencing `OldTable[Column]` to `NewTable[Column]`.
- Updates relationships referencing the table in `fromTable` and `toTable`.
- Seamlessly propagates through `safe_rename` to keep report visual bindings (`page.json`) and dataset models in lockstep.

### 3. I/O Error Transparency

- Replaced broad exception swallowing with precise exception handling.
- Any OS-level write failure (disk full, read-only filesystem, permission denied) immediately returns `OperationResult(success=False, error_message="I/O error updating <file>: <reason>")` rather than being ignored.

### 4. Entra ID Auth Claim Unification

- In `validate_entra_token`, unified `scp` (delegated permissions) and `roles` (application roles).
- Tokens containing both scopes and app roles now have both evaluated, preventing unexpected 403 Forbidden errors for service principals and app-role clients.

### 5. SQLite Connection Lifecycle Cleanup

- Wrapped sqlite queries in `count_entries` (`audit.py`) and execution store health check (`powerbi_health.py`) in `try ... finally: conn.close()`.
- Prevents open database lock contention and file descriptor exhaustion across long-running processes.

---

## Verification

- Test Suite: 946 passed, 11 skipped across unit, integration, and E2E suites.
- Type Safety: `mypy src tests scripts` passed with zero errors across 127 source files.
- Linter: `ruff check src tests` passed with zero violations.
- Tested nonexistent column renames: verified `apply_plan` returns failure, reports clear error, and preserves disk intact.
- Tested table renames: verified full mutation of model, measures, and report visual bindings on disk.
