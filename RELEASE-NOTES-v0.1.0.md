# Release Notes — v0.1.0 (2026-08-26)

> First tagged release of `powerbi-orchestrator-mcp`. Ships the
> MVP foundation: orchestrator + 3 working tools + 2 engine adapters +
> comprehensive spec coverage + 374 tests passing.

**Status:** Alpha (pre-Word-2). Suitable for development and testing
against mock engines. **Not yet production-ready** — see Known Limitations.

---

## What's included

### Orchestrator (Layer 6) — fully implemented

| Tool | Status | Notes |
|------|--------|-------|
| `connect_target` | ✅ real | Detects engines, creates session, persists to SQLite |
| `plan_change` | ✅ real | 4 MVP templates (safe_rename, audit, deploy, dax_regression) |
| `apply_plan` | ✅ real | Heartbeat tracking, rollback, audit log entry |

**Foundation modules:**
- `identifiers.py` — canonical formats (session_id, plan_id, target_id, ...)
- `plan_executions.py` — 6-state machine + crash recovery (orphan reconciliation on boot)
- `plan_models.py` — Pydantic models shared by planner + rollback
- `planner.py` — `PlanBuilder` with 4 MVP templates
- `rollback.py` — `RollbackEngine` with cross-engine dispatcher support
- `audit.py` — SQLite + HMAC-chained log + `audit verify` CLI
- `elicitation.py` — MCP 2025-06-18 wrapper with rate limiting
- `context.py` — SessionContext with SQLite WAL persistence
- `engine_detector.py` — discovers 5 subprocess engines in parallel
- `step_executor.py` — registry + DryRun/MissingEngine fallback

### Engine adapters (Layer 1+2)

| Adapter | Status | Coverage | Notes |
|---------|--------|----------|-------|
| `python_report` | ✅ built-in | 78% | Cross-platform PBIR fallback, always available |
| `powerbi-modeling-mcp` | ✅ wired | 99% | MCP JSON-RPC adapter, mock-tested |
| `superbi_mcp` | ✅ wired | 90% | Second-tier ReportEngine, page CRUD delegates to python_report for MVP coverage |
| `te` | ⏳ Week 2 | — | Modeling fallback (not yet implemented) |
| `dscmd` | ⏳ Week 2 | — | DAX trace (Windows only) |
| `pbip-validator` | ⏳ Week 2 | — | Validation CLI |

### Engines contracts (Layer 1+2 cross-cutting)

| Module | Coverage | Notes |
|--------|----------|-------|
| `engines/errors.py` | 100% | 8-class hierarchy, code + remediation_hint on every error |
| `engines/timeouts.py` | 95% | Per-engine timeouts with env var overrides |
| `engines/exit_codes.py` | 100% | Canonical exit code → EngineError mapping |
| `engines/base.py` | 55% | Protocols + JsonRpcSubprocessEngine base class |
| `engines/selector.py` | 91% | EngineSelector with dual modeling/report registries |

### Specifications — 100% closed for MVP scope

- 13 spec documents in `specs/` + `docs/`
- 5 Tier-A specs (engine contracts, identifiers, crash recovery, pbip-validator, connect_target matrix)
- 4 Tier-B specs (elicitation outcomes, audit rotation, cloud audit, concurrency limits)
- 2 Tier-C specs (plan YAML versioning, fixture PBIP specification)
- 9 v2/v3 outlines (1 page each, ready for Week 2+ implementation)

### Documentation

- `SPEC.md` — vision, architecture, MVP plan
- `docs/architecture.md` — 6-layer architecture
- `docs/IMPLEMENTATION-PLAN-v1.0.md` — 5-week roadmap
- `docs/MVP-STATUS.md` — implementation status (all Tier A/B/C closed)
- `docs/connect-target.md` — usage guide with 5 worked examples
- `docs/engines-setup.md` — installation + troubleshooting for 5 engines

### Tests

- **374 tests passing** (unit + integration)
- **89% coverage** across the codebase
- **mypy --strict clean** (26 source files)
- **ruff clean** (no outstanding warnings)
- End-to-end `safe_rename` integration test (forward + rollback)

---

## Quality metrics

| Metric | Value |
|--------|-------|
| Lines of code (src/) | ~1,640 |
| Lines of tests | ~3,300 |
| Specs completed | 11 (5 Tier-A + 4 Tier-B + 2 Tier-C) |
| Spec outlines for v2/v3 | 9 |
| Test count | 374 |
| Coverage | 89% |
| mypy --strict | clean (26 files) |
| ruff | clean |

---

## Known limitations (Week 2 work)

1. **`te` modeling fallback not implemented** — `powerbi-modeling-mcp` is the only modeling engine; fallback chain is empty.
2. **`pbip-validator` adapter not implemented** — `validate_pbir` falls back to a structural-only check (no semantic validation).
3. **`dscmd` adapter not implemented** — DAX tracing unavailable.
4. **No real-binary integration tests** — all engine tests use mocks (`mock_responses`). Real subprocess tests deferred to when binaries are installed.
5. **No fixture PBIP generated** — `tests/fixtures/README.md` exists but `generate.py` not implemented.
6. **In-memory plan store** — plans don't persist across server restarts (acceptable for MVP single-process).

---

## Installation

```bash
pip install powerbi-orchestrator-mcp
```

Then in your MCP client config:

```json
{"mcpServers":{"powerbi-orchestrator-mcp":{
  "command":"powerbi-orchestrator-mcp",
  "args":["--start"],
  "env":{"PBI_AUTH_MODE":"interactive"}
}}}
```

See [`docs/engines-setup.md`](engines-setup.md) for installing the
optional subprocess engines (powerbi-modeling-mcp, te, etc.).

---

## Compatibility

- **Python:** 3.11+ (uses `StrEnum`, `tomllib`, modern type syntax)
- **OS:** Linux, macOS, Windows (some engines have OS restrictions)
- **MCP protocol:** 2025-06-18 (elicitation, resources, prompts)

---

## Acknowledgments

Spec-first development approach: 11 specs were written and committed
before any implementation code in those areas. This caught design
errors cheaply (e.g. cross-engine rollback debt detected by integration
test before production).

The `python_report` adapter serves as the always-available fallback
for report operations, ensuring MVP works on any OS without external
dependencies.

---

## Next: v0.2.0 (Week 2-3)

Planned additions per [`SPEC.md` §6.1](../SPEC.md):
- `te` modeling adapter
- `pbip-validator` adapter
- `te_bpa_runner` validation layer
- `audit_model_and_report` composite tool
- `safe_rename` end-to-end with real binary
- Fixture PBIP generation

---

**Released:** 2026-08-26 by Bastian Berrios (@berriosb)
**License:** MIT
**Tag:** v0.1.0
