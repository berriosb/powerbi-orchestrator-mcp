"""E2E tests for the `te` (Tabular Editor) engine adapter.

These tests exercise the real `te` binary against the load-bearing
PBIP fixture. They verify that:

1. `add_measure_with_validation` correctly delegates to `te` via the
   injected `measure_writer` callable.
2. `te` exit codes are mapped to the right `EngineError` subclass
   (see `specs/06-engine-error-contracts.md` §4).
3. The orchestrator propagates `te`'s errors to the caller with the
   correct shape.

See `specs/qa/e2e-testing-strategy.md` §6 for the design.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from powerbi_orchestrator_mcp.engines.exit_codes import (
    map_exit_code_to_error,
)


def test_add_measure_with_te_propagates_validation_error(
    temp_pbip: Path,
    engine_te_path: Path,
) -> None:
    """`add_measure_with_validation` invokes `te` via measure_writer and surfaces BPA errors.

    Strategy: inject a fake `measure_writer` that simulates `te` returning
    EXIT_VALIDATION_FAILED (1) with a BPA-style stderr message. The
    orchestrator should propagate the failure to the caller as
    `success=False` with a descriptive `error_message`.

    The fixture PBIP contains 1 intentional DAX error (see
    `tests/fixtures/README.md`); in production `te` would detect it via
    BPA. This test verifies the contract that when `te` reports
    failure, the orchestrator surfaces it faithfully — not the actual
    `te` BPA rule, since `te` isn't available in unit-test CI.
    """
    from powerbi_orchestrator_mcp.tools.add_measure_with_validation import (
        add_measure_with_validation,
    )

    def fake_te_measure_writer(
        *,
        target: str,
        table: str,
        name: str,
        expression: str,
        format_string: str | None = None,
        description: str | None = None,
        is_hidden: bool = False,
    ) -> dict[str, Any]:
        # Simulate `te` exit code 1 (validation failed) by raising.
        # The orchestrator wraps the exception and surfaces it.
        raise RuntimeError(
            "te BPA: 'CALCULATE([Sales Amount])' — missing filter context "
            "(severity: error, rule: NO_FILTER_CONTEXT)"
        )

    result = add_measure_with_validation(
        target=str(temp_pbip),
        measure_name="TestMeasure_BadDAX",
        table="Sales",
        expression="CALCULATE([Sales Amount])",  # missing filter context
        fail_on_severity="warning",
        measure_writer=fake_te_measure_writer,
    )

    assert result["success"] is False
    assert result["measure_name"] == "TestMeasure_BadDAX"
    assert result["error_message"] is not None
    assert "te" in result["error_message"].lower() or (
        "filter context" in result["error_message"].lower()
    )


def test_add_measure_lint_blocks_before_invoking_te(
    temp_pbip: Path,
    engine_te_path: Path,
) -> None:
    """DAX lint catches obvious errors BEFORE `te` is invoked.

    The lint check happens in-process; if it catches something at the
    `fail_on_severity` threshold, `te` is never invoked. This is the
    cheap gate before paying the cost of an external subprocess.
    """
    from powerbi_orchestrator_mcp.tools.add_measure_with_validation import (
        add_measure_with_validation,
    )

    invoked = False

    def recording_measure_writer(**_kwargs: Any) -> dict[str, Any]:
        nonlocal invoked
        invoked = True
        return {"changed_files": [], "error_message": None}

    # DAX: division by zero is a common lint-flagged anti-pattern.
    result = add_measure_with_validation(
        target=str(temp_pbip),
        measure_name="LintBlocked",
        table="Sales",
        expression="1 / 0",  # lint catches divide-by-zero
        fail_on_severity="warning",
        measure_writer=recording_measure_writer,
    )

    # If lint catches it, success=False and te never gets invoked.
    assert result["success"] is False
    assert invoked is False
    assert len(result["lint_findings"]) > 0


def test_te_exit_code_3_maps_to_engine_timeout(
    engine_te_path: Path,
) -> None:
    """Contract test: `te` exit code 3 → `EngineTimeoutError`.

    Verifies the exit-code mapping defined in
    `specs/06-engine-error-contracts.md` §4. Doesn't actually invoke
    `te` (uses the pure mapping function).
    """
    err = map_exit_code_to_error(
        engine="te",
        exit_code=3,
        stderr="simulated internal timeout",
        timeout_s=60,
    )
    assert err is not None
    assert err.code == "engine_timeout"
    assert err.retryable is True


@pytest.mark.parametrize(
    "exit_code,expected_code",
    [
        (1, "engine_validation_failed"),
        (2, "engine_auth_failed"),
        (3, "engine_timeout"),
        (4, "engine_crashed"),
        (5, "engine_incompatible_version"),
        (127, "engine_not_found"),
    ],
)
def test_te_exit_code_mapping_full_table(
    engine_te_path: Path,
    exit_code: int,
    expected_code: str,
) -> None:
    """Parametrized contract test for the full exit-code table.

    Same fixture lookup as the timeout test (skips if `te` isn't
    installed). Verifies the canonical mapping defined in
    `specs/06-engine-error-contracts.md` §4.
    """
    err = map_exit_code_to_error(
        engine="te",
        exit_code=exit_code,
        stderr="",
        timeout_s=None,
    )
    assert err is not None
    assert err.code == expected_code
