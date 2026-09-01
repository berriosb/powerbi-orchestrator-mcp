# Verifying a fresh installation

> How to confirm that someone cloning this repo and running `pip install`
> gets a working MCP server they can plug into Claude Desktop, VS Code,
> Cursor, or any MCP-compatible client.

The project ships with an **end-to-end verification script** that
spawns the MCP server, speaks real JSON-RPC 2.0 to it, and asserts the
3 MVP tools are visible and callable.

---

## Quick verification (2 minutes)

```bash
# 1. Clone the repo
git clone https://github.com/<owner>/powerbi-orchestrator-mcp.git
cd powerbi-orchestrator-mcp

# 2. Install in a fresh venv
python -m venv .venv
.venv/bin/pip install .

# 3. Run the verification script
.venv/bin/python scripts/verify_mcp_server.py
```

Expected output:

```
Using MCP server binary: /path/to/.venv/bin/powerbi-orchestrator-mcp
Spawning MCP server over stdio...

[1] JSON-RPC initialize handshake
  ✓ Response received — id=1
  ✓ Has protocolVersion — version='2024-11-05'
  ✓ Has serverInfo — name='powerbi-orchestrator-mcp'
  ✓ serverInfo.name is powerbi-orchestrator-mcp

[2] tools/list — verify MVP tools are exposed
  ✓ Response received
  ✓ Got 3 tools — names=['apply_plan', 'connect_target', 'plan_change']
  ✓ All 3 MVP tools present

[3] Each MVP tool has a non-empty input schema
  ✓   connect_target.inputSchema present
  ✓   plan_change.inputSchema present
  ✓   apply_plan.inputSchema present

[4] tools/call — connect_target with valid PBIP path
  ✓ connect_target returned a response
  ✓ structuredContent has session_id
  ✓ structuredContent has engines_available

[5] Clean shutdown
  ✓ Process terminated cleanly

✓ All checks passed — powerbi-orchestrator-mcp works as an MCP server.
```

Exit code: `0` on success, `1` on any failure.

---

## What the script verifies

The script simulates a real MCP client (Claude Desktop, VS Code,
Cursor, etc.) and confirms the server responds correctly to:

| Check | What it proves |
|-------|---------------|
| **JSON-RPC initialize** | Server speaks MCP protocol 2024-11-05, identifies itself, returns server info. |
| **tools/list** | All 3 MVP tools (`connect_target`, `plan_change`, `apply_plan`) are registered. |
| **inputSchema** | Each tool exposes a valid JSON Schema (FastMCP auto-generates from Pydantic models). |
| **tools/call** | `connect_target` actually executes end-to-end: creates a PBIP folder, detects missing engines, returns a session_id and engines_available list. |
| **Clean shutdown** | The server responds to SIGTERM without hanging (uses stdio JSON-RPC, so process cleanup is important). |

If all 5 checks pass, the orchestrator is **fully usable** as an MCP
server — plug it into any MCP client and the LLM will see the 3 tools.

---

## What the script does NOT verify (deliberate)

- **Engine adapters beyond `python_report`** — `powerbi-modeling-mcp`,
  `superbi-mcp`, `te`, `dscmd`, `pbip-validator` are subprocess
  dependencies. If missing, `connect_target` reports them as
  unavailable in `engines_available`, but the orchestrator still
  works (with degraded capabilities).
- **Real subprocess integration** — adapter-level tests use
  `mock_responses`. Real-binary tests require the engine to be
  installed; see [`docs/engines-setup.md`](./engines-setup.md).
- **Plan change + apply** beyond `connect_target` — they require an
  active session and proper plan execution; covered by the unit test
  suite (`tests/unit/test_server.py`, 31 tests).

---

## Configuring a real MCP client

After the script confirms the server works, register it with your client:

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "powerbi-orchestrator-mcp": {
      "command": "/absolute/path/to/.venv/bin/powerbi-orchestrator-mcp",
      "args": ["--start"],
      "env": {"PBI_AUTH_MODE": "interactive"}
    }
  }
}
```

Restart Claude Desktop. The 3 tools should appear in the tools panel.

### VS Code + Copilot

Edit `.vscode/mcp.json` in your workspace:

```json
{
  "servers": {
    "powerbi-orchestrator-mcp": {
      "command": "/absolute/path/to/.venv/bin/powerbi-orchestrator-mcp",
      "args": ["--start"],
      "env": {"PBI_AUTH_MODE": "interactive"}
    }
  }
}
```

### Cursor

Similar — Cursor reads from `~/.cursor/mcp.json`. See [Cursor docs](https://docs.cursor.com/context/model-context-protocol).

---

## Troubleshooting the verification script

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `powerbi-orchestrator-mcp not on PATH and not in active venv` | `pip install` didn't run or ran in a different venv than the one you're testing with | Re-run `pip install .` in the venv whose Python you're invoking. |
| `spawning subprocess timeout` | The server hung on initialize | Run the binary manually (`powerbi-orchestrator-mcp --start` in a terminal) and see if it responds. Check for missing system libraries. |
| `JSON-RPC parse error` | FastMCP version mismatch | Update mcp: `pip install --upgrade mcp`. We require `mcp>=1.0,<2.0`. |
| `connect_target returned a response but no engines_available` | The Pydantic schema serializes differently than expected | Check `src/orchestrator/server.py::ConnectResult` — should be a flat dict. |

---

## Running the script in CI

```yaml
# .github/workflows/verify.yml
- name: Install
  run: python -m venv .venv && .venv/bin/pip install .

- name: Verify MCP server
  run: .venv/bin/python scripts/verify_mcp_server.py
```

If the verification step passes in CI, every PR is guaranteed to ship
a working MCP server.

---

## See also

- [`scripts/verify_mcp_server.py`](../scripts/verify_mcp_server.py) — the script itself
- [`docs/engines-setup.md`](./engines-setup.md) — installing subprocess engines
- [`docs/connect-target.md`](./connect-target.md) — what the first tool returns
- [`SPEC.md §6.5`](../SPEC.md) — MVP done criteria
