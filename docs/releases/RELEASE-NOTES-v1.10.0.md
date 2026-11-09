# Release Notes — v1.10.0 (2026-09-24)

> **Minor release — adds opt-in HTTP transport + dependency on PyJWT.**
> Backward-compatible: stdio (the default) is unchanged. Anyone who
> doesn't pass `--transport http` sees zero behavior difference vs
> v1.9.x. The only externally visible default-change is a new
> optional dependency (`pyjwt[crypto]>=2.8`) and a `project_urls`
> sidebar entry on the PyPI project page.

**Status:** Beta. New: 1 transport, 1 module, 1 dep. Removed: none.
Migration from v1.9.x: `pip install --upgrade powerbi-orchestrator-mcp`
and you're on v1.10.0. No data migration, no schema changes, no
audit-log format changes.

---

## What's new

### 1. HTTP transport (opt-in) — Closes Capa 0

The orchestrator now speaks Streamable HTTP (MCP spec 2025-06+) in
addition to stdio. SSE is intentionally **not** supported (deprecated
by the spec). Auth uses Entra ID (Azure AD) JWT bearer tokens,
validated via PyJWT + JWKS.

```bash
# stdio (default, unchanged)
powerbi-orchestrator-mcp --start

# HTTP (new)
powerbi-orchestrator-mcp --transport http \
    --http-entra-tenant-id abc-1234 \
    --http-entra-audience api://powerbi-orchestrator-mcp
```

Or via env vars: `PBI_TRANSPORT=http`, `PBI_ENTRA_TENANT_ID=...`,
`PBI_ENTRA_AUDIENCE=...`. See `--help` for the full list.

**Scope hierarchy (RBAC):**
- `Tools.Read` — read-only tools (connect_target, powerbi_health,
  audit_model_and_report)
- `Tools.Write` — mutating tools (apply_plan, deploy_to_workspace,
  run_refresh)
- `Tools.Admin` — administrative ops; implicitly grants lower scopes

Tokens without `Tools.Read` (or `Tools.Admin`) get 403. Tokens with
the wrong audience, wrong issuer, or expired signature get 401.

**Limitations** (documented for the next minor):
- The validation function (`orchestrator/transport.py:auth_middleware_factory`)
  is exposed and tested but **not yet wired into FastMCP's request
  lifecycle**. Custom deployment adapters (e.g. ASGI app behind
  Azure API Management) can call it directly. The wiring to
  `mcp.run(transport="streamable-http")` for unauthenticated-request
  rejection lands in v1.11.
- No end-to-end test against a real Entra tenant yet (would require
  Azure tenant provisioning).

Design: `specs/architecture/07-http-transport.md`.

### 2. `pyjwt[crypto]>=2.8` dependency

Required by the HTTP transport auth. Adds ~50KB to install size.
Already pulled in transitively by `msal` for the cloud path, but now
declared explicitly so users on `--transport http` get it without
manual install.

### 3. PyPI `project_urls` sidebar

The project page on https://pypi.org/project/powerbi-orchestrator-mcp/
now shows links to Homepage, Repository, Issues, Changelog, and
Releases. Cosmetic but improves discoverability.

### 4. Release infrastructure (not user-facing)

Two new GitHub Actions workflows ship in this release. **They don't
affect `pip install` users directly** — they're for the maintainer's
release process:

- **`.github/workflows/publish.yml`** — automated publish on tag push
  (or manual dispatch). Builds wheel + sdist, gates on TestPyPI
  smoke install, then uploads to PyPI under a `pypi-production`
  environment with required reviewers. Secrets required (not yet
  configured): `PYPI_API_TOKEN`, `TEST_PYPI_API_TOKEN`.
- **`.github/workflows/e2e-nightly.yml`** — nightly + on-PR E2E
  tests with real engine binaries (`te`, `dscmd`, `pbip-validator`),
  pinned by SHA256. SHA256 placeholders until first real run;
  workflow self-skips downloads while placeholders are in place so
  PRs aren't blocked.

This release itself was **not** done via the new workflow (secrets
not configured yet) — it followed the v1.9.x manual `twine upload`
pattern. Future releases should use `publish.yml`.

### 5. Specs audit v0.3 (docs only)

Four cross-cutting specs landed as documentation (see
`specs/README.md`):

- `specs/release/supersede-policy.md` — when to yank vs patch vs
  leave on PyPI.
- `specs/ci/publish-workflow.md` — the publish workflow design above.
- `specs/qa/e2e-testing-strategy.md` — the nightly E2E design above.
- `specs/architecture/07-http-transport.md` — the HTTP transport
  design above.

Plus drift fix: README claim of "26 tools" corrected to "27" in 5
places (consistent in with the actual count after `powerbi_health`
landed in v1.9.0).

