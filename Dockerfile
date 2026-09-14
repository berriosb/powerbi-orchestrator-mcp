# Multi-stage Dockerfile for powerbi-orchestrator-mcp.
#
# Stage 1: builder — installs the package + dependencies.
# Stage 2: runtime — minimal image with just the package + runtime deps.
#
# The image is for running the orchestrator as an MCP server (stdio).
# For HTTP transport (planned v4) you'd expose port + use a process
# manager that proxies stdio.
#
# Build:
#   docker build -t powerbi-orchestrator-mcp:1.8.0 .
#
# Run (stdio for MCP — connect via your MCP client):
#   docker run --rm -i powerbi-orchestrator-mcp:1.8.0
#
# Run the standalone CLI:
#   docker run --rm powerbi-orchestrator-mcp:1.8.0 version
#   docker run --rm -v /host/path:/data powerbi-orchestrator-mcp:1.8.0 validate /data
#

# ---------------------------------------------------------------------------
# Stage 1 — builder
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

# uv is significantly faster than pip + caches better.
COPY --from=ghcr.io/astral-sh/uv:0.4.18 /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy only what uv needs to resolve dependencies first (cache layer).
COPY pyproject.toml ./

# Create a virtualenv at /app/.venv for the runtime stage to copy.
RUN uv venv /app/.venv --python 3.11 && \
    uv pip install --python /app/.venv/bin/python \
        "mcp[cli]>=1.0.0,<2.0.0" \
        "pydantic>=2.0.0,<3.0.0" \
        "pydantic-settings>=2.0.0" \
        "azure-identity>=1.15.0" \
        "azure-core>=1.30.0" \
        "httpx>=0.27.0" \
        "structlog>=24.1.0" \
        "pyyaml>=6.0.1" \
        "rich>=13.7.0"

# Now copy the source and install the package itself (no deps).
COPY src /app/src
RUN uv pip install --python /app/.venv/bin/python --no-deps /app

# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# Non-root user for security (defence in depth — the orchestrator can
# read filesystem + call APIs).
RUN useradd --system --create-home --uid 10001 orchestrator

# Copy the pre-built virtualenv.
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
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
      org.opencontainers.image.licenses="MIT"