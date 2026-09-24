# Examples

Reproducible workflows showing how to drive `powerbi-orchestrator-mcp`
from an MCP client (Claude Desktop, VS Code + Copilot, Cursor, Cline, etc.).

Each example is a self-contained README you can walk through without
prior context. They're ordered from simplest to most advanced:

| # | Workflow | Time | Demonstrates |
|---|---|---|---|
| [01](./01-safe-rename/README.md) | Safe Rename | 5 min | `connect_target` → `plan_change` → `apply_plan` → rollback |
| [02](./02-deploy-pbip/README.md) | Deploy to Fabric | 10 min | pre-deploy gate → publish → refresh schedule → initial refresh |
| [03](./03-audit-then-fix/README.md) | Audit-then-fix loop | 15 min | baseline audit → WCAG fix → lint-gated writes → re-audit |

## Target audience

- **LLM agents** that need to learn the tool surface.
- **Power BI developers** evaluating the orchestrator.
- **Contributors** writing new tools who want a template.

## What you need

- An MCP client connected to the orchestrator
  ([install instructions](../README.md)).
- A PBIP folder on disk (use `tests/fixtures/sample.pbip` or your own).

## How to use these

1. **Open the README** for the workflow you want.
2. **Copy each block** (the `tool_name(...)` snippet) into your MCP client.
3. **Inspect the response** — most blocks include the expected JSON shape.

You don't need to run them sequentially; each example stands alone.

## What if my MCP client doesn't show tool names?

The orchestrator exposes 27 tools grouped by category. See the main
[README § Quick links](../README.md) for the full list. If your client
only shows a subset, check:

- **Claude Desktop**: the orchestrator appears under
  `powerbi-orchestrator-mcp` in the tools menu.
- **VS Code + Copilot**: `.vscode/mcp.json` must include the server.
- **Cursor**: `~/.cursor/mcp.json` (or per-project `.cursor/mcp.json`).

See [`docs/troubleshooting.md`](../docs/troubleshooting.md#mcp-client-configuration).

## Adding a new example

1. Create `examples/0N-short-name/`.
2. Add a `README.md` following the same structure (Prerequisites,
   Walkthrough, What can go wrong, Files).
3. Reference it in this README's table.

Examples must be **self-contained** — no references to private
workspaces or customer-specific PII.