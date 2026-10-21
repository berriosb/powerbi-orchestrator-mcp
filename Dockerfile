# Multi-stage Dockerfile for powerbi-orchestrator-mcp.
#
# Stage 1: builder — installs the package + dependencies from uv.lock.
# Stage 2: runtime — minimal image with just the package + runtime deps.
#
# The image is for running the orchestrator as an MCP server (stdio).
# For HTTP transport you'd expose the port and use a process manager that
# proxies stdio.
#
# Build:
#   docker build -t powerbi-orchestrator-mcp:1.21.0 .
#
# Run (stdio for MCP — connect via your MCP client):
#   docker run --rm -i powerbi-orchestrator-mcp:1.21.0
#
# Run the standalone CLI (the entrypoint is the MCP server, so the CLI
# needs its own entrypoint):
#   docker run --rm --entrypoint powerbi-orchestrator \
#     powerbi-orchestrator-mcp:1.21.0 version
#   docker run --rm --entrypoint powerbi-orchestrator \
#     -v /host/path:/data powerbi-orchestrator-mcp:1.21.0 validate /data
#

# ---------------------------------------------------------------------------
# Stage 1 — builder
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

# The uv version has to be able to read the lock file. The lock is on
# revision 5, which needs a recent uv; the old 0.4.18 pin could not read
# it, which is why the dependency list used to be spelled out by hand
# here instead.
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# The lock file pins the dependency graph. --locked makes the build fail
# rather than silently resolve something else, so the image holds the
# versions that were audited, not the ones that happen to resolve today.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-install-project --no-dev

# Now copy the source and install the package itself. --no-editable puts
# a real copy in site-packages: an editable install would point back at
# /app/src, which the runtime stage does not carry.
COPY src /app/src
RUN uv sync --locked --no-dev --no-editable

# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# Non-root user for security (defence in depth — the orchestrator can
# read filesystem + call APIs).
RUN useradd --system --create-home --uid 10001 orchestrator

# Copy the pre-built virtualenv.
COPY --from=builder /app/.venv /app/.venv

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PBI_AUTH_MODE=interactive

USER orchestrator
WORKDIR /home/orchestrator

# Default to the MCP stdio transport. Override with ``powerbi-orchestrator``
# for the standalone CLI.
ENTRYPOINT ["/app/.venv/bin/powerbi-orchestrator-mcp"]
CMD ["--start"]

# Labels.
LABEL org.opencontainers.image.title="powerbi-orchestrator-mcp" \
      org.opencontainers.image.description="High-level MCP orchestrator for Power BI / Microsoft Fabric" \
      org.opencontainers.image.source="https://github.com/berriosb/powerbi-orchestrator-mcp" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="1.21.0"
