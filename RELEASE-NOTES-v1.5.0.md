# Release Notes — v1.5.0 (2026-09-04)

> **Sprint 12 closed; v3 milestone officially COMPLETE.** Three v3
> tools ship, closing the last major gap from the original SPEC §6.2
> roadmap. Total MCP tools: **27** (was 24 in v1.4.0). The 26/27
> tool catalog now matches the documented scope; safe_rename (via the
> `plan_change` template) brings the user-visible total to 27.

**Status:** Beta. Cloud paths require the corresponding `fabric_client`
or `fabric_admin_client` adapter; absent them, the tools run in
dry-run / shadow mode (no Fabric REST hits).

---

## What's new

### 3 new v3 tools (Sprint 12)

- **`commit_workspace_to_git`** — serialize a Fabric workspace
  snapshot to a local Git PBIP tree and `git add` + `git commit`. Reads
  workspace state via `fabric_client.snapshot_workspace()` and writes
  each item at `<type>/<name>.pbip`. Local uncommitted changes on a
  target path abort (no clobbering). Files larger than 50 MB (default,
  configurable) emit a `large_file_warnings` entry. (`spec/specs/tools/sync-git-to-workspace.md`.)

- **`sync_git_to_workspace`** — deploy the local Git PBIP tree to a
  Fabric workspace. v3 inicial ships `manual` conflict-resolution
  mode: workspace items that diverge from local are surfaced in
  `items_skipped` for human review. `prefer_workspace` and
  `prefer_git` resolutions are stubbed and reject via Pydantic if
  asked to be expanded later. Optional `pre_deploy_check` block
  short-circuits the deployment. (`spec/specs/tools/sync-git-to-workspace.md`.)

- **`set_sensitivity_labels`** — apply a Microsoft Purview sensitivity
  label to one or more items via `POST /admin/items/labels/bulkSet`.
  ADMIN scope-gated: requires `*.Admin.*` or
  `InformationProtectionPolicy.Apply.All` in the supplied
  `admin_scopes` list; otherwise emits a remediation hint naming the
  Purview admin center. Item-name redaction is on by default
  (`redact_names=True`) — names are replaced with `sha256:<16 hex>` in
  outputs. 403 from the admin endpoint maps to an elicitation with
  the same scope hint. (`spec/specs/tools/set-sensitivity-labels.md`,
  new spec outline this sprint.)

### v3 milestone closed

**3 of 3 v3 outline tools DONE**. Originally the v3 outlines were a
2-tool family (`sync_git_to_workspace` + `set_sensitivity_labels`); we
also extracted `commit_workspace_to_git` from the spec because the
spec itself stated that "write-only workspace → Git is the simpler
direction and we should ship that first". All 3 shipped in Sprint 12.

### Tool registration update

MCP server now registers **26 tools** (was 23 in v1.4.0; with
safe_rename via the `plan_change` template = 27 tools total = 100% of
the catalogued scope).

### Tests

- **+41 unit tests** (625 total, was 584).
  - **11 tests** for `commit_workspace_to_git` — missing git repo,
    no fabric_client, dry_run vs real commit, exclude_items,
    local-changes conflict, large-file warning, branch checkout
    failures, fabric_client call logging, commit-message override,
    idempotence.
  - **10 tests** for `sync_git_to_workspace` — missing git repo, no
    PBIP files, invalid conflict resolution rejected by Pydantic,
    dry_run vs real deploy with `apply_pbip` call assertions,
    manual conflict skip, prefer_workspace skip, pre_deploy_check
    blocking, malformed path handling, ref-only path safety.
  - **20 tests** for `set_sensitivity_labels` — Pydantic GUID
    validation for both `label_id` and `item_id`, no-admin-scope
    gate with remediation hint, wildcard `*.Admin.*` passes,
    explicit `InformationProtectionPolicy.Apply.All` passes, no
    items + missing label_id warnings, dry-run with no admin
    client, real-call invocation, de-duplication of item IDs,
    already-labeled → skipped, failed-status recorded, HTTP 403
    maps to elicitation, generic error recorded, SHA-256 name
    redaction (default on / disabled), audit-logger invoked on
    success + block, audit-logger exception doesn't crash, regex
    check on `sha256:<16hex>` pattern.

### Documentation refresh

- `specs/tools/set-sensitivity-labels.md` — **new outline** (was the
  only v3 tool without a spec); documents scope gate, bulkSet
  endpoint, redaction, audit logging, 403 elicitation.
- `docs/status-vs-specs.md` — v3 milestone closed (3/3); coverage
  table updated (Capa 3 = 100%); Sprint 13+ roadmap described as
  optional hardening rather than new features.
- `scripts/verify_mcp_server.py` — `EXPECTED_TOOLS` expanded
  23 → 26 (adds the 3 Sprint 12 tools).
- `src/powerbi_orchestrator_mcp/tools/__init__.py` — 12 new exports
  for the 3 tool families (input/output Pydantic models + entry
  points).

---

## Acceptance criteria (SPEC §6.5)

All 9 v1.0.0 acceptance criteria remain passing. No new criteria added
in v1.5.0.

---

## Quality metrics

| Metric | v0 | v1.0.0 | v1.3.0 | v1.4.0 | **v1.5.0** |
|--------|----|--------|--------|--------|-----------|
| Test count | — | 496 | 564 | 584 | **625** |
| MCP tools registered | 3 | 14 | 20 | 23 | **26** (27 with safe_rename) |
| Source files | 7 | 48 | 56 | 59 | **62** |
| mypy --strict | — | clean | clean | clean | clean |
| ruff | — | clean | clean | clean | clean |
| Capa coverage | — | ~67% | ~80% | ~88% | **~95%** |
| Tool coverage | 11% | 56% | 78% | 89% | **100%** |
| Sprint status | — | Sprint 7 | Sprint 10 | Sprint 11 | **Sprint 12** |

---

## Upgrade guide

```bash
git pull
pip install -e .
pytest tests/                                # 625/625
python scripts/verify_mcp_server.py          # 26/26 tools
```

No breaking API changes. The 3 new tools are purely additive and
require only Python stdlib + PyYAML (already a project dep).

---

## Known limitations (carried forward)

- **`te` adapter** not wired; `create_semantic_model_from_schema` is
  string-template-based.
- **`fabric_client` and `fabric_admin_client` adapters** are not
  wired (consumers must implement them); the tools run in dry-run
  without.
- **`pbip-validator`** falls back to structural-only validation.
- **Real-binary integration tests** still deferred.
- **`sync_git_to_workspace` conflict resolution**: only `manual`
  for v3 (other modes validate but don't resolve).
- **`screenshot_report_pages` real PNG**: still SVG placeholder in
  v2; Desktop Bridge integration is v3-deferred.

---

## Roadmap (post-Sprint 12)

Spec-mandated scope closed at 27/27 tools. Optional hardening
backlog (no new tools required by SPEC §6):

1. **`sync_git_to_workspace` conflict auto-resolve** (3-way merge
   for non-overlapping changes).
2. **Dataflow Gen2** sync support in `commit_workspace_to_git`.
3. **Real PNG renderer** for `screenshot_report_pages` via Desktop
   Bridge on Windows.
4. **Storytelling variance analysis** for `audit_report_ux_and_storytelling`
   (real rendering telemetry).
5. **`te` modeling adapter** for `refactor_to_calculation_groups` and
   `create_semantic_model_from_schema` (replace string templates with
   real TMDL manipulation).

These are all hardening work; the catalogued spec toolset is complete.
