# Release Notes — v1.9.0 (2026-09-14)

> **Sprint 16 — product-readiness pass.** v1.8.0 had every tool working
> and tests green, but lacked the polish expected of a real product.
> This release adds distribution + onboarding + diagnostics + a
> standalone CLI + Docker image + plugin system. The orchestrator now
> ships in a form a new user can install + configure + run in 60
> seconds without help from the maintainer.

**Status:** Beta. No breaking changes to the existing 27 tools. New
artifacts: 4 markdown files at repo root, 3 example workflows, 1
standalone CLI, 1 Dockerfile, 1 plugin system, 1 fixture generator,
4 new test files, expanded CI matrix.

---

## What's new

### 1. Distribution + onboarding (Phase 1)

The biggest gap in v1.8.0 was the new-user experience: someone
cloning the repo had to guess the install steps, the test fixture
didn't actually exist (only a spec did), and there was no
troubleshooting guide for common errors.

v1.9.0 ships:

- **`CHANGELOG.md`** — condensed changelog following Keep-a-Changelog
  format. The 8 per-release `RELEASE-NOTES-vX.Y.Z.md` files stay for
  full context.
- **`CONTRIBUTING.md`** — dev setup, testing, release process,
  pre-commit, how to add a new tool / engine / plugin.
- **`SECURITY.md`** — vulnerability disclosure policy, supported
  versions, threat model, what we log + redact.
- **`docs/troubleshooting.md`** — common errors with remediation:
  AADSTS codes, missing engines, MCP client configs, file-permission
  errors, validation failures.
- **`examples/`** — 3 reproducible workflows:
  - `01-safe-rename/` — `connect_target` → `plan_change` →
    `apply_plan` → rollback.
  - `02-deploy-pbip/` — pre-deploy gate → publish → schedule →
    initial refresh.
  - `03-audit-then-fix/` — baseline audit → WCAG fix → lint-gated
    writes → re-audit.
- **README.md** — added badges (tests, coverage, python, license, MCP),
  a 60-second quickstart, links to the new docs.

### 2. SQLite-backed plan store (Phase 2)

`orchestrator/plan_store.py` introduces `PlanStore` — a
SQLite-backed persistence layer for plans created by `plan_change`.
This replaces the in-memory `dict[str, Plan]` in `server.py`, giving:

- **Survives restarts** — the user doesn't need to re-call
  `plan_change` after a Ctrl-C + restart.
- **Multi-process safety** — SQLite WAL gives MVCC isolation per
  writer (matters for v4 HTTP transport).
- **Same `Plan` shape** — no API change to tools.

Wiring into `apply_plan` is queued for v1.10.0; for now `server.py`
keeps the dict (no behaviour change) but the `PlanStore` API is
available for downstream users (e.g. the standalone CLI).

### 3. `powerbi_health` MCP tool (Phase 2)

New tool that returns a diagnostic snapshot:

```json
{
  "server_version": "1.9.0",
  "uptime_seconds": 12.4,
  "checks": [
    {"name": "engine.powerbi-modeling-mcp", "ok": false,
     "detail": "binary not found in PATH (tried powerbi-modeling-mcp)"},
    ...
  ],
  "engines": {...},
  "plan_store_count": 3,
  "execution_count": 12,
  "audit_entries": 47,
  "warnings": ["engine 'powerbi-modeling-mcp' unavailable"],
  "remediation": [
    "Install powerbi-modeling-mcp: `npx -y @microsoft/powerbi-modeling-mcp`",
    "Install Tabular Editor: ..."
  ]
}
```

Designed for the LLM to surface "X is missing, here's how to install
it" without the workflow hitting a brick wall.

Tool count: **26 → 27**.

### 4. Standalone CLI (Phase 3)

New console script: **`powerbi-orchestrator`**. Lets you script
workflows without launching an LLM client.

```bash
# Check the orchestrator is installed
powerbi-orchestrator version

# Inspect a PBIP folder
powerbi-orchestrator inspect /path/to/report.pbip

# Run a composite audit with threshold
powerbi-orchestrator validate /path/to/report.pbip --min-score 80

# Scaffold a new PBIP
powerbi-orchestrator init /path/to/new.pbip

# Verify the audit log
powerbi-orchestrator audit-verify

# Run health diagnostics
powerbi-orchestrator health
```

All subcommands are thin wrappers over the public Python API
(`audit_model_and_report`, `generate_data_dictionary`,
`powerbi_health`, `verify_cli`). Exit codes: `0` = success,
`1` = below threshold, `2` = bad input.

### 5. Docker (Phase 3)

- **`Dockerfile`** — multi-stage build (uv + python:3.11-slim) →
  ~150 MB image with the orchestrator + CLI.
- **`docker-compose.yml`** — persistent state volume, easy mount of
  PBIP folders.
- **`DOCKER.md`** — usage guide for MCP stdio + CLI workflows.

```bash
docker build -t powerbi-orchestrator-mcp:1.9.0 .
docker run --rm -i powerbi-orchestrator-mcp:1.9.0   # stdio
docker run --rm powerbi-orchestrator-mcp:1.9.0 validate /data
```

