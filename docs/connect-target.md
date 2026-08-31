# `connect_target` — Usage Guide

> How to use the `connect_target` tool to open a session against a
> Power BI target (PBIP folder, Fabric workspace, PBI Desktop, or
> legacy `.pbix` file), what the output looks like, and how to
> interpret the warnings and engines_available fields.

**Spec:** [`specs/01-orchestrator.md` §3.1](../specs/01-orchestrator.md) + [`specs/05-engines-adapters.md` §11](../specs/05-engines-adapters.md)
**Source:** `src/orchestrator/server.py::connect_target` + `src/orchestrator/engine_detector.py`
**Status:** MVP ✅ implemented

---

## 1. What it does

`connect_target` is the entry point for every workflow. It:

1. **Validates** the target reference (path, workspace ID, or Desktop hint).
2. **Reconciles** any orphan plan executions left from a previous server crash.
3. **Detects** which subprocess engines are installed (`powerbi-modeling-mcp`,
   `te`, `dscmd`, `pbip-validator`, `superbi-mcp`).
4. **Creates a session** in `~/.powerbi-orchestrator-mcp/sessions/<id>.db`
   with the target, the detected engines, and a target_id.
5. **Returns** a `session_id` that subsequent tools (`plan_change`,
   `apply_plan`) use implicitly via the active session.

It never silently swallows a missing engine — every engine is reported
even when unavailable, so the agent (or you) can see what's missing.

---

## 2. Input schema

```python
{
    "target_type": "pbi_desktop" | "fabric_workspace" | "pbip_folder" | "pbix_file",
    "target_ref": "<path | workspace_id>",
    "auth_mode": "interactive" | "service_principal",   # default: interactive
    "tenant_id": "<azure-tenant-uuid>"                  # required for service_principal
}
```

**Validation rules:**

| `target_type` | `target_ref` format | Extra checks |
|---------------|---------------------|--------------|
| `pbip_folder` | absolute or relative path | Path must exist (else warning) |
| `pbix_file` | absolute or relative path | Path must exist (else warning) |
| `fabric_workspace` | workspace ID (6-64 chars, `[A-Za-z0-9-]`) | Format regex; actual workspace existence is checked later by the cloud adapter |
| `pbi_desktop` | any non-empty string | Loopback TCP check deferred to actual adapter call |

---

## 3. Output schema

```python
ConnectResult(
    session_id="<32-char hex>",
    engines_available={
        "powerbi-modeling-mcp": EngineStatus(...),
        "te": EngineStatus(...),
        "dscmd": EngineStatus(...),
        "pbip-validator": EngineStatus(...),
        "superbi-mcp": EngineStatus(...),
    },
    warnings=[<string>, ...]   # human-readable diagnostics
)

EngineStatus(
    name="<engine>",
    available=True | False,
    version="<version string>" | None,
    reason_unavailable="<why>" | None,   # present iff available=False
)
```

**`engines_available` is always populated for all 5 known engines.**
Unavailable entries have `available=False` and a non-null `reason_unavailable`.

---

## 4. Example outputs

### 4.1 Happy path: PBIP folder with all engines installed

Input:
```json
{"target_type": "pbip_folder", "target_ref": "./out/sales.pbip"}
```

Output:
```json
{
  "session_id": "5f3a2b8c9d4e1f2a3b4c5d6e7f8a9b0c",
  "engines_available": {
    "powerbi-modeling-mcp": {"available": true, "version": null},
    "te": {"available": true, "version": "3.0.0"},
    "dscmd": {"available": false, "reason_unavailable": "binary not found in PATH (tried dscmd, daxstudio)"},
    "pbip-validator": {"available": true, "version": "0.3.2"},
    "superbi-mcp": {"available": false, "reason_unavailable": "binary not found in PATH (tried superbi-mcp); for npm package 'superbi-mcp', run `npx superbi-mcp` to verify"}
  },
  "warnings": [
    "engine 'dscmd' unavailable: binary not found in PATH (tried dscmd, daxstudio)",
    "engine 'superbi-mcp' unavailable: binary not found in PATH (tried superbi-mcp); for npm package 'superbi-mcp', run `npx superbi-mcp` to verify"
  ]
}
```

### 4.2 Path doesn't exist (warning, not error)

Input:
```json
{"target_type": "pbip_folder", "target_ref": "./does/not/exist"}
```

Output:
```json
{
  "session_id": "...",
  "engines_available": {...},
  "warnings": ["pbip_folder path does not exist: ./does/not/exist"]
}
```

`connect_target` succeeds — the warning is surfaced so the agent can
prompt the user or correct the path. The session IS created (engine
detection still runs).

### 4.3 Malformed fabric_workspace ID (warning)

Input:
```json
{"target_type": "fabric_workspace", "target_ref": "with spaces"}
```

Output:
```json
{
  "session_id": "...",
  "engines_available": {...},
  "warnings": ["fabric_workspace ref should be a workspace ID, got 'with spaces'"]
}
```

