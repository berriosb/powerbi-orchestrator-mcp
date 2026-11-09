# Release Notes — v1.13.0 (2026-10-07)

> **Minor release — Step Executor Engine Implementation & Runtime Hardening.**
> Resolves critical execution defects in `apply_plan`, closes multi-client HTTP session leaks, hardens path security, and connects automatic data dictionary inspection.

**Status:** Beta. Fixed: production step executors, `dry_run` safety verification, E2E fixtures, HTTP session isolation, path traversal guards, and data dictionary auto-inspection. Total MCP tools: 28.

---

## What's new

### 1. Production Step Executors for `apply_plan`
- Implemented real production `StepExecutor` adapters for all orchestrator engines: `ValidationStepExecutor`, `ModelingStepExecutor`, `ReportStepExecutor`, and `CloudStepExecutor`.
- `apply_plan` now executes real multi-step plans end-to-end (`safe_rename`, `audit`, `deploy`, `dax_regression`) without failing on missing executors.
- Standalone execution works out-of-the-box on local PBIP files with fallback to `InMemoryModelingAdapter` when external binaries are not present.

### 2. Elimination of `dry_run` False Positives
- `_resolve_executor` now strictly verifies that the engine requested by a plan step is registered before returning `DryRunExecutor`.
- Dry-runs on unregistered engines cleanly fail with `MissingEngineExecutor` and prescriptive remediation advice instead of falsely reporting success.

### 3. Multi-client HTTP Session Isolation
- Eliminated cross-tenant session leaks caused by falling back to mutable module-level globals in multi-client HTTP mode.
- Context isolation is enforced via `contextvars.ContextVar` under HTTP transport.

### 4. Path Traversal & System Directory Guards
- Added `validate_safe_pbip_path` rejecting attempts to pass system directories (such as `/etc`, `/proc`, `/sys`) across all tools that take filesystem paths.

### 5. Automatic Model Inspection in `generate_data_dictionary`
- `generate_data_dictionary` now automatically reads semantic model definitions (`definition.pbism`, `model.bim`) directly from local PBIP folders when no custom inspector is injected, generating rich Markdown data dictionaries and Mermaid ER diagrams out-of-the-box.

### 6. E2E Fixture Resolution
- Corrected `tests/e2e/conftest.py` to target the actual `sample.pbip` fixture with automatic generation fallback if the directory is missing.

### 7. Plan Target Tracking
- `Plan` now records its connected target reference, ensuring generated YAML plans serialize the actual target path instead of an empty string.

---

## Pre-release Verification
- **Test Suite**: Fully verified with unit, integration, and E2E suites.
- **Linting & Types**: `ruff check src tests` (0 errors) and `mypy src tests scripts` (0 errors).
- **MCP Verification**: `scripts/verify_mcp_server.py` passed with all 28 tools verified.
