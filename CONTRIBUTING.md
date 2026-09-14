# Contributing to powerbi-orchestrator-mcp

Thanks for your interest in making Power BI workflows easier for AI agents.
This document covers the development setup, testing, and release process.

---

## Quick links

- [Code of conduct](#code-of-conduct)
- [Development setup](#development-setup)
- [Running tests](#running-tests)
- [Code style](#code-style)
- [Adding a new tool](#adding-a-new-tool)
- [Adding a new engine adapter](#adding-a-new-engine-adapter)
- [Release process](#release-process)
- [Reporting bugs](#reporting-bugs)
- [Security](#security)

---

## Code of conduct

Be respectful, constructive, and welcoming. We follow the
[Covenant Code of Conduct](https://www.contributor-covenant.org/version/2/1/code_of_conduct/).
Report unacceptable behavior to the maintainers (see [`SECURITY.md`](./SECURITY.md) for contact).

---

## Development setup

### Prerequisites

- **Python 3.11+** (we test on 3.11 + 3.12).
- **Git**.
- Optional: **Node.js** (for `powerbi-modeling-mcp` + `superbi-mcp`
  real-binary integration tests).
- Optional: **Tabular Editor CLI** (`te2`) for BPA validation tests.

### Clone & install

```bash
git clone https://github.com/berriosb/powerbi-orchestrator-mcp.git
cd powerbi-orchestrator-mcp

# Create a virtual environment (venv-semana1 was the original dev name;
# use whatever your project standardizes on).
python3.11 -m venv .venv
source .venv/bin/activate

# Install in editable mode + dev tools.
pip install -e ".[dev]"
```

This installs:
- The package + `powerbi-orchestrator-mcp` console script.
- `pytest`, `pytest-asyncio`, `pytest-cov`, `pytest-mock`, `ruff`,
  `mypy`, `types-pyyaml`.

### Verify the install

```bash
# Sanity check: the package imports and 26 tools are registered.
python scripts/verify_mcp_server.py

# Run the full test suite.
pytest tests/

# Lint + type-check.
ruff check src/powerbi_orchestrator_mcp
mypy --strict src/powerbi_orchestrator_mcp
```

All three commands must exit 0 before submitting a PR.

### Project layout

```
.
├── src/powerbi_orchestrator_mcp/
│   ├── orchestrator/      # Capa 6 — server, planner, rollback, audit, elicitation
│   ├── tools/             # High-level MCP tools (the 26 user-facing functions)
│   ├── engines/           # Adapters for powerbi-modeling-mcp, superbi-mcp, te, ...
│   ├── cloud/             # Capa 3 — Fabric + Power BI Service REST client
│   ├── validation/        # Capa 4 — BPA, DAX linter, WCAG, regression
│   └── viz/               # Capa 5 — visual registry + suggester
├── tests/
│   ├── unit/              # 823 unit tests (mocks + fakes)
│   └── integration/       # 1 multi-module E2E test (PythonReportEngine + mocked modeling)
├── examples/              # Reproducible workflows for end users (added in v1.9.0)
├── docs/                  # Architecture, MVP status, troubleshooting
├── specs/                 # Modular per-tool / per-capability specifications
├── scripts/               # verify_mcp_server.py, future helpers
├── pyproject.toml         # Build + lint + test config
└── RELEASE-NOTES-vX.Y.Z.md # Per-release changelog with decisions
```

---

## Running tests

```bash
# Run all tests.
pytest tests/

# With coverage report.
pytest tests/ --cov=powerbi_orchestrator_mcp --cov-report=term-missing

# Run only one module's tests (fast iteration).
pytest tests/unit/test_fabric_client.py -v

# Run only integration tests.
pytest tests/integration/ -v

# Watch for changes.
pytest-watch tests/unit/test_your_module.py  # requires: pip install pytest-watch
```

Coverage is enforced at **80% minimum** globally, **80% minimum** on
each new tool before merge (CI will fail otherwise).

---

## Code style

We use **ruff** for formatting + linting and **mypy --strict** for types.

### Rules

- **Type hints everywhere**. No `Any` in tool outputs (Pydantic models
  serialize to JSON Schema for the LLM).
- **Line length**: 100 chars (ruff default).
- **Imports**: sorted by `isort` (ruff's `I` rule).
- **No comments unless the code is non-obvious**. If you need a comment,
  consider extracting a helper.
- **Pydantic v2** for input/output schemas. Use `BaseModel`, `Field`.
- **async** for any I/O. Sync code is reserved for pure functions.
- **Engine errors** raised through `engines/errors.py` subclasses, never
  raw `RuntimeError` or `ValueError`.

### Pre-commit (optional but recommended)

```bash
pip install pre-commit
pre-commit install
```

`.pre-commit-config.yaml` (add if not present):

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.4.0
    hooks:
      - id: ruff
      - id: ruff-format
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.10.0
    hooks:
      - id: mypy
        files: ^src/
        args: [--strict]
```

---

## Adding a new tool

1. **Spec it first.** Add a markdown file in `specs/tools/your-tool.md`
   following the existing template (input schema, output schema,
   errors, examples).
2. **Implement** in `src/powerbi_orchestrator_mcp/tools/your_tool.py`.
   Reuse `cloud/`, `validation/`, `engines/`, `viz/` — don't reach into
   `httpx` directly.
3. **Register** in `orchestrator/server.py` with `@mcp.tool()`.
4. **Export** from `tools/__init__.py` so the orchestrator can find it.
5. **Test** in `tests/unit/test_your_tool.py`. Aim for 90%+ coverage.
6. **Update** `MVP-STATUS.md`, `CHANGELOG.md`, and the relevant
   `RELEASE-NOTES-vX.Y.Z.md`.

A minimal tool looks like:

```python
"""your_tool — one-line summary (SPEC §X.Y #N)."""
from __future__ import annotations
from pydantic import BaseModel

class YourToolInput(BaseModel):
    pbip_path: str
    # ... other fields ...

class YourToolResult(BaseModel):
    # ... output fields ...
    warnings: list[str] = []

async def your_tool(
    pbip_path: str,
    *,
    option_a: str = "default",
) -> dict:
    """Docstring with args + return shape; surfaced to the LLM."""
    # ... real implementation ...
    return YourToolResult(...).model_dump(mode="json")
```

---

## Adding a new engine adapter

1. **Subclass** `JsonRpcSubprocessEngine` (in `engines/base.py`) for
   subprocess-based engines, or implement the `ModelingEngine` /
   `ReportEngine` Protocol directly for in-process engines.
2. **Implement the Protocol** methods (`health_check`, `connect`,
   `list_tables`, `update_column`, `propagate_rename`, etc.).
3. **Register** the engine name in `engines/selector.py`'s
   `DEFAULT_MODELING_CHAIN` or `DEFAULT_REPORT_CHAIN`.
4. **Detect** it in `orchestrator/engine_detector.py` by adding an
   `EngineProbe` to `_ENGINE_PROBES`.
5. **Add timeout config** in `engines/timeouts.py` `DEFAULT_TIMEOUTS`.
6. **Test** with `mock_responses` (subprocess engines) or by
   instantiating directly (in-process engines).

For real-binary integration tests (CI runner with the binary installed):
- Use `pytest.mark.integration` (already configured for
  `tests/integration/`).
- Skip with `pytest.skip(...)` if the binary is not on PATH.

---

## Release process

1. **Pick a version.** We follow semver:
   - Patch (`1.8.0` → `1.8.1`): bug fix, no API change.
   - Minor (`1.8.0` → `1.9.0`): new tool or backward-compatible feature.
   - Major (`1.x.y` → `2.0.0`): breaking change to the tool API.

2. **Bump** in `pyproject.toml` (`[project] version = "..."`).

3. **Update** `CHANGELOG.md` and create `RELEASE-NOTES-vX.Y.Z.md` at
   the repo root.

4. **Tag** the release commit:
   ```bash
   git tag -a vX.Y.Z -m "Release vX.Y.Z"
   git push origin vX.Y.Z
   ```

5. **GitHub Actions** builds and (manually triggered) publishes to PyPI
   via `twine upload dist/*`. Required secret: `PYPI_API_TOKEN`.

6. **Announce** in the relevant channels. Update the README's "Status"
   badge.

### Pre-release checklist

```bash
# All checks green.
ruff check src/powerbi_orchestrator_mcp
mypy --strict src/powerbi_orchestrator_mcp
pytest tests/ --cov=powerbi_orchestrator_mcp --cov-fail-under=80

# Smoke test against the actual MCP transport.
python scripts/verify_mcp_server.py

# Build the package.
python -m build
```

If any of these fail, the release is blocked.

---

## Reporting bugs

Open an issue at
[https://github.com/berriosb/powerbi-orchestrator-mcp/issues](https://github.com/berriosb/powerbi-orchestrator-mcp/issues).

Include:

- The tool name (e.g. `deploy_to_workspace`).
- The MCP client you used (Claude Desktop, VS Code, Cursor, etc.).
- The OS + Python version.
- The exact input you passed (mask any secrets).
- The output you got vs what you expected.
- The `powerbi-orchestrator-mcp --version` output.

For security vulnerabilities, **do not** open a public issue — see
[`SECURITY.md`](./SECURITY.md).

---

## License

By contributing, you agree that your contributions will be licensed under
the project's [MIT License](./LICENSE).

---

## Questions?

Open a discussion at
[https://github.com/berriosb/powerbi-orchestrator-mcp/discussions](https://github.com/berriosb/powerbi-orchestrator-mcp/discussions)
or email the maintainers.