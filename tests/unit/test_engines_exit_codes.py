"""Tests for engines.exit_codes — exit code → EngineError mapping (spec §4)."""

from __future__ import annotations

import pytest

from powerbi_orchestrator_mcp.engines.errors import (
    EngineAuthError,
    EngineContractError,
    EngineCrashedError,
    EngineNotFoundError,
    EngineOutputParseError,
    EngineTimeoutError,
    EngineValidationError,
    EngineVersionMismatchError,
)
from powerbi_orchestrator_mcp.engines.exit_codes import (
    EXIT_AUTH_FAILED,
    EXIT_COMMAND_NOT_FOUND,
    EXIT_CONTRACT_VIOLATION_BASE,
    EXIT_CRASHED,
    EXIT_INCOMPATIBLE_VERSION,
    EXIT_INTERNAL_TIMEOUT,
    EXIT_OK,
    EXIT_VALIDATION_FAILED,
    is_degradable,
    is_retryable,
    is_timeout,
    is_validation,
    map_exit_code_to_error,
)


class TestMapExitCode:
    def test_zero_returns_none(self) -> None:
        assert map_exit_code_to_error("te", EXIT_OK) is None

    def test_exit_1_is_validation(self) -> None:
        err = map_exit_code_to_error("te", EXIT_VALIDATION_FAILED, "bad input")
        assert isinstance(err, EngineValidationError)
        assert err.code == "engine_validation_failed"
        assert "bad input" in err.message
        assert err.retryable is False

    def test_exit_2_is_auth(self) -> None:
        err = map_exit_code_to_error("te", EXIT_AUTH_FAILED, "401 unauthorized")
        assert isinstance(err, EngineAuthError)
        assert err.code == "engine_auth_failed"
        assert err.retryable is False
        assert "AZURE_CLIENT_ID" in err.remediation_hint

    def test_exit_3_is_internal_timeout(self) -> None:
        err = map_exit_code_to_error(
            "te", EXIT_INTERNAL_TIMEOUT, "hung on query", timeout_s=60
        )
        assert isinstance(err, EngineTimeoutError)
        assert err.code == "engine_timeout"
        assert err.timeout_s == 60
        assert err.retryable is True

    def test_exit_4_is_crashed(self) -> None:
        err = map_exit_code_to_error("te", EXIT_CRASHED, "segfault")
        assert isinstance(err, EngineCrashedError)
        assert err.code == "engine_crashed"
        assert err.retryable is False

    def test_exit_5_is_version_mismatch(self) -> None:
        err = map_exit_code_to_error(
            "te", EXIT_INCOMPATIBLE_VERSION, "expected 3.0.0, got 2.5.0"
        )
        assert isinstance(err, EngineVersionMismatchError)
        assert err.code == "engine_version_mismatch"
        assert "src/engines/versions.py" in err.remediation_hint

    def test_exit_64_plus_is_contract_violation(self) -> None:
        err = map_exit_code_to_error("te", 64, "schema changed")
        assert isinstance(err, EngineContractError)
        assert err.code == "engine_contract_violation_64"
        assert err.retryable is False

        err2 = map_exit_code_to_error("te", 99, "more drift")
        assert isinstance(err2, EngineContractError)
        assert err2.code == "engine_contract_violation_99"

    def test_exit_127_is_not_found(self) -> None:
        err = map_exit_code_to_error("te", EXIT_COMMAND_NOT_FOUND)
        assert isinstance(err, EngineNotFoundError)
        assert err.code == "engine_not_found"
        assert err.retryable is False

    def test_unknown_exit_code_is_crashed_with_specific_code(self) -> None:
        err = map_exit_code_to_error("te", 42, "weird")
        assert isinstance(err, EngineCrashedError)
        assert err.code == "engine_unknown_exit_42"
        assert err.retryable is False

    def test_stderr_truncated_when_too_long(self) -> None:
        long = "x" * 1000
        err = map_exit_code_to_error("te", EXIT_VALIDATION_FAILED, long)
        assert err is not None
        # Truncated to ~500 chars including the marker.
        assert "...[truncated]" in err.message
        assert len(err.message) < 1000

    def test_empty_stderr_does_not_break(self) -> None:
        err = map_exit_code_to_error("te", EXIT_CRASHED, "")
        assert err is not None
        assert "no stderr" in err.message

    def test_engine_name_always_propagated(self) -> None:
        for code in (1, 2, 3, 4, 5, 64, 127, 99):
            err = map_exit_code_to_error("superbi-mcp", code)
            if err is not None:
                assert err.engine == "superbi-mcp"


