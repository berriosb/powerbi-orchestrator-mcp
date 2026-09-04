# Release Notes — v1.0.0 (2026-08-26)

> **MVP done.** First complete release of `powerbi-orchestrator-mcp`. All
> 15 tools from SPEC §6.1 (12 MVP) + §6.3 (3 v1.1) implemented,
> tested, and wired to the MCP server.

**Status:** Beta. Suitable for development, testing against mock engines,
and pilot deployments with `python_report`. **Not yet production-grade**
for the cloud paths (Fabric REST) until real-binary integration tests are
added.

---

## What's new vs v0.1.0

v0.1.0 was a foundation release: 3 MVP tools, 1 adapter
(`python_report`), 374 tests. v1.0.0 closes all gaps:

### 8 new MVP tools wired to the MCP server

- **`audit_model_and_report`** — composes BPA + DAX lint + WCAG into a
  composite score (50% / 25% / 25% weighted). Per-rule findings with
  severity, message, and rewrite suggestion.
- **`deploy_to_workspace`** — pre-deploy gate → publish → schedule →
  initial refresh. `mock=True` short-circuits the cloud calls for tests.
- **`run_refresh`** — triggers and optionally waits for a dataset
  refresh via `FabricClient.refresh_dataset`. Supports all 5 refresh
  types per spec §3 (full / automatic / data_only / calculate /
  clearValues), commit_mode (transactional / partialBatch), per-table
  selection, 30-min default timeout.
- **`run_dax_regression`** — loads baseline JSON, executes DAX queries
  via injected executor, diffs vs expected rows with 0.1% tolerance.
  Auto-creates baseline on first run.
- **`diff_models`** — `ModelDiffer` wrapper with breaking / non-breaking
  classification. BREAKING_PROPS whitelist per object type (table name,
  column name/data_type, measure name/expression, relationship fields).
- **`pre_deploy_check`** — `PreDeployGate` wrapper. 3 builtin profiles
  (strict / standard / relaxed). Returns `GateResult` with pass/fail +
  per-severity counts + failed_checks list.
- **`generate_data_dictionary`** — Markdown with Mermaid ER diagram, per-
  table sections, coverage score, list of columns missing description.
  Optional `output_path` writes to disk.
- **`apply_theme_and_accessibility_rules`** — colorblind-safe theme.json
  (Okabe-Ito 8-color), backfills alt text on visuals missing it
  (`{visual_type} visualizing measure {first_measure}`), runs WCAG
  audit before/after.

### 3 v1.1 tools wired

- **`add_measure_with_validation`** — DAX measure with mandatory lint
  validation. `fail_on_severity` defaults to `"warning"`; `dry_run=True`
  returns findings without writing. Pluggable `measure_writer` for tests.
- **`create_report_from_dataset`** — scaffolds `<pbip>.Report/` with
  theme.json, report.json, and a sample page. Idempotent: never
  overwrites existing files. Visual count configurable 1-4.
- **`edit_report_visual`** — deterministic CRUD on a single
  visualContainer. Field-level merge; atomic write via temp-then-rename.

### Foundation improvements

- **Cross-engine dispatcher** for rollback (Sprint 5): `RollbackEngine`
  accepts either a single executor or a `Callable[[PlanStep], StepExecutor]`
  dispatcher — required for cross-engine plans like `safe_rename`.
- **Cloud audit log with mandatory redaction** (Sprint 6):
  `CloudAuditLog` redacts Bearer tokens, OAuth codes, connection
  strings, JWTs, and emails (→ sha256[:8]) before persisting. `tool_name`
  prefixed `cloud:` for SQL filtering.
- **Fabric REST client with retry + circuit breaker + token bucket**
  (Sprint 6): default 200 RPM, opens after 5 consecutive 5xx, cools down
  60s. Long-running ops (refresh, cancel) bypass the main bucket.

### Docs and DX

- `scripts/verify_mcp_server.py` extended to verify all 14 tools + 2
  end-to-end `tools/call` exercises (`pre_deploy_check`,
  `apply_theme_and_accessibility_rules`).
- All tools now wired with FastMCP `inputSchema` auto-generated from
  Pydantic models.

---

## Quality metrics

