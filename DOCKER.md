# Docker

Multi-stage Dockerfile that produces a minimal ~150 MB image with the
orchestrator + all Python dependencies. Uses `uv` (Astral) for fast,
cached dependency installs.

## Build

```bash
docker build -t powerbi-orchestrator-mcp:1.19.0 .
```

## Run

### MCP server (stdio)

```bash
docker run --rm -i powerbi-orchestrator-mcp:1.19.0
```

The container listens on stdio; your MCP client attaches to it.

### Standalone CLI (validation, inspection, etc.)

```bash
docker run --rm powerbi-orchestrator-mcp:1.19.0 version
docker run --rm \
    -v /host/path/to/sample.pbip:/data:ro \
    powerbi-orchestrator-mcp:1.19.0 \
    inspect /data
docker run --rm \
    -v /host/path/to/sample.pbip:/data:ro \
    powerbi-orchestrator-mcp:1.19.0 \
    validate /data --min-score 80
```

### Docker Compose

```bash
docker compose up               # interactive stdio (MCP client)
docker compose run --rm orchestrator version
docker compose run --rm orchestrator validate /workspace/examples/01-safe-rename
```

Persistent state (audit log, plan DB, execution DB) lives in the named
volume `orchestrator-state` and survives container restarts.

## Authentication

Pass Azure AD credentials via environment variables. The orchestrator's own
names take precedence over the `AZURE_*` names the azure-identity SDK uses,
so set these to pin the identity the container authenticates as:

```bash
docker run --rm -i \
    -e PBI_AUTH_MODE=service_principal \
    -e PBI_TENANT_ID=00000000-0000-0000-0000-000000000000 \
    -e PBI_CLIENT_ID=00000000-0000-0000-0000-000000000000 \
    -e PBI_CLIENT_SECRET=your-secret \
    powerbi-orchestrator-mcp:1.19.0
```

`AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` are still
read as a fallback. `PBI_AUTH_MODE` selects the mode
(`interactive` — the default — `service_principal`, `managed_identity`).

Or mount a `.env` file via docker-compose.

## HTTP transport

To serve over HTTP instead of stdio, the container needs the bind address,
the Entra ID settings, and the Host names it answers to:

```bash
docker run --rm -p 8000:8000 \
    -e PBI_TRANSPORT=http \
    -e PBI_HTTP_HOST=0.0.0.0 \
    -e PBI_HTTP_ALLOWED_HOSTS=orchestrator.internal \
    -e PBI_ENTRA_TENANT_ID=00000000-0000-0000-0000-000000000000 \
    -e PBI_ENTRA_AUDIENCE=api://powerbi-orchestrator-mcp \
    powerbi-orchestrator-mcp:1.19.0
```

`PBI_HTTP_ALLOWED_HOSTS` is a comma-separated list of Host header values the
server answers to. The MCP SDK's DNS-rebinding protection is always on; it
only allows loopback hosts by default, so a server bound to a container
address would otherwise reject every request with 421.

## What's NOT in the image

- **Node.js** — only needed for `powerbi-modeling-mcp` /
  `superbi-mcp` real-binary tests. The orchestrator works without them
  (falls back to `python_report` / `te` skeleton mode).
- **Tabular Editor CLI** (`te2`) — install via the package manager or
  mount the binary. Without it the BPA check is skipped, and the audit
  score is reported as partial.
- **Power BI Desktop** — Windows-only; not in the image. Used by
  `screenshot_report_pages` for real pixel-perfect screenshots; without
  it the tool emits SVG wireframes + JSON manifests.

## Image size

Target: ~150 MB. The base is `python:3.11-slim` (~50 MB) + ~100 MB of
Python packages (mcp, azure-identity, httpx, pydantic).

To verify:
```bash
docker images powerbi-orchestrator-mcp:1.19.0
```