class TestExitCodeConstants:
    def test_constants_are_canonical(self) -> None:
        # Per spec §4 table — these MUST NOT change without an ADR.
        assert EXIT_OK == 0
        assert EXIT_VALIDATION_FAILED == 1
        assert EXIT_AUTH_FAILED == 2
        assert EXIT_INTERNAL_TIMEOUT == 3
        assert EXIT_CRASHED == 4
        assert EXIT_INCOMPATIBLE_VERSION == 5
        assert EXIT_CONTRACT_VIOLATION_BASE == 64
        assert EXIT_COMMAND_NOT_FOUND == 127


class TestPredicates:
    def test_is_retryable(self) -> None:
        err = map_exit_code_to_error("te", EXIT_INTERNAL_TIMEOUT, timeout_s=60)
        assert is_retryable(err) is True  # type: ignore[arg-type]

        not_retryable = map_exit_code_to_error("te", EXIT_VALIDATION_FAILED)
        assert is_retryable(not_retryable) is False  # type: ignore[arg-type]

    def test_is_timeout(self) -> None:
        timeout = map_exit_code_to_error("te", EXIT_INTERNAL_TIMEOUT, timeout_s=60)
        assert is_timeout(timeout) is True  # type: ignore[arg-type]

        validation = map_exit_code_to_error("te", EXIT_VALIDATION_FAILED)
        assert is_timeout(validation) is False  # type: ignore[arg-type]

    def test_is_validation(self) -> None:
        v = map_exit_code_to_error("te", EXIT_VALIDATION_FAILED)
        assert is_validation(v) is True  # type: ignore[arg-type]

        a = map_exit_code_to_error("te", EXIT_AUTH_FAILED)
        assert is_validation(a) is False  # type: ignore[arg-type]

    def test_is_degradable_only_for_not_found(self) -> None:
        """Per spec 05 §7: only EngineNotFoundError triggers graceful degradation."""
        nf = map_exit_code_to_error("te", EXIT_COMMAND_NOT_FOUND)
        assert is_degradable(nf) is True  # type: ignore[arg-type]

        # All other errors propagate (selector asks user to install).
        for code, expected in [
            (EXIT_VALIDATION_FAILED, False),
            (EXIT_AUTH_FAILED, False),
            (EXIT_INTERNAL_TIMEOUT, False),
            (EXIT_CRASHED, False),
            (EXIT_INCOMPATIBLE_VERSION, False),
            (64, False),
            (42, False),
        ]:
            err = map_exit_code_to_error("te", code)
            assert is_degradable(err) is expected, (  # type: ignore[arg-type]
                f"code {code} should be degradable={expected}"
            )


class TestOutputParseError:
    """OutputParseError is raised by the adapter itself, not from exit codes.

    This test verifies the class exists and is importable + has the right
    fields, so adapter implementations can use it.
    """

    def test_instantiable(self) -> None:
        err = EngineOutputParseError(
            "stdout was not JSON",
            engine="te",
            code="engine_output_parse_error",
            remediation_hint="upgrade te or report upstream",
        )
        assert err.code == "engine_output_parse_error"
        assert err.engine == "te"


@pytest.mark.parametrize(
    "code,expected_class",
    [
        (0, None),
        (1, EngineValidationError),
        (2, EngineAuthError),
        (3, EngineTimeoutError),
        (4, EngineCrashedError),
        (5, EngineVersionMismatchError),
        (64, EngineContractError),  # boundary: 64 is contract violation per spec
        (99, EngineContractError),  # any 64+ is contract violation per spec
        (127, EngineNotFoundError),
        (200, EngineContractError),  # per spec §4: 64+ → contract violation
        (63, EngineCrashedError),   # just below the contract threshold → unknown
    ],
)
def test_exit_code_mapping_completeness(
    code: int, expected_class: type[Exception] | None
) -> None:
    err = map_exit_code_to_error("te", code)
    if expected_class is None:
        assert err is None
    else:
        assert isinstance(err, expected_class), (
            f"exit {code} should map to {expected_class.__name__}, "
            f"got {type(err).__name__ if err else 'None'}"
        )