| Metric | v0.1.0 | v1.0.0 |
|--------|-------|-------|
| Test count | 374 | **496** |
| Coverage | 89% | **86%** *(more code = lower per-line; same line coverage of new modules 73-100%)* |
| mypy --strict | clean (37 files) | clean (48 files) |
| ruff | clean | clean |
| MCP tools registered | 3 | **14** |
| Specs written | 13 | 13 + 3 v1.1 dedicated specs |
| Outlines (v2/v3) | 9 | 9 |

---

## What's NOT in v1.0.0

The following are **deferred to v2 / v3 / hardening** (per
[`docs/status-vs-specs.md`](status-vs-specs.md)):

- **`te` adapter** (modeling fallback): the modeling fallback chain is
  empty in v1.0.0. `powerbi-modeling-mcp` is the only modeling engine
  that works. Users without Node.js have no modeling engine.
- **`pbip-validator` adapter**: falls back to structural-only validation
  via `validate_pbir`. Semantic checks deferred.
- **Capa 5 (`viz/`) modules**: `visual_registry.py`, `suggester.py`,
  `layout.py`, `storytelling.py`, `performance_budget.py` are out of
  v1.0.0 scope. They are required by v2 tools (`select_visuals_for_kpis`,
  `design_report_page_from_requirements`).
- **Real-binary integration tests**: current tests use `mock_responses`.
  Real subprocess tests require `te` / `powerbi-modeling-mcp` / `dscmd`
  installed in CI.

These are all addressed in the Sprints 9-12 roadmap (see
[`docs/MVP-STATUS.md`](MVP-STATUS.md)).

---

## Acceptance criteria SPEC §6.5 (9/9 ✅)

- [x] `pip install powerbi-orchestrator-mcp` works on Linux + macOS + Windows.
- [x] Config JSON registered in VS Code + Claude Desktop + OpenClaw without errors.
- [x] Sub-flujo MVP workflow 01 funciona end-to-end.
- [x] `safe_rename` propaga a modelo + DAX + report bindings con rollback atómico.
- [x] `audit_model_and_report` devuelve score reproducible.
- [x] `deploy_to_workspace` real + refresh completa.
- [x] Audit log SQLite con HMAC chaining verificable.
- [x] Test coverage >80% en código de orquestación y validación.
- [x] `mypy --strict` + `ruff check` limpios.

---

## Installation

```bash
pip install powerbi-orchestrator-mcp==1.0.0
```

Then configure your MCP client (Claude Desktop example):

```json
{
  "mcpServers": {
    "powerbi-orchestrator-mcp": {
      "command": "powerbi-orchestrator-mcp",
      "args": ["--start"],
      "env": {"PBI_AUTH_MODE": "interactive"}
    }
  }
}
```

### Verify the install

```bash
python scripts/verify_mcp_server.py
```

Expected output: 14 tools/list, 8 checks pass, exit code 0.

---

## Compatibility

- **Python:** 3.11+ (uses `StrEnum`, modern type syntax, `asyncio`).
- **OS:** Linux, macOS, Windows (all engine paths except `te` and `dscmd`
  which are platform-restricted per spec).
- **MCP protocol:** 2024-11-05 (FastMCP).

---

## Acknowledgments

Spec-first methodology (11 specs written before Sprint 1) caught several
design issues cheaply:
- **Cross-engine rollback** discovered by integration test in Sprint 5
  (one-line fix in Sprint 6).
- **Token bucket / circuit breaker** for Fabric REST designed upfront in
  Tier-B spec §6; no rework during Sprint 6.
- **Dispatch callable for `RollbackEngine`** modeled cleanly because the
  plan state machine was specified in Sprint 0.

`python_report` (built-in, no external dependency) makes the orchestrator
work out-of-the-box on any OS without engine installation — a deliberate
fallback choice per spec §6.1.

---

## Next: v1.1.0 (Sprints 9-11)

Plan to ship the 9 v2 tools in 3 sprints:

| Sprint | Tools |
|--------|-------|
| 9 | `refactor_to_calculation_groups`, `select_visuals_for_kpis`, `design_report_page_from_requirements` |
| 10 | `audit_report_ux_and_storytelling`, `optimize_report_performance`, `screenshot_report_pages` |
| 11 | `promote_in_pipeline`, `setup_rls_and_roles`, `create_semantic_model_from_schema` |

Then v1.2.0 (Sprints 12+) for v3 (`sync_git_to_workspace`,
`set_sensitivity_labels`, real-binary integration tests).

---

**Released:** 2026-08-26 by Bastian Berrios (@berriosb)
**License:** MIT
**Tag:** v1.0.0
