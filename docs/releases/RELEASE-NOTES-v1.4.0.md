# Release Notes — v1.4.0 (2026-09-04)

> **Sprint 11 closed; v2 milestone officially COMPLETE.** Three more
> v2 tools ship, bringing the SPEC §6.2 catalog to **9/9 implemented**.
> Total MCP tools: **24** (was 21 in v1.3.0). Coverage by cap:
> Capa 1 (Modeling) +20%, Capa 3 (Cloud) +10%.

**Status:** Beta. Same caveats as prior releases for cloud paths; the
new tools are mostly stdlib-only with one PyYAML dependency (already
required by `pyyaml`).

---

## What's new

### 3 new v2 tools (Sprint 11)

- **`create_semantic_model_from_schema`** — declarative YAML/JSON spec
  → TMDL semantic model. Validates the spec (Pydantic types, dangling
  column references in relationships, basic DAX lint) and either returns
  the validation result (`dry_run=True`) or atomically writes the PBIP
  layout: `<name>.pbip`, `<name>.Dataset/definition.pbism`,
  `<name>.Dataset/definition.tmdl`. Supports tables, columns with
  declared types + format strings, measures (multi-line DAX
  expressions), relationships (cardinality + cross-filter behavior),
  user-defined hierarchies, and date-table marking. Roundtrips with
  Power BI Desktop. (`spec/specs/tools/create-semantic-model-from-schema.md`.)

- **`setup_rls_and_roles`** — apply RLS roles + members + a test matrix
  to a TMDL model. Validates a role spec (Pydantic), appends ``role``
  blocks (tablePermission + filterExpression + members) to
  ``definition.tmdl``, and runs each ``test_query`` through an injected
  ``test_engine`` callable. On any failure with
  ``rollback_on_test_failure=True`` the entire TMDL rewrite is reverted
  atomically. Risk score is a 0-1 heuristic from spec complexity.
  (`spec/specs/tools/setup-rls-and-roles.md`.)

- **`promote_in_pipeline`** — orchestrate Fabric Deployment Pipelines
  (dev → test → prod) with quality gates. Built-in gates wrap the
  existing tools: `pre_deploy_check`, `audit_model_and_report`,
  `run_dax_regression`. Custom gates are pluggable via
  ``custom_gates``. Without a real ``fabric_client`` the tool runs in
  dry-run / shadow mode (no Fabric REST hits), making it safe for CI.
  Audit-log emission hook for promotion/failure events.
  (`spec/specs/tools/promote-in-pipeline.md`.)

### v2 milestone closed

**9 of 9 v2 tools DONE** (Sprints 9 + 10 + 11):

| Sprint | Tools shipped |
|--------|---------------|
| 9 | `refactor_to_calculation_groups`, `select_visuals_for_kpis`, `design_report_page_from_requirements` |
| 10 | `optimize_report_performance`, `audit_report_ux_and_storytelling`, `screenshot_report_pages` |
| **11** | **`create_semantic_model_from_schema`, `setup_rls_and_roles`, `promote_in_pipeline`** |

### Tool registration update

MCP server now registers **23 tools** (was 20 in v1.3.0; with
`safe_rename` via the `plan_change` template = 24 tools total).

### Tests

- **+20 unit tests** (584 total, was 564).
  - **21 tests** for `create_semantic_model_from_schema` — schema
    validation, dry_run vs write, dangling-reference detection,
    TMDL renderer output for tables/columns/measures/relationships/hierarchies,
    Pydantic rejection of invalid types, no-tables warning, atomic
    write (no leftover `.tmp.*`).
  - **19 tests** for `setup_rls_and_roles` — spec parsing (YAML +
    JSON), member type validation, role block rendering, atomic TMDL
    merge, dry_run vs apply, test engine (pass + fail-with-rollback +
    fail-without-rollback), direct TMDL file path, missing PBIP
    warning, no leftover `.rls.*` tempfiles.
  - **18 tests** for `promote_in_pipeline` — invalid stages,
    same-stage short-circuit, dry-run without fabric_client, explicit
    items, real fabric_client_invoked with call-log assertions,
    fabric_client failure tolerated as warning, three gate types
    (pre_deploy_check pass/fail, audit_model_and_report
    pass/blocks-low-score/missing-PBIP, run_dax_regression missing
    baseline), invalid gate type rejected by Pydantic, custom gate
    pass/fail, non-blocking gate doesn't halt, audit_logger invoked
    on success + failure.

### Documentation refresh

- `docs/status-vs-specs.md` — v2 milestone closed (9/9 v2); coverage
  table updated (Capa 1 +20%, Capa 3 +10%); Sprint 12+ roadmap is v3.
- `docs/MVP-STATUS.md` (not updated in this release — see prior v1.3.0
  notes).
- `scripts/verify_mcp_server.py` — `EXPECTED_TOOLS` expanded
  20 → 23 (adds the 3 Sprint 11 tools).
- `src/powerbi_orchestrator_mcp/tools/__init__.py` — 14 new exports
  for the 3 tool families (input/output Pydantic models + entry points
  + TMDL renderers).

---

## Acceptance criteria (SPEC §6.5)

All 9 v1.0.0 acceptance criteria remain passing. No new criteria added
in v1.4.0.

---

## Quality metrics

| Metric | v1.0.0 | v1.1.0 | v1.3.0 | **v1.4.0** |
|--------|--------|--------|--------|-----------|
| Test count | 496 | 526 | 564 | **584** |
| MCP tools registered | 14 | 17 | 20 | **23** (24 with safe_rename) |
| Source files | 48 | 53 | 56 | **59** |
| mypy --strict | clean | clean | clean | clean |
| ruff | clean | clean | clean | clean |
| Capa coverage (weighted) | ~67% | ~73% | ~80% | **~88%** |
| Tool coverage | 15/27 (56%) | 18/27 (67%) | 21/27 (78%) | **24/27 (89%)** |
| v2 milestone | 0% | 33% | 67% | **100%** |

---

## Upgrade guide

```bash
git pull
pip install -e .
pytest tests/                                # 584/584
python scripts/verify_mcp_server.py          # 23/23 tools
```

No breaking API changes. The 3 new tools are purely additive.

---

## Known limitations (carried from v1.0.0)

- **`te` adapter** (Tabular Editor as modeling fallback) not wired;
  `create_semantic_model_from_schema` ships a deterministic
  string-template renderer. An injected `modeling_engine` seam exists
  for swapping in TOM/TE in production.
- **`fabric_client` adapter** for `promote_in_pipeline` not wired;
  the tool runs in dry-run / shadow mode without it.
- **`pbip-validator`** falls back to structural-only validation.
- **Real-binary integration tests** still deferred (require `te` /
  `powerbi-modeling-mcp` / `dscmd` installed in CI).

---

## Roadmap (Sprint 12+: v3)

- **`sync_git_to_workspace`** / **`commit_workspace_to_git`** —
  bidirectional PBIP ↔ Fabric sync (FSL-friendly).
- **`set_sensitivity_labels`** — Microsoft Purview sensitivity labels
  applied to semantic models and reports.
- **v3 deterministic rendering** for `screenshot_report_pages` (real
  PNG via Desktop Bridge when available).

Estimated 2-3 sprints to close the remaining 3 v3 tools.
