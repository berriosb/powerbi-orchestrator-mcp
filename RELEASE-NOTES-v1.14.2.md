# Release v1.14.2 — Subprocess Resilience, BPA Real Integration, and Production Hardening

> Release notes for `powerbi-orchestrator-mcp` version `1.14.2`.
> Hardens engine subprocess communication against startup banners/warnings, connects real BPA analysis in `audit_model_and_report`, eliminates false pass scores on non-PBIP folders, clamps scores to [0.0, 100.0], introduces workspace allowlisting, connects `blocking_severities` in pre-deploy checks, and provides hermetic test isolation via `PBI_ORCHESTRATOR_HOME`.
>
> Status: Beta. Total MCP tools: 28.

---

## What's new

### 1. Subprocess Engine Stdout Resilience & Dict Pop Safety
- In `JsonRpcSubprocessEngine._read_stdout_loop`, non-JSON stdout lines (such as Node.js warnings or package startup banners) are now captured in an internal buffer instead of throwing an unhandled parse exception and terminating the reader task.
- Replaced `del self._pending[request_id]` with thread-safe `self._pending.pop(request_id, None)` across RPC handling to prevent race conditions.
- Enforced timeout guard during engine initialization with `asyncio.wait_for`.

### 2. Real BPA Integration & Validated PBIP Auditing
- Connected `BpaRunner` to execute Tabular Editor Best Practice Analyzer rules on semantic models.
- Gracefully handles missing `te2` CLI by logging informative remediation warnings and omitting BPA from the aggregate score without reporting deceptive 100.0 scores.
- Strict directory validation ensures paths must contain genuine PBIP artifacts (`.pbip`, `.Report`, `.SemanticModel`, `.Dataset`); nonexistent or arbitrary directories now score 0.0 with clear explanatory diagnostics.
- `AuditResult.overall_score` is strictly bounded to `[0.0, 100.0]` and weight re-normalization dynamically adjusts across active checks only.

### 3. Path Safety Workspace Allowlist
- Added support for workspace root isolation via `PBI_WORKSPACE_ROOT` or `workspace_root` parameter, rejecting any path traversal escaping the workspace root.
- Blocked sensitive user configuration folders (`.ssh`, `.aws`, `.gnupg`, etc.).

### 4. Pre-Deploy Gate Severity Overrides
- Wired `blocking_severities` in `pre_deploy_check` to `PreDeployGate.evaluate()`, allowing callers to configure custom blocking policies (e.g. `["error"]` vs `["error", "warning"]`).

### 5. Hermetic Test Isolation (`PBI_ORCHESTRATOR_HOME`)
- Centralized orchestrator home directory resolution in `paths.py`.
- Test suite executes in isolated temporary directories without polluting the user's `$HOME` or existing databases.

### 6. FastMCP Server Version Parity & Async Screenshot Offload
- Explicitly configured `mcp._mcp_server.version = __version__` so protocol handshakes accurately report version `1.14.2`.
- Offloaded CPU-bound report image screenshot rendering via `asyncio.to_thread` to ensure event loop responsiveness.

---

## Verification
- Test Suite: 962 passed, 11 skipped across all suites.
- Type Safety: `mypy --strict src tests` passed with zero errors across 128 source files.
- Linter: `ruff check src tests` clean with zero issues.
- Zero comments in production and test Python code.