### 6. Real PBIP fixture + E2E tests (Phase 4)

`tests/fixtures/README.md` claimed a fixture existed but it didn't.
Sprint 16 closes that gap:

- **`tests/fixtures/generate.py`** — deterministic generator (seed 42)
  producing `sample.pbip` with 4 tables, ~15 measures, 2 pages.
- **`tests/fixtures/sample.pbip/`** — the actual fixture (now
  committed to the repo).
- **`tests/integration/test_real_pbip_e2e.py`** — 13 tests exercising
  the orchestrator on the real fixture:
  - `audit_model_and_report` on the fixture.
  - `apply_theme_and_accessibility_rules` writes theme.json + alt text.
  - `optimize_report_performance` returns a score.
  - `generate_data_dictionary` produces Mermaid markdown.
  - `validate_pbir` passes.
  - `propagate_rename` updates page.json.
  - CLI: `version`, `init`, `inspect`, `validate` work end-to-end.
  - `powerbi_health` returns a full report.

### 7. CI matrix expansion (Phase 4)

`verify.yml` now runs:

- **Lint**: Python 3.11 + 3.12 + 3.13 on Linux + macOS.
- **Test**: same matrix + Windows (Python 3.11 only — Windows CI
  minutes are expensive).
- **Integration**: Python 3.11 + 3.12 on Linux + macOS (separate job).

This catches platform-specific regressions (subprocess args,
tempfile paths, etc.) before users hit them.

### 8. Plugin system (Phase 5)

`plugins.py` loads Python modules from
`~/.powerbi-orchestrator-mcp/plugins/`. Each plugin can add:

- `GATE_PROFILES` — additional / overriding pre-deploy gate profiles.
- `BPA_RULES` — custom BPA rules with Python callables that inspect
  the model state.

Use cases: org-specific naming conventions, internal data-quality
checks, custom gate policies for regulated industries.

A plugin file looks like:

```python
# ~/.powerbi-orchestrator-mcp/plugins/my_org_rules.py
from powerbi_orchestrator_mcp.validation.pre_deploy_gate import (
    GateProfile, GateThresholds,
)

GATE_PROFILES = [
    GateProfile(
        name="acme-strict",
        warning=GateThresholds(max_findings=0, blocking=True),
    ),
]
```

Plugin load failures are isolated (one broken plugin doesn't take the
others down) and surfaced via `powerbi_health.warnings`.

---

## Numbers

| | v1.8.0 | v1.9.0 | Δ |
|---|---:|---:|---:|
| Tools registered | 26 | **27** | +1 (`powerbi_health`) |
| Tests | 823 | **867** | +44 |
| Coverage | 91% | **89%** | -2pp (more surface area) |
| Console scripts | 1 | **2** | +1 (`powerbi-orchestrator`) |
| Markdown files at repo root | 8 release notes + 1 README | + CHANGELOG + CONTRIBUTING + SECURITY + DOCKER | +4 |
| Examples | 0 | **3** | +3 workflows |
| CI matrix | py3.11+3.12 × Linux+Windows | + py3.13 + macOS + integration job | +12 cells |
| Docker artifacts | none | **Dockerfile + compose + DOCKER.md** | +3 |
| Plugin system | none | `plugins.py` + 11 tests | +1 |
| PBIP fixture | spec only | real `sample.pbip/` + generator | ✅ |
| | | | |
| **mypy --strict clean** | | | ✅ |
| **ruff src clean** | | | ✅ |
| **verify_mcp_server.py** | | | ✅ |

---

## Verification

```bash
pip install -e ".[dev]"

# All checks green
ruff check src/powerbi_orchestrator_mcp tests
mypy --strict src/powerbi_orchestrator_mcp
pytest tests/                           # 867/867
python scripts/verify_mcp_server.py     # 27 tools
powerbi-orchestrator version           # 1.9.0
powerbi-orchestrator health             # JSON snapshot
```

CI matrix (Linux + macOS + Windows, py3.11/3.12/3.13) green.

---

## Breaking changes

**None.** All v1.8.0 tools + their signatures are unchanged.

The new `PlanStore` is additive; `server.py` still uses the in-memory
dict (will switch in v1.10.0).

---

## Migration notes

No migration. Install `pip install -e .` and you're on v1.9.0.

Optional follow-ups for existing users:

- Read [`CHANGELOG.md`](./CHANGELOG.md) + this file.
- Drop a PBIP into [`examples/`](./examples/) to try the new
  workflows.
- Add `~/.powerbi-orchestrator-mcp/plugins/*.py` if you have org-specific
  rules.

---

## Next up (Sprint 17+ backlog)

- Wire `PlanStore` into `server.py` (replace in-memory dict).
- HTTP transport + Entra OAuth (v4 path).
- Real-binary E2E matrix: Tabular Editor, dscmd, superbi-mcp on
  Windows runner.
- Plugin discovery → powerbi_health surfaces loaded plugin count.
- Marketplace of page templates.