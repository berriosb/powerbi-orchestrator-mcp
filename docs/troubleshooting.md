# Troubleshooting

> Common errors you'll hit when running `powerbi-orchestrator-mcp` and
> how to fix them. If your problem isn't here, open an issue at
> [github.com/berriosb/powerbi-orchestrator-mcp/issues](https://github.com/berriosb/powerbi-orchestrator-mcp/issues).

---

## Authentication (Azure AD / Fabric / PBI Service)

### `AADSTS700016: Application not found in directory`

**Cause:** The SPN `client_id` you passed doesn't exist in the tenant.

**Fix:**
1. Go to **Azure Portal → App registrations** and confirm the SPN exists.
2. Verify the `tenant_id` matches the SPN's home tenant (not the user's
   home tenant — they can differ).
3. For multi-tenant apps, ensure admin consent has been granted.

### `AADSTS650057: Invalid resource`

**Cause:** The SPN hasn't been granted access to the Power BI Service API.

**Fix:**
1. **Azure Portal → App registrations → your SPN → API permissions**.
2. Add **Power BI Service** with the scopes your operation needs:
   - Read-only: `Dataset.Read.All`
   - Write: `Dataset.ReadWrite.All`
   - Admin (labels): `InformationProtectionPolicy.Apply.All`
3. **Grant admin consent** for the tenant.

### `Token expired` mid-session

