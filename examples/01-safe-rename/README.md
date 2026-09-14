# Example 01 — Safe Rename

> The showpiece workflow: rename a column / measure / table across the
> model + DAX expressions + M queries + report bindings, with atomic
> rollback on failure.

## What this demonstrates

- `connect_target` — open a session against a PBIP.
- `plan_change(safe_rename)` — generate a versionable plan.
- `apply_plan` — execute with checkpoints + rollback.
- Cross-engine propagation (model + DAX + report bindings).
- `audit_model_and_report` — verify the change didn't break anything.

## Prerequisites

1. A Power BI project folder (`.pbip`) on disk. We'll create a synthetic
   one in step 1.
2. The orchestrator installed (`pip install -e .`).
3. An MCP client connected (Claude Desktop, VS Code, Cursor, etc.).

## Walkthrough

### Step 1 — Synthesize a PBIP

We use the `tests/fixtures/sample.pbip` fixture as a target (4 tables,
~15 measures, 4 visuals). Copy it to a writable location:

```bash
cp -r tests/fixtures/sample.pbip /tmp/safe-rename-demo.pbip
```

Or generate your own with Power BI Desktop (Save As → `.pbip`).

### Step 2 — Connect

In your MCP client:

```
connect_target(
    target_type="pbip_folder",
    target_ref="/tmp/safe-rename-demo.pbip"
)
```

Returns:

```json
{
  "session_id": "ses-abc123",
  "engines_available": {
    "python_report": { "available": true },
    "powerbi-modeling-mcp": { "available": true }
  },
  "warnings": []
}
```

### Step 3 — Plan the rename

```
plan_change(
    intent="safe_rename",
    options={
        "old_path": "FactSales[UnitPrice]",
        "new_path": "FactSales[Price]",
        "scope": "full"
    }
)
```

Returns a `PlanResult` with:
- `plan_id` (UUID)
- `plan_yaml` (the versionable plan — commit this to Git)
- `steps` (3 cross-engine steps: model rename + DAX propagation + report bindings)
- `risk_score` (0.0–1.0)
- `estimated_changes` (files affected, measures affected, visuals affected)

### Step 4 — Apply the plan

```
apply_plan(
    plan_id="<from plan_change>",
    dry_run=false,
    confirm_each_step=false
)
```

Returns:

```json
{
  "result": "success",
  "executed_steps": [
    {"id": "step-1", "engine": "modeling", "action": "rename_column", "success": true},
    {"id": "step-2", "engine": "modeling", "action": "propagate_dax", "success": true},
    {"id": "step-3", "engine": "report", "action": "update_bindings", "success": true}
  ],
  "rollback_handle": null,
  "artifacts_changed": [
    "sample.Dataset/definition/tables/FactSales.tmdl",
    "sample.Dataset/definition/expressions.tmdl",
    "sample.Report/pages/Overview/page.json"
  ]
}
```

### Step 5 — Verify

```
audit_model_and_report(
    pbip_path="/tmp/safe-rename-demo.pbip"
)
```

Confirm:
- BPA score didn't regress.
- DAX linter doesn't flag the new column name in any measure.
- WCAG score unaffected.
- Data dictionary reflects the rename.

### Step 6 — Rollback (if needed)

If anything looks wrong, the `RollbackEngine` from `apply_plan` keeps
an HMAC-chained snapshot. To re-run with rollback, use:

```
apply_plan(
    plan_id="<original>",
    dry_run=false  # the same plan, run again → reverts to pre-state
)
```

…or restore from the explicit `rollback_handle` you got from step 4.

## What can go wrong

- **`unitprice` is referenced by 50 measures** → estimated_changes will
  say so; consider splitting into 2-3 smaller renames.
- **The column is in a relationship** → `setup_rls_and_roles` may need
  to re-derive its filter expressions; review the plan's `risk_score`.
- **You don't have `powerbi-modeling-mcp` installed** → orchestrator
  falls back to `te` (or returns `engine_not_found`). See
  [`docs/troubleshooting.md`](../docs/troubleshooting.md#engines-subprocess-mcps).

## What to learn from this example

- Plans are **versionable** (the `plan_yaml` can be committed to Git).
- Rollback is **atomic** across engine boundaries.
- Dry-run is the safe way to preview: `apply_plan(..., dry_run=true)`.

## Related tools

- `diff_models` — compare pre/post states.
- `pre_deploy_check` — gate the rename before deploying.
- `audit_model_and_report` — confirm no regressions.
- `generate_data_dictionary` — keep docs in sync.

## Files

```
examples/01-safe-rename/
├── README.md       # this file
└── expected-output.md  # sample tool outputs for the synthetic PBIP
```