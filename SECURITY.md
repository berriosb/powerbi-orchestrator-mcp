# Security Policy

> The `powerbi-orchestrator-mcp` project takes security seriously. This
> document describes how to report vulnerabilities, what we do about them,
> and our threat model.

---

## Supported versions

| Version | Supported           |
|---------|---------------------|
| 1.19.x  | ✅ active           |
| 1.18.x  | ✅ critical security fixes |
| 1.17.x  | ✅ critical security fixes |
| < 1.17  | ❌ upgrade required |

We follow semver. Patch releases get critical security fixes; the current
minor receives full backports for one cycle.

---

## Reporting a vulnerability

**Please do not open a public GitHub issue for security problems.**

Report privately via:

- **GitHub Security Advisories** (preferred):
  [https://github.com/berriosb/powerbi-orchestrator-mcp/security/advisories/new](https://github.com/berriosb/powerbi-orchestrator-mcp/security/advisories/new)
- **Email**: security@berriosb.dev (PGP key on request)

Include in your report:

1. **Description** of the vulnerability and its impact.
2. **Reproduction steps** (a minimal PBIP + MCP call is ideal).
3. **Affected versions** (test against `main` + the latest release).
4. **Your coordinate / / timeline expectations**.
5. Whether you want public credit in the advisory.

We aim to:

- **Acknowledge** within **48 hours**.
- **Triage** within **5 business days**.
- **Patch** critical issues within **7 days**; others within **30 days**.

---

## Threat model

### What the orchestrator handles

- Power BI / Fabric credentials (Azure AD, SPN, MSI).
- Power BI workspace IDs, dataset IDs, report IDs.
- PBIP / PBIR / TMDL files on local disk.
- DAX queries (executed against user-supplied datasets).
- HTTP calls to `api.fabric.microsoft.com` and `api.powerbi.com`.

### What the orchestrator **does not** do

- It **does not accept credentials as tool arguments**. Secrets are read from
  the environment (`PBI_CLIENT_ID` / `PBI_TENANT_ID` /
  `PBI_CLIENT_SECRET`, or the `AZURE_*` equivalents) or from a workload
  identity by `azure-identity`; no MCP tool signature takes a secret, so
  credentials never enter the JSON-RPC payload, the model context, or
  client logs.
- It **does not load plugins unless explicitly enabled**. Plugin execution
  requires `PBI_ENABLE_PLUGINS`, and even then the plugin directory must be
  writable only by the owner and contain no symlinks.
- It **does not let one principal run another's plan**. Plans record the
  identity that created them and lookups are scoped to the caller.
- It **does not let one principal use another's HTTP session**. The MCP
  session is bound to the credential that opened it: a request carrying a
  different credential is rejected with 404, and the per-tool scope check
  runs against the claims of the current call.
- It **does not log raw DAX outputs or PII unless** the caller opts into a
  higher `max_rows`; audit payloads pass through `CloudAuditLog`, which
  redacts bearer tokens, connection strings and email addresses before
  persisting, replaces any value stored under a key that names a secret,
  and tool arguments are stored as a SHA-256 hash.
- It **does not execute DAX locally** (queries go to the dataset via
  Power BI Service REST).
- It **does not write outside its workspace root**. Write tools reject paths
  outside `PBI_WORKSPACE_ROOT`, which defaults to the process working
  directory, and additionally refuse system and credential directories.
- It **does not let an identifier change the API path it is sent to**.
  Workspace, dataset, item and datasource identifiers are validated before
  they are interpolated into a Fabric REST path.

### Out-of-scope

- Vulnerabilities in upstream Microsoft Power BI REST API behavior.
- Vulnerabilities in the user's MCP client (Claude Desktop, VS Code, etc.).
- Vulnerabilities in user-supplied PBIP files (the orchestrator trusts
  the input PBIP; malformed JSON fails loudly).
- Vulnerabilities in subprocess engines (`powerbi-modeling-mcp`,
  `superbi-mcp`, `te`) — report to those projects.

---

## Security features

| Feature | Where |
|---|---|
| Token-bucket rate limiting per tenant | `cloud/fabric_client.py` |
| Circuit breaker (opens after 5 consecutive 5xx) | `cloud/fabric_client.py` |
| Least-privilege token scopes (read vs write) | `cloud/fabric_client.py`, `cloud/auth.py` |
| HMAC-chained audit log (tamper-evident) | `orchestrator/audit.py` |
| Truncation detection via out-of-DB chain anchor | `orchestrator/audit.py` |
| Owner-only permissions on plan/session/audit stores (POSIX hosts) | `orchestrator/paths.py` |
| PII / token redaction in audit log (by pattern and by key) | `cloud/audit_cloud.py` (wired via `orchestrator/audit_guard.py`) |
| Audit coverage for state-changing tools | `orchestrator/audit_guard.py` |
| Elicitation before writes (configurable) | `orchestrator/elicitation.py` |
| Write-path containment (`PBI_WORKSPACE_ROOT`) | `validation/path_safety.py` |
| Per-tool scope enforcement (HTTP transport) | `orchestrator/authorization.py` |
| HTTP session bound to the calling credential | `orchestrator/transport.py` |
| Host allowlist for the HTTP transport (DNS-rebinding protection) | `orchestrator/transport.py`, `orchestrator/server.py` |
| REST path identifier validation | `cloud/fabric_client.py` |
| Plan ownership (object-level authorization) | `orchestrator/plan_store.py` |
| Symlink refusal when reading repo files | `tools/sync_git_to_workspace.py` |
| Opt-in, owner-only plugin loading | `plugins.py` |
| `dry_run=True` default on cloud write tools | `tools/deploy_to_workspace.py`, `tools/run_refresh.py` |
| Row cap on DAX results (`max_rows`) | `orchestrator/server.py` |
| Atomic file writes (temp + rename) | `tools/edit_report_visual.py`, `tools/edit_report_visual.py` |
| Cross-engine rollback, with the outcome reported | `orchestrator/rollback.py` |

### Not implemented

Stated explicitly so nobody assumes a control that is not there:

- Plan ownership is **per-process state in a local SQLite file**, not an
  enterprise identity provider. It binds plans to the claims presented at
  creation time; it does not re-validate that those claims are still valid.

- There is **no `--readonly` flag**. Write tools are made safe by
  `dry_run=True` defaults and per-tool scopes, not by a global mode switch.
- There is **no code-signing or signature check for plugins**. Plugin loading
  is opt-in via `PBI_ENABLE_PLUGINS`, refuses directories that are group- or
  world-writable, and refuses symlinked plugin files, but a plugin that an
  operator deliberately enables runs with full interpreter access.
- The HMAC chain is **tamper-evident, not tamper-proof**: an attacker with
  write access to the audit directory can rewrite both the database and the
  anchor file. Ship the audit directory on append-only storage if you need
  resistance against a host-level attacker. When `PBI_ORCHESTRATOR_AUDIT_SECRET`
  is unset the HMAC key is generated next to the database, so it does not
  protect against an attacker who can write to that directory — set the
  variable to a secret held outside the audit directory.
- The audit log is **fail-open**: if a row cannot be written, the tool call
  still goes ahead and the failure is surfaced as a warning in the result.
  The audit log is for forensics, not for access control.
- The HTTP transport's **Host allowlist** follows the bind address, so a
  server bound to a routable address answers requests for that address.
  DNS-rebinding protection stays enabled; list the exact names you serve
  with `--http-allowed-host` / `PBI_HTTP_ALLOWED_HOSTS`.

---

## Sensitive data handling

### What we never log

- Bearer tokens (redacted via `Bearer XXXX...`).
- Connection strings (`Server=...;Password=...` → `[REDACTED]`).
- Any value stored under a key that names a secret (`password`,
  `client_secret`, `api_key`, `token`, `authorization`, …) — replaced whole,
  because a short secret has no pattern to match.
- Admin emails (SHA-256 hashed via `set_sensitivity_labels`).

### What we log

- Tool name, with the operation recorded as `cloud:<tool>`.
- A SHA-256 hash of the tool arguments — never the arguments themselves.
- Result status (`success` / `failed` / `partial` / `dry_run` / `rolled_back`).
- Known result identifiers (`item_id`, `dataset_id`, `refresh_id`,
  `execution_id`, `plan_id`, `session_id`).
- Target ID (workspace ID, dataset ID, report ID).
- Raw error messages are **not** persisted: only the exception type is
  recorded, and API response bodies are truncated before they can reach the
  model context.

Audit log entries are HMAC-chained — each row signs the previous row's
hash, so a tampered entry breaks the chain and surfaces in verification.
A separate anchor file records the expected tail of the chain, so deleting
the most recent rows is detected as truncation instead of verifying clean.

Plan, session and audit stores are created with owner-only permissions
(`0o700` directories, `0o600` databases) on POSIX hosts. On Windows,
`chmod` only toggles the read-only attribute, so restrict access with NTFS
ACLs instead.

---

## Dependencies

We pin all dependencies in `pyproject.toml` with upper bounds
(e.g. `mcp[cli]>=1.0.0,<2.0.0`) so a breaking change upstream can't
silently land, and `uv.lock` pins exact versions for reproducible installs.

Automated supply-chain monitoring:

- **Dependabot** (`.github/dependabot.yml`).
- **pip-audit** (`--strict`) runs in CI and is available as a dev extra.

If you find a vulnerability in a transitive dependency, please report
privately as above so we can coordinate disclosure.

---

## Threat-model assumptions

The orchestrator assumes:

1. The **MCP client** is trusted (the user already runs Claude Desktop
   or VS Code with full file + network access).
2. The **Azure AD tenant** is correctly configured by the user (least
   privilege scopes, MFA, etc.).
3. The **PBIP** files come from a trusted source (the user / org's Git
   repo). Malicious PBIP files can contain arbitrary DAX that runs
   against the user's workspace.
4. The **subprocess engines** (`powerbi-modeling-mcp`, `superbi-mcp`, `te`)
   are trusted local binaries. npm-sourced engines are pinned to a specific
   version in `engines/modeling_mcp.py`; install and vet them yourself rather
   than trusting whatever is on `PATH`.

If any of these assumptions break, the orchestrator is not the right
defense layer.

---

## Hall of fame

We thank the following reporters (with permission):

- _(None yet — be the first!)_

---

## Contact

For anything not covered above: open a discussion at
[https://github.com/berriosb/powerbi-orchestrator-mcp/discussions](https://github.com/berriosb/powerbi-orchestrator-mcp/discussions).