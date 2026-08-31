"""Tests for engines.errors — EngineError hierarchy (spec 06-engine-error-contracts §2)."""

from __future__ import annotations

import pytest

from powerbi_orchestrator_mcp.engines.errors import (
    EngineAuthError,
    EngineContractError,
    EngineCrashedError,
    EngineError,
    EngineNotFoundError,
    EngineOutputParseError,
    EngineTimeoutError,
    EngineValidationError,
    EngineVersionMismatchError,
)


class TestEngineError:
    def test_carries_all_required_fields(self) -> None:
        err = EngineError(
            "boom",
            engine="te",
            code="x",
            remediation_hint="do Y",
            retryable=True,
        )
        assert str(err) == "boom"
        assert err.message == "boom"
        assert err.engine == "te"
        assert err.code == "x"
        assert err.remediation_hint == "do Y"
        assert err.retryable is True
        assert err.timeout_s is None

    def test_default_retryable_false(self) -> None:
        err = EngineError(
            "boom",
            engine="te",
            code="x",
            remediation_hint="do Y",
        )
        assert err.retryable is False

    def test_is_exception(self) -> None:
        err = EngineError(
            "boom",
            engine="te",
            code="x",
            remediation_hint="do Y",
        )
        with pytest.raises(EngineError):
            raise err


class TestSubclasses:
    def test_not_found_default_retryable(self) -> None:
        err = EngineNotFoundError(
            "not installed",
            engine="te",
            code="engine_not_found",
            remediation_hint="install it",
        )
        assert err.retryable is False

    def test_version_mismatch_default_retryable(self) -> None:
        err = EngineVersionMismatchError(
            "wrong version",
            engine="te",
            code="engine_version_mismatch",
            remediation_hint="upgrade",
        )
        assert err.retryable is False

    def test_timeout_is_always_retryable(self) -> None:
        err = EngineTimeoutError(
            "hung",
            engine="te",
            remediation_hint="retry smaller",
            timeout_s=60,
        )
        assert err.retryable is True
        assert err.timeout_s == 60
        assert err.code == "engine_timeout"

    def test_crashed_default_retryable(self) -> None:
        err = EngineCrashedError(
            "segfault",
            engine="te",
            code="engine_crashed",
            remediation_hint="reproduce upstream",
        )
        assert err.retryable is False

    def test_output_parse_default_retryable(self) -> None:
        err = EngineOutputParseError(
            "bad json",
            engine="te",
            code="engine_output_parse_error",
            remediation_hint="retry",
        )
        assert err.retryable is False

    def test_auth_default_retryable(self) -> None:
        err = EngineAuthError(
            "401",
            engine="te",
            code="engine_auth_failed",
            remediation_hint="refresh token",
        )
        assert err.retryable is False

    def test_validation_default_retryable(self) -> None:
        err = EngineValidationError(
            "bad input",
            engine="te",
            code="engine_validation_failed",
            remediation_hint="fix input",
        )
        assert err.retryable is False

    def test_contract_default_retryable(self) -> None:
        err = EngineContractError(
            "schema drift",
            engine="te",
            code="engine_contract_violation_64",
            remediation_hint="file upstream bug",
        )
        assert err.retryable is False


class TestHierarchy:
    """All subclasses are catchable as EngineError (uniform handling)."""

    @pytest.mark.parametrize(
        "cls",
        [
            EngineNotFoundError,
            EngineVersionMismatchError,
            EngineTimeoutError,
            EngineCrashedError,
            EngineOutputParseError,
            EngineAuthError,
            EngineValidationError,
            EngineContractError,
        ],
    )
    def test_subclass_is_engine_error(self, cls: type[EngineError]) -> None:
        kwargs: dict[str, str | int] = {
            "engine": "te",
            "code": "k",
            "remediation_hint": "h",
        }
        if cls is EngineTimeoutError:
            kwargs["timeout_s"] = 30
            kwargs["message"] = "x"
        else:
            kwargs["message"] = "x"
        err = cls(**kwargs)  # type: ignore[arg-type]
        assert isinstance(err, EngineError)


class TestRepr:
    def test_repr_is_diagnostic(self) -> None:
        err = EngineTimeoutError(
            "x",
            engine="te",
            remediation_hint="r",
            timeout_s=30,
        )
        r = repr(err)
        assert "EngineTimeoutError" in r
        assert "te" in r
        assert "engine_timeout" in r
        assert "retryable=True" in r
