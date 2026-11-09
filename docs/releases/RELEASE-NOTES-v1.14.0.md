# Release Notes — v1.14.0 (2026-10-07)

> **Minor release — PBIP Fallback Disk Mutations, Authentication Safety & Runtime Hardening.**
> Resolves 4 critical blockers and 4 secondary hardening findings identified during external runtime audits: synchronizes PBIP semantic models on disk in fallback mode, fixes interactive browser authentication, expands DAX RPC buffer to 16MB, normalizes HMAC audit secrets, prevents subprocess hangs/deadlocks, enforces cross-platform path safety, and guarantees idempotent retries in Fabric REST client.

**Status:** Beta. Fixed: PBIP fallback disk mutation, interactive Azure credential chain, DAX stream buffer limit, HMAC env variable naming, subprocess stderr draining & EOF crash handling, Windows/UNC system path safety, HTTP-date Retry-After parsing, 5xx gateway mapping, and AuthConfig secret masking. Total MCP tools: 28.

---

## What's new

### 1. PBIP Local Fallback Disk Mutations
- `InMemoryModelingAdapter` now inspects, mutates, and atomically saves semantic model files on disk (`definition.pbism`, `model.bim`, TMDL) when connected to a local PBIP project.
- Renaming columns or measures updates both the column definition, measure DAX expressions referencing `Table[Column]`, and relationship bindings on disk in complete lockstep with report visual bindings (`page.json`).
- Prevents desynchronization where report visuals reference renamed fields that were never updated in the semantic model.

### 2. Interactive Azure Browser Authentication
- Configured `exclude_interactive_browser_credential=False` and explicit tenant/client IDs in `FabricCredential` when `auth_mode="interactive"`.
- Overcomes `azure-identity >= 1.19` defaults where browser credential was excluded by default, restoring interactive browser authentication as documented.

### 3. Large JSON-RPC Result Buffer for DAX Queries
- Increased `asyncio.StreamReader` buffer limit from the default 64KB (2^16) to 16MB (16 * 1024 * 1024) in `JsonRpcSubprocessEngine`.
- Prevents `EngineCrashedError` when querying large semantic models or tabular datasets via `execute_dax_query`.

### 4. HMAC Audit Secret Normalization
- Standardized the canonical audit HMAC key environment variable to `PBI_ORCHESTRATOR_AUDIT_SECRET` in all uppercase, aligning code with documentation.
- Retained transparent fallback to legacy casing (`PBI_ORCHestrATOR_AUDIT_SECRET`) for backward compatibility.

### 5. Subprocess Hang and Deadlock Prevention
- Added continuous background draining of subprocess `stderr` to a bounded circular buffer (`deque(maxlen=100)`), preventing OS buffer exhaustion deadlocks on verbose engine output.
- Implemented immediate cancellation of all pending RPC futures with `EngineCrashedError` upon encountering premature `stdout` EOF, eliminating 6-second hang timeouts on crashing processes.

### 6. Cross-Platform System Path Safety
- Extended `validate_safe_pbip_path` to block Windows system root directories (`Windows`, `System32`, `Program Files`, `Program Files (x86)`, `ProgramData`) and UNC paths (`\\`).
- Guarantees strict path safety across Linux, macOS, and Windows operating systems.

### 7. Fabric REST Client Idempotency & HTTP-Date Retries
- Restricted automatic retries on 502/503/504 status codes exclusively to idempotent HTTP methods (`GET`, `PUT`, `DELETE`), preventing accidental duplicate resource creation on transient gateway failures.
- Added support for RFC 7231 HTTP-date formatted `Retry-After` headers in addition to integer seconds.
- Mapped 5xx server and gateway errors directly to `FabricAPIError` so high-level exception handling safely catches all cloud failures.

### 8. Masked Credential Representation
- Set `repr=False` on `client_secret` in `AuthConfig` dataclass to prevent sensitive secrets from leaking into application logs, console traces, or error messages.

---

## Verification
- **Test Suite**: 943 passed, 11 skipped across unit, integration, and E2E suites.
- **Type Safety**: `mypy src tests scripts` passed with zero errors across 127 source files.
- **Linter**: `ruff check src tests` passed with zero violations.
- **MCP Protocol**: `verify_mcp_server.py` passed with all 28 tools verified over stdio.
