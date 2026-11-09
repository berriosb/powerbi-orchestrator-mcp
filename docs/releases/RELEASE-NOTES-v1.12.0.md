# Release Notes — v1.12.0 (2026-10-06)

> **Minor release — External Engine Remediation & MCP Protocol Conformance.**
> Resolves critical integration issues identified during static and dynamic audits of external engine adapters.

**Status:** Beta. Fixed: MCP subprocess handshake, `@microsoft/powerbi-modeling-mcp` official tool routing, and engine selector graceful fallback. Total MCP tools: 28.

---

## What's new

### 1. JSON-RPC MCP Handshake Conformance
- Subprocess engines using `JsonRpcSubprocessEngine` now execute standard MCP handshake negotiation upon startup (`initialize` request followed by `notifications/initialized`).
- Mitigates rejection by strict MCP servers that disallow direct `tools/call` without prior initialization.
- Added graceful cleanup if the handshake process encounters a crash or timeout.

### 2. Official Tool Contract Alignment for `@microsoft/powerbi-modeling-mcp`
- Mapped modeling operations to the official upstream Microsoft MCP server's 21 category-based tools (`table_operations`, `column_operations`, `measure_operations`, `relationship_operations`, `dax_query_operations`, `database_operations`).
- Replaced flat method calls with the required envelope schema: `{"name": "<tool_operations>", "arguments": {"request": {"operation": "<OperationName>", ...}}}`.
- Added bidirectional field normalization (e.g. `dataType` <-> `data_type`, `isHidden` <-> `is_hidden`, `crossFilteringBehavior` <-> `cross_filter`) supporting both live engine responses and test mocks.

### 3. Functional Graceful Degradation in `EngineSelector`
- Fixed the fallback chain loop in `EngineSelector._select` to verify engine availability via `is_available` and executable path probes prior to caching and returning candidates.
- Unregistered or missing engine binaries now correctly raise `EngineNotFoundError` with detailed remediation hints, enabling graceful fallback to secondary candidates (e.g., falling back to `te` when `powerbi-modeling-mcp` is absent).

---

## Pre-release Verification
- **Test Suite**: 925 passed, 11 skipped, 0 failures.
- **Linting & Types**: `ruff check src tests` (0 errors) and `mypy src tests scripts` (0 errors).
- **MCP Verification**: `scripts/verify_mcp_server.py` passed with all 28 tools verified.
