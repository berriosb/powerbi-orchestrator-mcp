"""End-to-end tests for the powerbi-orchestrator-mcp.

These tests exercise the orchestrator against REAL engine binaries
(`te`, `dscmd`, `pbip-validator`) on a REAL PBIP fixture (not mocks).
They are slow, environment-dependent, and may flake.

Run with:

    pytest tests/e2e/ -v --tb=short --junitxml=e2e-results.xml

They are NOT run by default in the unit/integration matrix; they run
in `.github/workflows/e2e-nightly.yml` on a schedule and on changes
to `src/powerbi_orchestrator_mcp/engines/**` or `tests/fixtures/**`.

If the engine binaries are not installed, each test calls its
respective `engine_*_path` fixture which returns a `pytest.skip`
sentinel — so the suite degrades gracefully on machines without the
binaries (e.g., macOS runners missing `dscmd`).

See `specs/qa/e2e-testing-strategy.md` for the design rationale,
including how binaries are pinned by SHA256 in the workflow.
"""