**Cause:** `DefaultAzureCredential` token cache hit an expired refresh
token (rare; usually means the AAD app hasn't been used in 90+ days).

**Fix:**
1. Re-authenticate via `az login --service-principal -u <client_id> -p <secret> --tenant <tenant_id>`.
2. Or use interactive auth (`PBI_AUTH_MODE=interactive`) — opens a
   browser, no SPN needed.

### `fabric_client: 401 Unauthorized` after several successful calls

**Cause:** The SPN's secret or certificate is rotating / expired.

**Fix:**
1. Check the SPN's **Certificates & secrets** in Azure Portal.
2. Rotate the secret; pass the new value via env var or arg.

---

## Engines (subprocess MCPs)

### `engine_not_found: powerbi-modeling-mcp binary not found`

**Cause:** The `@microsoft/powerbi-modeling-mcp` npm package isn't on
PATH (it's expected to be discoverable via `npx`).

**Fix:**
```bash
# Verify Node.js + npm.
node --version    # >= 18
npm --version

# Test the package manually.
npx -y @microsoft/powerbi-modeling-mcp --help
```

If `npx` works manually, the orchestrator should find it on next
`connect_target` call.

### `engine_not_found: te binary not found`

**Cause:** Tabular Editor CLI is not installed.

**Fix per OS:**
- **Linux**: `dotnet tool install --global TabularEditor`
- **macOS / Windows**: download from
  [github.com/TabularEditor/TabularEditor/releases](https://github.com/TabularEditor/TabularEditor/releases).
- Verify: `te2 --version` returns a version string.

For v1.7+ the TE adapter runs in `mode='skeleton'` by default (no
binary required); the call returns a synthetic success. To use TE for
real TOM edits, set `PBI_TE_MODE=subprocess` in the env.

### `engine_timeout: te bpa timed out after 60s`

**Cause:** BPA validation on a large model takes longer than the default
timeout.

**Fix:**
```bash
export PBI_ENGINE_TIMEOUT_TE_S=300    # 5 minutes
# or for a single call, pass timeout_s=300
```

### `engine_output_parse_error: stdout was not JSON`

**Cause:** The subprocess emitted non-JSON output (usually a startup
error or a banner).

**Fix:**
1. Run the engine manually and check the output: `npx -y powerbi-modeling-mcp`.
2. If it's a banner, update `engines/versions.py` to a version that
   starts cleanly.
3. If the error persists, file an issue with the stdout capture.

---

## MCP client configuration

### Claude Desktop doesn't see the orchestrator

**Cause:** `claude_desktop_config.json` is wrong or the console script
isn't on PATH.

**Fix:**
```bash
# 1. Verify the console script is installed.
which powerbi-orchestrator-mcp
# Should return /path/to/.venv/bin/powerbi-orchestrator-mcp

# 2. Verify it runs.
powerbi-orchestrator-mcp --help
# (currently exits silently; the server runs over stdio)

# 3. Check the config:
cat ~/Library/Application\ Support/Claude/claude_desktop_config.json  # macOS
cat ~/.config/Claude/claude_desktop_config.json                       # Linux
```

Valid config:
```json
{
  "mcpServers": {
    "powerbi-orchestrator-mcp": {
      "command": "powerbi-orchestrator-mcp",
      "args": ["--start"],
      "env": {"PBI_AUTH_MODE": "interactive"}
    }
}
```

### VS Code + Copilot: "command not found" in MCP picker

**Cause:** VS Code uses its own shell PATH which may differ from the
terminal. PATH.

**Fix:**
1. Use the **absolute path** to `powerbi-orchestrator-mcp` in
   `.vscode/mcp.json`:
   ```json
   {
     "servers": {
       "powerbi-orchestrator-mcp": {
         "command": "/Users/you/.venv/bin/powerbi-orchestrator-mcp",
         "args": ["--start"]
       }
     }
   }
   ```
2. Or add your venv to VS Code's PATH
   (`.vscode/settings.json` → `python.defaultInterpreterPath`).

---

## Tools

### `connect_target: target_ref does not exist: <path>`

**Cause:** The PBIP path doesn't exist on disk, OR the path is wrong.

**Fix:**
1. Confirm the path is a directory (PBIP folder, not a single file).
2. The folder must contain a `<name>.Report/` subfolder (Power BI
   Project structure).
3. For `pbi_desktop` target_type, leave `target_ref=""` (loopback is
   auto-discovered).

### `apply_plan: plan not found`

**Cause:** The `plan_id` returned by `plan_change` is being used in a
different server process (the in-memory plan store is per-process).

**Fix:**
- In v1.x the orchestrator runs as a single stdio process per MCP
  client; if the user opens two clients, they each have their own
  plan store.
- For multi-client setups, upgrade to a server that uses SQLite-backed
  storage (planned v1.9.0+).

### `deploy_to_workspace: publish_failed: 403 forbidden`

**Cause:** The SPN doesn't have write access to the workspace.

**Fix:**
1. Workspace admin: **Power BI Service → workspace → Access → Add
   `<SPN name>` with Contributor / Member role**.
2. For service-principal sign-in to work, the tenant admin must have
   enabled **"Allow service principals to use Power BI APIs"** in
   the Power BI admin portal.

### `run_refresh: 401 Unauthorized`

**Cause:** SPN doesn't have `Dataset.ReadWrite.All`.

**Fix:** see "AADSTS650057" above + add the scope.

### `audit_model_and_report: BPA score 0` on a known-good model

**Cause:** Without `te` installed, `bpa_runner` returns an empty
findings list with score 100.0 (mock path). If you're getting 0, you
have a stale mock or a different rule.

**Fix:**
1. Check the rule: BPA score = 100 - (sum of weighted findings).
2. If 5 errors → score 0; if 5 warnings → score still 100.
3. Inspect `result.findings` to see which rules fired.

### `screenshot_report_pages: rendering_warnings: ['Desktop Bridge not available']`

**Cause:** The orchestrator can't drive Power BI Desktop (Linux/macOS
without superbi-mcp, or Windows without the binary).

**Fix:**
1. Expected on non-Windows without superbi-mcp. The tool still emits
   **SVG wireframes + JSON manifests** (deterministic for regression
   tests) — use those.
2. For pixel-perfect PNG screenshots, install superbi-mcp on Windows
   (FSL license; non-Windows is best-effort).

---

## File / disk errors

### `OSError: [Errno 28] No space left on device` mid-write

**Cause:** PBIP write operations need 2× the final size temporarily
(temp file + rename).

**Fix:** Free space on the disk.

### `Permission denied` when writing `*.Report/pages/<page>/page.json`

**Cause:** PBIP is checked into Git as read-only, or the user doesn't
own the files.

**Fix:**
```bash
chmod -R u+w path/to/report.Report
```

### `FileNotFoundError` on a path that "definitely exists"

**Cause:** Path encoding mismatch (Windows: `\\` vs `/`, macOS:
case-insensitive filesystem).

**Fix:** Use `pathlib.Path` everywhere (the orchestrator does); if
you're constructing paths in a tool, prefer `Path(...)` over string
concatenation.

---

## Validation failures

### `pre_deploy_check: failed_checks: 3`

**Cause:** the `findings_json` you passed contains blocking findings.

**Fix:**
1. Inspect the failed check names — usually these come from
   `audit_model_and_report` (BPA, WCAG, naming).
2. Fix the underlying issues:
   - BPA: rename columns, add descriptions, remove `FILTER` without
     `ISFILTERED`.
   - WCAG: add alt text to every visual; ensure 4.5:1 contrast.
   - Naming: ensure all columns / measures follow the convention.
3. Re-run `audit_model_and_report` to confirm score > threshold.

### `gate_blocked` on `deploy_to_workspace`

**Cause:** the `gate_profile` rejected your findings.

**Fix:**
1. The default `gate_profile="standard"` blocks any error-severity
   findings. Try `"relaxed"` for non-prod workspaces.
2. Or pass empty `findings` (`"[]"`) for emergency deploys (not
   recommended for real workspaces).

---

## Logs and debugging

### Where are logs?

- **stderr**: the MCP transport (visible to the LLM client).
- **structlog output**: configured in v1.9.0 to emit JSON to stderr
  when `PBI_LOG_LEVEL=DEBUG`.
- **Audit log**: SQLite database at `<config>/audit.db`. Query with
  `sqlite3 ~/.powerbi-orchestrator-mcp/audit.db "SELECT * FROM log ORDER BY ts DESC LIMIT 20"`.

### Enable debug logging

```bash
export PBI_LOG_LEVEL=DEBUG
powerbi-orchestrator-mcp --start
```

### Get a stack trace from a tool

Pass `dry_run=False` (default) and read the tool's `error_message` —
Pydantic-serialized error paths always include the exception chain.

---

## Performance

### `connect_target` takes 30+ seconds

**Cause:** the engine detector is probing 5 binaries with `--version`
subprocess calls (each with a 10s timeout).

**Fix:** This is normal — first-time startup. Subsequent calls within
the same MCP session use the cached result.

### `deploy_to_workspace` times out

**Cause:** the refresh + schedule calls each take seconds, plus token
acquisition.

**Fix:** set `timeout_s=600` (10 min) on `run_refresh` and `deploy_to_workspace`.

---

## Still stuck?

1. Search [existing issues](https://github.com/berriosb/powerbi-orchestrator-mcp/issues?q=is%3Aissue).
2. Run `python scripts/verify_mcp_server.py` to confirm the orchestrator
   itself works (rules out client-side issues).
3. File a new issue with the diagnostic info from above.