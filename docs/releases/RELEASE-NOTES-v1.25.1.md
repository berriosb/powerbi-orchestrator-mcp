# Release Notes — v1.25.1

**Release date:** 2026-11-13

Patch release delivering enterprise-readiness security, HTTP operability, audit-backup integrity, and adapter test-coverage hardening on top of `v1.25.0`.

## Security

- **RBAC & Audit Coverage (`orchestrator/server.py`).**
  - Enforced `require_capability_scope(SCOPE_WRITE)` inside `screenshot_report_pages` in addition to `PBI_READONLY=1`, preventing `Tools.Read` callers from triggering rendered disk artifacts over HTTP.
  - Added `@audited("plan_change")` so plan persistence is recorded in the HMAC-chained audit log (`cloud:plan_change`).
- **Hardened Email & Microsoft Graph Delivery (`tools/send_report_email.py`).**
  - Restricted `_SIMPLE_EMAIL_RE` / `_extract_domain` against path-routing characters (`/`, `?`, `#`, `%`, `..`), added CR/LF header-injection blocking on `subject`, and URL-encoded `sender` in the Graph `sendMail` endpoint.
  - Offloaded `DefaultAzureCredential().get_token()` to a worker thread (`asyncio.to_thread`).
  - Added implicit TLS support (`smtplib.SMTP_SSL` on port `465` or `PBI_SMTP_SSL=1`) and refused silent plaintext SMTP fallback when `STARTTLS` is absent (unless `PBI_SMTP_ALLOW_INSECURE=1`).
- **Fail-Closed Scheduled Runner Audit (`orchestrator/report_runner.py`).**
  - Records a write-ahead `"attempted"` entry before `send_report_email` and propagates audit write errors when `PBI_AUDIT_FAIL_CLOSED=1` or `PBI_STRICT_COMPLIANCE=1`.
- **WAL-Safe Audit Export & Rotation Lock (`orchestrator/iso_handoff.py`).**
  - Switched live SQLite copying in `export_audit_bundle` to `sqlite3.Connection.backup()`, checkpoints WAL (`PRAGMA wal_checkpoint(TRUNCATE)`), and serializes `--rotate` behind `threading.Lock` + `fcntl.flock`.

## Operations & Reliability

- **Unauthenticated HTTP Health Probes (`orchestrator/server.py`).**
  - Added `build_http_app` serving `GET`/`HEAD /healthz` and `/readyz` (`200` ready / `503` with failed check names only, no paths or secrets) before `EntraAuthMiddleware`.
- **Credential-Scoped Shared Rate Limiter & Circuit Breaker (`cloud/auth.py`, `cloud/fabric_client.py`).**
  - Shared `TokenBucket` and `CircuitBreaker` instances across `FabricClient` instances keyed by `(mode, tenant_id, client_id)`.
- **Session Store TTL Pruning (`orchestrator/context.py`).**
  - Added `SessionStore.prune_expired()` (`PBI_SESSION_TTL_HOURS`, default `168` h) to clean up expired `.db` / `-wal` / `-shm` session files.
- **Docs & Container Alignment (`Dockerfile`, `DOCKER.md`, `docker-compose.yml`, `SECURITY.md`).**
  - Updated container image tags/labels to `1.25.1`, added `EXPOSE 8000`, and updated `SECURITY.md` supported versions, shared resilience, HTTP health probes, and `--readonly` documentation.

## Verification

- `ruff check src tests scripts`, `ruff format --check src tests scripts`, `mypy src tests scripts`
- `uv lock --check`, `pip-audit --strict --skip-editable .`
- `pytest tests/ --cov=powerbi_orchestrator_mcp --cov-fail-under=80` (1704 passed, 4 Windows-only TE skipped; 89.05% coverage)
- `python scripts/verify_mcp_server.py` (38 tools verified) + wheel/sdist `twine check`
