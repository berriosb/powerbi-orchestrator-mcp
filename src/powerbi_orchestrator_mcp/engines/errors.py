"""Engine error hierarchy.

Implements the error hierarchy from specs/06-engine-error-contracts.md §2.

All subprocess engine adapters raise subclasses of ``EngineError`` (or one
of its descendants). This lets the selector and the orchestrator apply
uniform degradation rules (e.g. an EngineNotFoundError triggers
degradation; an EngineValidationError does not — it propagates).
"""

from __future__ import annotations


class EngineError(Exception):
    """Base for all errors raised by a subprocess engine adapter.

    Attributes are documented per spec §2.
    """

    def __init__(
        self,
        message: str,
        *,
        engine: str,
        code: str,
        remediation_hint: str,
        retryable: bool = False,
        timeout_s: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.engine = engine
        self.code = code
        self.remediation_hint = remediation_hint
        self.retryable = retryable
        self.timeout_s = timeout_s

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(engine={self.engine!r}, code={self.code!r}, "
            f"retryable={self.retryable!r})"
        )


class EngineNotFoundError(EngineError):
    """Binary or package not installed or not on PATH."""


class EngineVersionMismatchError(EngineError):
    """Installed version is outside the range supported by the adapter."""


class EngineTimeoutError(EngineError):
    """Subprocess exceeded the timeout. ``retryable=True`` by default."""

    def __init__(
        self,
        message: str,
        *,
        engine: str,
        code: str = "engine_timeout",
        remediation_hint: str,
        timeout_s: int,
    ) -> None:
        super().__init__(
            message,
            engine=engine,
            code=code,
            remediation_hint=remediation_hint,
            retryable=True,
            timeout_s=timeout_s,
        )


class EngineCrashedError(EngineError):
    """Subprocess died with a signal (segfault, OOM killed) or unexpected exit."""


class EngineOutputParseError(EngineError):
    """stdout is not parseable JSON or doesn't match the expected schema."""


class EngineAuthError(EngineError):
    """Authentication failed (token expired, scopes insufficient)."""


class EngineValidationError(EngineError):
    """Engine rejected the operation due to validation (not retryable)."""


class EngineContractError(EngineError):
    """Engine doesn't respect the expected contract (schema drift; not retryable)."""
