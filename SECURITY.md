# Security Policy

> The `powerbi-orchestrator-mcp` project takes security seriously. This
> document describes how to report vulnerabilities, what we do about them,
> and our threat model.

---

## Supported versions

| Version | Supported           |
|---------|---------------------|
| 1.8.x   | ✅ active           |
| 1.7.x   | ✅ critical fixes only |
| 1.6.x   | ⚠️ EOL — please upgrade |
| < 1.6   | ❌ unsupported       |

We follow semver. Patch releases (1.8.x) get critical security fixes;
minor releases (1.x) get full backports for one cycle.

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

- It **does not** store credentials on disk. `DefaultAzureCredential`
  is the only auth flow; tokens come from the Azure SDK / `az` CLI /
  managed-identity endpoint.
- It **does not** log raw DAX outputs or PII unless `audit_cloud`
  explicitly tags them — and it redacts Bearer tokens, connection
  strings, and emails by default (see `cloud/audit_cloud.py`).
- It **does not** execute DAX locally (queries go to the dataset via
  Power BI Service REST).

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
| HMAC-chained audit log (tamper-evident) | `orchestrator/audit.py` |
| PII / token redaction in audit log | `cloud/audit_cloud.py` |
| Elicitation before writes (configurable) | `orchestrator/elicitation.py` |
| `--readonly` flag to disable all write tools | `orchestrator/server.py` (planned for v1.9.0) |
| Atomic file writes (temp + rename) | `tools/edit_report_visual.py`, `tools/edit_report_visual.py` |
| Cross-engine rollback | `orchestrator/rollback.py` |

---

## Sensitive data handling

### What we never log

- Bearer tokens (redacted via `Bearer XXXX...`).
- Connection strings (`Server=...;Password=...` → `[REDACTED]`).
- Admin emails (SHA-256 hashed via `set_sensitivity_labels`).

### What we log

- Tool name + args (no secrets).
- Result status (`success` / `failed` / `rolled_back`).
- Duration.
- Plan execution ID (UUID).
- Target ID (workspace ID + dataset ID + report ID).

Audit log entries are HMAC-chained — each row signs the previous row's
hash, so a tampered entry breaks the chain and surfaces in verification.

---

## Dependencies

We pin all dependencies in `pyproject.toml` with upper bounds
(e.g. `mcp[cli]>=1.0.0,<2.0.0`) so a breaking change upstream can't
silently land.

Automated supply-chain monitoring:

- **Dependabot** (planned — see roadmap).
- **pip-audit** in CI (planned).

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
4. The **subprocess engines** (`powerbi-modeling-mcp`, `te`, etc.) are
   pinned to specific versions via `engines/versions.py`.

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