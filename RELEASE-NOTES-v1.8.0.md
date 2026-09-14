# Release Notes — v1.8.0 (2026-09-14)

> **Sprint 15 — closing the real-cloud-path gaps.** Removes the three
# remaining "TODO Week 2" stubs in `deploy_to_workspace`, adds the
# missing Power BI Service REST endpoints to `fabric_client`, and
# reconciles the documentation drift (`MVP-STATUS.md` was lagging the
# code base by ~3 sprints).

**Status:** Beta. No breaking changes — only additive behavior + bug
fixes to the `deploy_to_workspace` real path.

---

## What's new

### 1. `fabric_client` ships the missing Power BI Service endpoints

Per spec §2.3 the dataset endpoints live on the **Power BI Service REST
API** (`https://api.powerbi.com/v1.0/myorg`), not the Fabric Items API
(`https://api.fabric.microsoft.com/v1`). Until now `FabricClient` used
a single Fabric base URL, which silently mis-routed dataset calls.

Sprint 15 introduces two httpx clients in `FabricClient`:

- `_fabric_client` → `https://api.fabric.microsoft.com/v1` (workspaces,
  items).
- `_pbi_client` → `https://api.powerbi.com/v1.0/myorg` (datasets,
  refresh, schedules).

`_request` accepts a `service="fabric"|"pbi"` discriminator and the
high-level methods now select the right base URL. New endpoints:

| Method | Path | Service |
|---|---|---|
| `create_item(workspace_id, display_name, item_type, definition=...)` | `POST /v1/workspaces/{id}/items` | fabric |
| `delete_item(workspace_id, item_id)` | `DELETE /v1/workspaces/{id}/items/{itemId}` | fabric |
| `list_items(workspace_id, item_type=...)` | `GET /v1/workspaces/{id}/items` | fabric |
| `get_item(workspace_id, item_id)` | `GET /v1/workspaces/{id}/items/{itemId}` | fabric |
| `cancel_refresh(workspace_id, dataset_id)` | `POST /groups/{id}/datasets/{dsId}/refreshes/cancel` | pbi |
| `get_refresh_history(workspace_id, dataset_id, top=10)` | `GET /groups/{id}/datasets/{dsId}/refreshes` | pbi |
| `update_refresh_schedule(workspace_id, dataset_id, *, schedule)` | `PATCH /groups/{id}/datasets/{dsId}/refreshSchedule` | pbi |
| `take_over_dataset(workspace_id, dataset_id)` | `POST /groups/{id}/datasets/{dsId}/takeover` | pbi |
| `update_datasource(workspace_id, dataset_id, datasource_id, *, body)` | `PATCH /groups/{id}/datasets/{dsId}/datasources/{dsId}` | pbi |
| `execute_queries(workspace_id, dataset_id, *, queries, impersonated_user_name=...)` | `POST /groups/{id}/datasets/{dsId}/queries` | pbi |

The `PATCH` HTTP verb is now supported at the transport level (was
missing — only GET/POST/DELETE before).

`deploy_to_workspace` (`mock=False`) now uses these endpoints
end-to-end: `create_item` (Fabric) + `update_refresh_schedule` +
`refresh_dataset` (Power BI Service). The `# TODO Week 2` comments are
gone.

### 2. `deploy_to_workspace` — real path wired

The two `# TODO Week 2` markers in `deploy_to_workspace.py` are gone.
With `mock=False` and valid Azure credentials, the tool now:

1. Calls `PreDeployGate` (unchanged).
2. `POST /v1/workspaces/{ws}/items` with `type=PowerBIDataset` (real
   `FabricClient.create_item`).
3. `PATCH /v1.0/myorg/groups/{ws}/datasets/{ds}/refreshSchedule` with
   a daily schedule at `refresh_daily_hour` (UTC, clamped 0-23).
4. `POST /v1.0/myorg/groups/{ws}/datasets/{ds}/refreshes` to trigger
   the initial refresh.
5. Each step surfaces independently in `DeployResult`:
   - `publish_ok`, `item_id`, `schedule_ok`, `refresh_id`, `dataset_id`,
   - `errors` collects per-step failure messages (e.g.
     `publish_failed: <exception>`).

This means the tool now does what the docstring always said it did.