---

## What's NOT in v1.10.0

- ❌ Trusted Publishing OIDC (planned for a future minor once
  `publish.yml` has been validated by a release or two).
- ❌ Real binary SHA256 pinning for the E2E nightly (placeholders in
  place; first real run is a separate PR).
- ❌ HTTP middleware wired into FastMCP's request lifecycle (the
  validation function is exported and tested; the wiring to
  `mcp.run()` lands in v1.11).
- ❌ `docs/http-transport.md` user guide with curl examples (will
  follow v1.11).
- ❌ CONTRIBUTING.md rewrite to reflect the new `publish.yml`
  workflow (deferred — current text still mentions "GH Actions" but
  defaults to manual; rewrite follows once the workflow is in real
  use).

---

## Upgrade

```bash
pip install --upgrade powerbi-orchestrator-mcp
# verify
pip show powerbi-orchestrator-mcp | grep Version
# -> Version: 1.10.0
```

For development installs (editable):

```bash
pip install -e ".[dev]"
# version comes from pyproject.toml, now 1.10.0
```

No data migration. No audit-log format changes. The wheel content
modulo the new module + version bump is identical to v1.9.1 for
stdio users.

### Trying the HTTP transport

```bash
# After upgrade:
pip show pyjwt[crypto]  # confirms new dep installed

# Minimal HTTP setup (requires an Entra tenant + app registration):
export PBI_ENTRA_TENANT_ID="your-tenant-guid"
export PBI_ENTRA_AUDIENCE="api://powerbi-orchestrator-mcp"
export PBI_ENTRA_REQUIRED_SCOPE="Tools.Read"   # or .Write / .Admin
powerbi-orchestrator-mcp --transport http --http-host 127.0.0.1 --http-port 8000
```

Then connect with MCP Inspector or any MCP 2025-06+ HTTP client. The
token must include `aud=api://powerbi-orchestrator-mcp` and the
configured scope. See `specs/architecture/07-http-transport.md` for
the full threat model.

---

## Verification

| Check | Result |
|---|---|
| `ruff check src/ tests/e2e/ tests/unit/test_http_transport.py` | ✅ 0 errors |
| `mypy --strict src/powerbi_orchestrator_mcp` | ✅ 0 errors (69 source files) |
| `pytest tests/ -q` | ✅ 903 passed, 11 skipped |
| `python scripts/verify_mcp_server.py` | ✅ 27 tools exposed over stdio |
| `python -m build` | ✅ wheel + sdist produced |
| `twine check dist/*` | ✅ PASSED |
| `twine upload dist/*` | ✅ Uploaded to https://upload.pypi.org/legacy/ |
| Fresh venv `pip install --no-cache-dir powerbi-orchestrator-mcp==1.10.0` | ✅ Installs cleanly, both console scripts on PATH |

---

## Files changed (vs v1.9.1)

- `pyproject.toml` — version 1.9.1 → 1.10.0; add `pyjwt[crypto]>=2.8`
  dep; add `[project.urls]` block.
- `src/powerbi_orchestrator_mcp/orchestrator/transport.py` — **new**,
  HTTP transport + Entra ID JWT validation.
- `src/powerbi_orchestrator_mcp/orchestrator/server.py` — `main()`
  dispatches on `--transport`; stdio path unchanged.
- `tests/unit/test_http_transport.py` — **new**, 21 tests for the
  transport module.
- `.github/workflows/publish.yml` — **new**, release automation.
- `.github/workflows/e2e-nightly.yml` — **new**, nightly E2E.
- `tests/e2e/` — **new**, E2E test scaffold.
- `README.md` — drift fix "26 → 27 tools" + added `pyjwt[crypto]`
  rationale.
- `CHANGELOG.md` — new `[1.10.0]` entry at top.
- `RELEASE-NOTES-v1.10.0.md` — this file.

Total: ~1,600 new lines across 10 files. No `src/` API changes for
stdio users.

---

## Why minor (not patch)?

semver-wise: new backward-compatible functionality (HTTP transport
opt-in) + new dependency = minor bump. Patch would imply no new
external API, which is wrong here. The project's own
`CONTRIBUTING.md` §"Release process" defines:

> Minor (`1.8.0` → `1.9.0`): new tool or backward-compatible feature.

This release adds a backward-compatible feature (HTTP transport) and
a new CLI flag, so minor is correct.

---

## Attribution

Same as v1.9.0. New third-party dep:
- [`PyJWT`](https://pyjwt.readthedocs.io/) — MIT, by jpadilla.

No new engine integrations, no new tool surface area, no breaking
changes to the existing 27 MCP tools.