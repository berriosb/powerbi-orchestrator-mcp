"""E2E tests for the PBIP validation the orchestrator performs natively.

The orchestrator validates a PBIP/PBIR project with its own structural
validator (`PythonReportEngine.validate_pbir`), so these tests need no
external binary and run on every platform. They exercise it against the
real load-bearing fixture, which is the point of the E2E suite: the
fixture on disk, parsed by the production code path.

The engine detector also recognises an external `pbip-validator` binary
if one is installed, but there is no such package on PyPI, so it is not
installed by CI and these tests do not depend on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.engines.base import ConnectionHandle


@pytest.fixture()
def pbip_conn(temp_pbip: Path) -> ConnectionHandle:
    return ConnectionHandle(
        engine="python_report",
        target_type="pbip_folder",
        target_ref=str(temp_pbip),
        session_token="e2e-token",
    )


async def test_native_validation_accepts_the_fixture(pbip_conn: ConnectionHandle) -> None:
    """The load-bearing fixture must pass the native validator."""
    from powerbi_orchestrator_mcp.engines.report_python import PythonReportEngine

    result = await PythonReportEngine().validate_pbir(pbip_conn)

    assert result.valid is True, f"unexpected findings: {result.findings}"


async def test_native_validation_rejects_a_broken_page(pbip_conn: ConnectionHandle) -> None:
    """A page file that does not parse must be reported, not ignored."""

    from powerbi_orchestrator_mcp.engines.report_python import PythonReportEngine

    page = Path(pbip_conn.target_ref) / "sample.Report" / "pages" / "Overview" / "page.json"
    page.write_text("{ not json", encoding="utf-8")

    result = await PythonReportEngine().validate_pbir(pbip_conn)

    assert result.valid is False
    assert any(f.get("rule_id") == "page_json_invalid" for f in result.findings)


async def test_audit_uses_the_native_validation(temp_pbip: Path) -> None:
    """`audit_model_and_report` reports the checks it could actually run."""
    from powerbi_orchestrator_mcp.tools.audit_model_and_report import (
        AuditCheck,
        audit_model_and_report,
    )

    result = await audit_model_and_report(
        pbip_path=str(temp_pbip),
        checks=AuditCheck(
            bpa=False,
            dax_lint=False,
            accessibility=True,
            naming=False,
        ),
    )

    # The accessibility check runs natively, so the score is not partial.
    assert "accessibility" not in result.skipped_checks
    assert result.score_is_partial is False
    assert 0.0 <= result.overall_score <= 100.0
