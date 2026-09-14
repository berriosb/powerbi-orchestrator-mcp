# Docker

Multi-stage Dockerfile that produces a minimal ~150 MB image with the
orchestrator + all Python dependencies. Uses `uv` (Astral) for fast,
cached dependency installs.

## Build

```bash
docker build -t powerbi-orchestrator-mcp:1.8.0 .
```

## Run

### MCP server (stdio)

```bash
docker run --rm -i powerbi-orchestrator-mcp:1.8.0
```

The container listens on stdio; your MCP client attaches to it.

### Standalone CLI (validation, inspection, etc.)

```bash
docker run --rm powerbi-orchestrator-mcp:1.8.0 version
docker run --rm \
    -v /host/path/to/sample.pbip:/data:ro \
    powerbi-orchestrator-mcp:1.8.0 \
    inspect /data
docker run --rm \
    -v /host/path/to/sample.pbip:/data:ro \
    powerbi-orchestrator-mcp:1.8.0 \
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

Pass Azure AD credentials via environment variables:

```bash
docker run --rm -i \
    -e PBI_AUTH_MODE=service_principal \
    -e PBI_TENANT_ID=00000000-0000-0000-0000-000000000000 \
    -e PBI_CLIENT_ID=00000000-0000-0000-0000-000000000000 \
    -e PBI_CLIENT_SECRET=your-secret \
    powerbi-orchestrator-mcp:1.8.0
```

Or mount a `.env` file via docker-compose.

## What's NOT in the image

- **Node.js** — only needed for `powerbi-modeling-mcp` /
  `superbi-mcp` real-binary tests. The orchestrator works without them
  (falls back to `python_report` / `te` skeleton mode).
- **Tabular Editor CLI** (`te2`) — install via the package manager or
  mount the binary. The orchestrator works without it (skeleton mode).
- **Power BI Desktop** — Windows-only; not in the image. Used by
  `screenshot_report_pages` for real pixel-perfect screenshots; without
  it the tool emits SVG wireframes + JSON manifests.

## Image size

Target: ~150 MB. The base is `python:3.11-slim` (~50 MB) + ~100 MB of
Python packages (mcp, azure-identity, httpx, pydantic).

To verify:
```bash
docker images powerbi-orchestrator-mcp:1.8.0
```