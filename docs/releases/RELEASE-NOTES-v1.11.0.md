# Release Notes — v1.11.0 (2026-10-01)

> **Minor release — Enterprise Hardening & Natural Language MCP Optimization.**
> Backward-compatible: All existing tool signatures remain compatible while gaining support for native dict/list objects and enriched documentation. Total MCP tools increased to 28 with the addition of `execute_dax_query`.

**Status:** Beta. New: 1 tool (`execute_dax_query`), enterprise TMDL multipart deployment, concurrency isolation via `contextvars`, and official npm `powerbi-modeling-mcp` integration. Removed: none.

---

## What's new

### 1. Enterprise Readiness & Production Hardening
- **TMDL Multipart Publication (`deploy_to_workspace`)**:
  Properly parses and encodes `.tmdl` files into Fabric's `InlineBase64` multipart payload for semantic model deployment, and extracts the target dataset UUID for scheduled refresh automation.
- **Official NPM Integration (`powerbi-modeling-mcp@1.0.0`)**:
  Pinned to the official package with `--start` and `--accept-eula` flags, supporting automatic EULA acceptance via `PBI_MODELING_MCP_ACCEPT_EULA="true"`.
- **Session & Concurrency Isolation (`contextvars`)**:
  Isolated active session IDs and authenticated user claims across concurrent requests to prevent multi-tenant data leakage.
- **FSL License Guardrails**:
  Added `PBI_COMMERCIAL_MODE` and `PBI_DISABLE_FSL_ENGINES` flags to ensure enterprise deployments can strictly enforce commercial compliance.

### 2. Natural Language Agent Optimizations
- **Global `MCP_INSTRUCTIONS`**:
  Integrated comprehensive architectural instructions into `FastMCP` so LLM agents (Claude Desktop, Cursor, Copilot) receive an immediate workflow map upon handshake.
- **Resilient Argument Parsing (`_parse_json_arg`)**:
  Tools accepting JSON payloads now seamlessly accept both serialized JSON strings and native Python dicts/lists without runtime `JSONDecodeError`.
- **New Tool: `execute_dax_query`**:
  Enables live evaluation of DAX queries against published Fabric / Power BI semantic models with optional RLS testing via `impersonated_user_name`.
- **Intent-Driven Tool Documentation**:
  All 28 MCP tools have enriched docstrings specifying exact user intents ("Use this tool when the user asks to..."), parameter types, and output structures.

---

## Pre-release Verification
- **Test Suite**: 920 passed, 11 skipped, 0 failures.
- **Linting & Types**: `ruff check` and `mypy --strict` 100% clean.
- **MCP Smoke Test**: `verify_mcp_server.py` passed with all 28 tools verified over JSON-RPC stdio.
