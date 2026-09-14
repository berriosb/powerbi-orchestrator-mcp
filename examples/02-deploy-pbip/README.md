# Example 02 — Deploy a PBIP to a Fabric Workspace

> End-to-end deploy: pre-deploy gate → publish PBIP → configure refresh
> schedule → trigger initial refresh. Each step's success is surfaced
> independently so you can debug partial failures.

## What this demonstrates

- `pre_deploy_check` — gate findings before publish.
- `deploy_to_workspace` — publish + schedule + initial refresh.
- `run_refresh` — explicit refresh trigger (separate from deploy).

## Prerequisites

1. A Fabric workspace with an admin or member SPN.
2. Azure AD app registration with `Dataset.ReadWrite.All` scope.
3. The orchestrator configured with auth:
   ```bash
   export PBI_AUTH_MODE=service_principal
   export PBI_TENANT_ID=<your-tenant>
   export PBI_CLIENT_ID=<your-spn-id>
   export PBI_CLIENT_SECRET=<your-spn-secret>
   ```
4. A PBIP folder ready to publish.

## Walkthrough

### Step 1 — Pre-flight: confirm findings are below gate

First, audit the model to generate findings:

```
audit_model_and_report(pbip_path="/path/to/sales.pbip")
```

Pass the findings to the gate:

```
pre_deploy_check(
    findings_json='<paste from previous step>',
    profile="standard"
)
```

If the gate passes (`passed: true`), proceed to deploy.

### Step 2 — Deploy

```
deploy_to_workspace(
    pbip_path="/path/to/sales.pbip",
    workspace_id="<your-workspace-id>",
    refresh_daily_hour=6,        # 0-23 UTC
    findings_json='<paste from audit>',
    gate_profile="standard",
    auth_mode="service_principal",
    tenant_id="<your-tenant>",
    client_id="<your-spn-id>",
    client_secret="<your-spn-secret>",
    mock=false                    # set true for a dry run
)
```

Returns:

```json
{
  "gate_result": {
    "passed": true,
    "profile_name": "standard",
    "failed_checks": []
  },
  "publish_ok": true,
  "item_id": "abcd-1234-...",
  "schedule_ok": true,
  "refresh_id": "refresh-5678",
  "dataset_id": "sales",
  "errors": []
}
```

Each step is independent — if `publish_ok=false`, the schedule + refresh
still run, and the failure surfaces in `errors`:

```json
{
  "publish_ok": false,
  "errors": ["publish_failed: 403 forbidden"],
  "schedule_ok": true,
  "refresh_id": "refresh-5678"
}
```

### Step 3 — Explicit refresh (optional)

If you want to trigger a refresh separately (e.g. from a CI pipeline
on merge to `main`):

```
run_refresh(
    workspace_id="<your-workspace-id>",
    dataset_id="sales",
    refresh_type="full",
    wait=true,
    timeout_s=1800,
    auth_mode="service_principal",
    tenant_id="<your-tenant>",
    client_id="<your-spn-id>",
    client_secret="<your-spn-secret>"
)
```

Returns the refresh status + history.

## What can go wrong

- **`publish_failed: 403 forbidden`** — SPN isn't a workspace member.
  See [troubleshooting](../docs/troubleshooting.md#deploy_to_workspace-publish_failed-403-forbidden).
- **`schedule_failed: 400 bad request`** — usually a malformed refresh
  schedule body; check the workspace allows scheduled refresh.
- **Refresh never completes** — likely a gateway issue (the dataset
  points to on-prem sources). Verify the gateway mapping in Power BI
  Service.

## Mock mode

For CI / testing without a real workspace:

```
deploy_to_workspace(
    pbip_path="/path/to/test.pbip",
    workspace_id="ws-fake",
    mock=true
)
```

Skips all cloud calls and returns synthetic success.

## Files

```
examples/02-deploy-pbip/
├── README.md
└── expected-output.md
```