### 3. `superbi_mcp` — fall-back path is the implementation

The `add_page` / `add_visual` / `update_visual` / `propagate_rename`
methods on `SuperBiMcpEngine` previously carried `# TODO` markers that
were misleading — the methods already routed through `_dispatch` (with
mocked responses for tests) and **fell back to `PythonReportEngine`**
when the binary didn't expose the requested method.

Sprint 15 makes the fallback explicit in code: the `_dispatch` call is
wrapped in `try/except` and the `PythonReportEngine` fallback runs
deterministically. No behavior change for current users; the doc
strings and TODO comments are accurate now.

### 4. Documentation sync — `MVP-STATUS.md` rewritten

`docs/MVP-STATUS.md` claimed Sprints 10/11/12 were "pendiente" while
the code base had shipped all 26 tools months earlier. Sprint 15
replaces the doc with the **real** post-Sprint 14 state:

- Capa 1 (Modeling): 80% — TE adapter shipped (Sprint 14A).
- Capa 2 (Report): 95% — superbi_mcp with explicit fallback.
- Capa 3 (Cloud): 100% — both Fabric Items API + Power BI Service REST.
- Capa 4 (Validación): 100% — all 5 modules.
- Capa 5 (Viz/UX): 90% — all 5 v2 tools shipped.

### 5. Coverage + tests

- `optimize_report_performance`: **28% → 100%** (new
  `tests/unit/test_optimize_report_performance.py`, 25 tests covering
  `_classify_cost`, `_estimate_visual_cost`, `_suggest_fix`,
  `_analyze_page`, plus end-to-end paths including corrupt JSON).
- `fabric_client`: **70% → 75%** (10 new tests covering the new
  endpoints + the fabric/pbi base-URL discriminator).
- `deploy_to_workspace`: 93% (2 new tests: real-path success + failure
  surfacing).

#### Sprint 15 follow-ups (post-v1.8 release)

Six new test files covering low-coverage modules:

- `audit_report_ux_and_storytelling`: **33% → 100%** (51 tests across
  all 5 heuristics + page loading + main entry).
- `bpa_runner`: **55% → 100%** (13 tests covering subprocess paths:
  missing binary, timeout, non-zero exit, JSON decode error, payload
  parsing with both TE and lowercase keys).
- `engines/base.py`: **69% → 92%** (11 tests covering
  `JsonRpcSubprocessEngine` subprocess plumbing via fake
  asyncio.StreamReader-backed subprocesses — spawn-time, RPC success /
  error / timeout / stdin-closed, stdout reader, stop cancel-pending).
- `engines/report_python.py`: **78% → 83%** (24 tests for
  `_replace_in_obj`, `propagate_rename`, `validate_pbir` per-page
  checks, and the `_PythonReportExecutor` dispatcher for every
  action branch + EngineError catch).
- `engine_detector`: **80% → 100%** (7 tests for `_probe_version`
  covering empty args, success, empty stdout, non-zero exit, OSError
  on spawn, TimeoutError, and UTF-8 decode errors).
- `edit_report_visual`: **81% → 100%** (10 tests for the missing-paths
  branches: no .Report dir, missing page, invalid page.json, invalid
  format/position JSON, format / is_hidden changes, atomic-write
  failure).

**Total**: 823 tests, 91% coverage (was 707 / 86%).

---

## Verification

```bash
pip install -e .
python scripts/verify_mcp_server.py    # 26 tools/list, tools/call OK
pytest tests/                          # 707/707 passing
mypy --strict src/powerbi_orchestrator_mcp   # clean
ruff check src/powerbi_orchestrator_mcp       # clean
```

CI matrix (Linux + macOS, Python 3.11 + 3.12) green.

---

## Breaking changes

None. New methods on `FabricClient` are additive. The signature of
`deploy_to_workspace` is unchanged; `DeployResult` gains two new
optional fields (`item_id`, `dataset_id`) that default to `None` in
mock mode and to real values otherwise.

---

## Next up (Sprint 16+ backlog)

- `pip install powerbi-orchestrator-mcp` from PyPI (currently `git+...`
  only).
- E2E tests with real Tabular Editor + dscmd binaries (need a Windows
  runner + license).
- Marketplace of page templates.
- Plugin system for org-specific BPA rules.