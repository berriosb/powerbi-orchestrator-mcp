"""Exit code → EngineError mapping.

Implements specs/06-engine-error-contracts.md §4.

Canonical exit codes for all subprocess engines. The mapping is
deterministic; the only contextual input is the stderr excerpt (used to
enrich the error message).
"""

from __future__ import annotations

from powerbi_orchestrator_mcp.engines.errors import (
    EngineAuthError,
    EngineContractError,
    EngineCrashedError,
    EngineError,
    EngineNotFoundError,
    EngineTimeoutError,
    EngineValidationError,
    EngineVersionMismatchError,
)

# ---------------------------------------------------------------------------
# Exit codes (per spec §4 table)
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_VALIDATION_FAILED = 1
EXIT_AUTH_FAILED = 2
EXIT_INTERNAL_TIMEOUT = 3
EXIT_CRASHED = 4
EXIT_INCOMPATIBLE_VERSION = 5
EXIT_CONTRACT_VIOLATION_BASE = 64  # 64+ = EngineContractError
EXIT_COMMAND_NOT_FOUND = 127


def _truncate_stderr(stderr: str, max_chars: int = 500) -> str:
    """Truncate stderr to a reasonable size for inclusion in the message."""
    if len(stderr) <= max_chars:
        return stderr
    return stderr[: max_chars - 13] + "...[truncated]"


def map_exit_code_to_error(
    engine: str,
    exit_code: int,
    stderr: str = "",
    timeout_s: int | None = None,
) -> EngineError | None:
    """Map a subprocess exit code to an EngineError subclass.

    Returns ``None`` for ``exit_code == 0`` (success).

    Args:
        engine: Name of the engine that produced this exit code.
        exit_code: The integer exit code returned by the subprocess.
        stderr: The captured stderr (truncated to 500 chars in the error).
        timeout_s: Only used for ``EXIT_INTERNAL_TIMEOUT`` to populate
            the corresponding field on ``EngineTimeoutError``.

    Raises:
        Nothing — all branches return an EngineError or None.
    """
    stderr_excerpt = _truncate_stderr(stderr)

    if exit_code == EXIT_OK:
        return None

    if exit_code == EXIT_VALIDATION_FAILED:
        return EngineValidationError(
            f"{engine} validation failed: {stderr_excerpt or 'no stderr'}",
            engine=engine,
            code="engine_validation_failed",
            remediation_hint=(
                f"Fix the input that {engine} rejected and retry. "
                f"See stderr excerpt for details."
            ),
        )

    if exit_code == EXIT_AUTH_FAILED:
        return EngineAuthError(
            f"{engine} authentication failed: {stderr_excerpt or 'no stderr'}",
            engine=engine,
            code="engine_auth_failed",
            remediation_hint=(
                "Verify token / scopes. For SPN: confirm AZURE_CLIENT_ID, "
                "AZURE_TENANT_ID and AZURE_CLIENT_SECRET are set. "
                "For interactive: re-auth via 'az login'."
            ),
        )

    if exit_code == EXIT_INTERNAL_TIMEOUT:
        return EngineTimeoutError(
            f"{engine} internal timeout: {stderr_excerpt or 'no stderr'}",
            engine=engine,
            code="engine_timeout",
            remediation_hint=(
                f"{engine} aborted its own operation. Consider increasing "
                f"PBI_ENGINE_TIMEOUT_<ENGINE>_S or use a smaller input."
            ),
            timeout_s=timeout_s if timeout_s is not None else 0,
        )

    if exit_code == EXIT_CRASHED:
        return EngineCrashedError(
            f"{engine} crashed (exit 4): {stderr_excerpt or 'no stderr'}",
            engine=engine,
            code="engine_crashed",
            remediation_hint=(
                f"{engine} died unexpectedly. Inspect full stderr in the "
                f"audit log; if reproducible, open an upstream issue with "
                f"the stderr excerpt and the version pinned in "
                f"src/engines/versions.py."
            ),
        )

    if exit_code == EXIT_INCOMPATIBLE_VERSION:
        return EngineVersionMismatchError(
            f"{engine} version mismatch: {stderr_excerpt or 'no stderr'}",
            engine=engine,
            code="engine_version_mismatch",
            remediation_hint=(
                f"The installed {engine} version is not in the supported "
                f"range. See src/engines/versions.py for the pinned version "
                f"and docs/engines-setup.md for the upgrade command."
            ),
        )

    if exit_code == EXIT_COMMAND_NOT_FOUND:
        return EngineNotFoundError(
            f"{engine} binary not found in PATH",
            engine=engine,
            code="engine_not_found",
            remediation_hint=(
                f"Install {engine} per docs/engines-setup.md, or fall back "
                f"to an available engine."
            ),
        )

    if exit_code >= EXIT_CONTRACT_VIOLATION_BASE:
        return EngineContractError(
            f"{engine} contract violation (exit {exit_code}): "
            f"{stderr_excerpt or 'no stderr'}",
            engine=engine,
            code=f"engine_contract_violation_{exit_code}",
            remediation_hint=(
                f"{engine} did not respect the contract. File a bug with "
                f"the stderr excerpt and the {engine} version. Likely "
                f"cause: upstream schema drift."
            ),
        )

    # Unknown exit code: treat as crash but with a distinct code.
    return EngineCrashedError(
        f"{engine} unknown exit {exit_code}: {stderr_excerpt or 'no stderr'}",
        engine=engine,
        code=f"engine_unknown_exit_{exit_code}",
        remediation_hint=(
            f"{engine} exited with an unexpected code. Capture full stderr "
            f"and check {engine} upstream docs."
        ),
    )


def is_retryable(error: EngineError) -> bool:
    """Convenience predicate matching ``EngineError.retryable``."""
    return error.retryable


def is_timeout(error: EngineError) -> bool:
    """Convenience predicate for ``isinstance(error, EngineTimeoutError)``."""
    return isinstance(error, EngineTimeoutError)


def is_validation(error: EngineError) -> bool:
    """Convenience predicate for ``isinstance(error, EngineValidationError)``."""
    return isinstance(error, EngineValidationError)


def is_degradable(error: EngineError) -> bool:
    """True if the orchestrator should fall back to another engine.

    Per spec §7 in 05-engines-adapters.md, only ``EngineNotFoundError``
    triggers graceful degradation. All other errors (including timeouts
    and version mismatches) propagate so the user is informed.
    """
    return isinstance(error, EngineNotFoundError)