### 4.4 Service principal auth without tenant_id (error, raises)

Input:
```json
{"target_type": "fabric_workspace", "target_ref": "ws-abc", "auth_mode": "service_principal"}
```

Raises:
```
ValueError: tenant_id is required for service_principal auth_mode
```

The session is NOT created. The agent must retry with `tenant_id`.

### 4.5 Invalid target_type (error, raises)

Input:
```json
{"target_type": "garbage", "target_ref": "x"}
```

Raises:
```
ValueError: target_type must be one of ['fabric_workspace', 'pbip_folder', 'pbix_file', 'pbi_desktop'], got 'garbage'
```

---

## 5. Common agent workflows

### 5.1 PBIP-first workflow (recommended)

```
[Agent]
1. connect_target(target_type="pbip_folder", target_ref=path)
   → session_id, engines_available, warnings
2. plan_change(intent="audit", options={"target": ..., "checks": [...]})
   → plan_id, plan_yaml, risk_score, estimated_changes
3. apply_plan(plan_id, dry_run=true)
   → ApplyResult{result="success", executed_steps, rollback_steps_executed}
4. apply_plan(plan_id, dry_run=false)
   → ApplyResult{result="success" | "rolled_back" | "failed"}
```

After step 4, the audit log has rows for each tool invocation; verify
with `python -m powerbi_orchestrator_mcp.orchestrator.audit verify`.

### 5.2 Fabric-first workflow (cloud target)

```
[Agent]
1. connect_target(
     target_type="fabric_workspace",
     target_ref="ws-abc-def",
     auth_mode="service_principal",
     tenant_id="00000000-1111-2222-3333-444444444444"
   )
   → session_id, engines_available, warnings
2. plan_change(intent="deploy", options={...})
   → plan_id, plan_yaml
3. apply_plan(plan_id, dry_run=false)
   → ApplyResult (real deployment to Fabric)
```

### 5.3 PBI Desktop workflow (Windows-only)

```
[Agent]
1. connect_target(target_type="pbi_desktop", target_ref="localhost")
   → session_id; engines_available may be limited (DS is Windows-only)
2. ...
```

The loopback TCP check happens in the modeling adapter when an actual
operation is invoked. `connect_target` doesn't probe Desktop at
connect time (per spec §11.1).

---

## 6. Edge cases & gotchas

| Situation | Behavior |
|-----------|----------|
| Path doesn't exist | Warning, session created anyway (so the agent can prompt the user) |
| Path has spaces | No warning for PBIP/.pbix; warning for fabric_workspace ID |
| `tenant_id` missing with SPN auth | Raises ValueError before session creation |
| No engines installed | All 5 reported as unavailable; 1 warning per missing engine |
| Stale session from previous run | Reconciles orphans before creating new session |
| Re-connecting (same target) | New session_id each time; old session row remains in `sessions/` |

---

## 7. What happens after `connect_target` succeeds

The agent receives a `session_id` and a `target_id` (computed internally
per spec §2.7). Subsequent `plan_change` / `apply_plan` calls don't
need to pass these explicitly — they're implicit via the active
session in the orchestrator.

When the server process restarts, sessions are persisted but the
in-memory `_active_session_id` is reset. To reconnect, the agent must
call `connect_target` again. v4 will add `use_session=<session_id>` to
restore context across restarts.

---

## 8. Errors that `connect_target` raises

| Error | Cause |
|-------|-------|
| `ValueError("target_type must be one of ...")` | Invalid `target_type` |
| `ValueError("auth_mode must be one of ...")` | Invalid `auth_mode` |
| `ValueError("tenant_id is required ...")` | SPN auth without tenant |

Warnings (NOT errors) appear in `ConnectResult.warnings`:
- Engine missing (binary not in PATH).
- PBIP/.pbix path doesn't exist.
- Fabric workspace ID format looks wrong.
- (PBI Desktop connectivity is NOT checked here — see §5.3.)

---

## 9. Related docs

- [`specs/01-orchestrator.md` §3.1](../specs/01-orchestrator.md) — formal input/output schema
- [`specs/05-engines-adapters.md` §11](../specs/05-engines-adapters.md) — per-target-type behavior matrix
- [`docs/MVP-STATUS.md`](./MVP-STATUS.md) — implementation status
- [`src/orchestrator/server.py`](../src/powerbi_orchestrator_mcp/orchestrator/server.py) — implementation
- [`src/orchestrator/engine_detector.py`](../src/powerbi_orchestrator_mcp/orchestrator/engine_detector.py) — engine probe logic

---

## 10. Test coverage

| Module | Coverage | Test file |
|--------|----------|-----------|
| `src/orchestrator/server.py` | 92% | `tests/unit/test_server.py::TestConnectTarget` |
| `src/orchestrator/engine_detector.py` | 80% | `tests/unit/test_engine_and_executor.py::TestDetectAll` + `TestDetectSingle` |

Coverage of edge cases (path missing, malformed workspace ID, SPN
without tenant, all engines missing) is included in the test suite.
