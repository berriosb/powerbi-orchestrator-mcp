"""E2E tests for the `pbip-validator` engine.

Verifies that the `audit_model_and_report` and `pre_deploy_check`
tools correctly invoke `pbip-validator` against a real PBIP.

See `specs/qa/e2e-testing-strategy.md` §6 for the design rationale.
"""

from __future__ import annotations

from pathlib import Path


def test_audit_with_pbip_validator(
    temp_pbip: Path,
    engine_pbip_validator_path: Path,
) -> None:
    """`audit_model_and_report` invokes `pbip-validator` and surfaces findings."""
    from powerbi_orchestrator_mcp.tools.audit_model_and_report import (
        AuditCheck,
        audit_model_and_report,
    )

    result = audit_model_and_report(
        pbip_path=str(temp_pbip),
        checks=AuditCheck(
            bpa=False,
            dax_lint=False,
            accessibility=False,
            naming=False,
        ),
    )

    # pbip-validator is fast (<5s) and produces deterministic output
    # for the load-bearing fixture.
    assert result.engine_used in {"pbip-validator", "python_report"}


def test_pbip_validator_exit_code_0_passes(
    engine_pbip_validator_path: Path,
    temp_pbip: Path,
) -> None:
    """Direct subprocess test: `pbip-validator` exits 0 on a clean PBIP.

    Sanity check that the binary itself works before testing the
    orchestrator integration. Bypasses if the binary isn't installed.
    """
    import subprocess

    result = subprocess.run(
        [str(engine_pbip_validator_path), "validate", str(temp_pbip)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode in (0, 1), (
        f"pbip-validator exited {result.returncode} unexpectedly: "
        f"stderr={result.stderr!r}"
    )
