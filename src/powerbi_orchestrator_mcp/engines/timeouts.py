"""Engine timeout configuration.

Implements specs/06-engine-error-contracts.md §3.

Per-engine default + max timeouts, with env var overrides for ops
flexibility. Adapters query ``resolve_timeout()`` before each subprocess
invocation and respect the returned value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EngineTimeout:
    """Default + maximum timeout for an engine subprocess.

    Attributes:
        default_s: Used when the caller doesn't specify a timeout.
        max_s: Hard cap; values above this are rejected.
        long_running: Hint that some operations of this engine may need
            significantly longer than the default (e.g. trace FE/SE).
            Adapters should accept a per-call timeout override.
    """

    default_s: int
    max_s: int
    long_running: bool = False


DEFAULT_TIMEOUTS: dict[str, EngineTimeout] = {
    "powerbi-modeling-mcp": EngineTimeout(default_s=30, max_s=120),
    "te": EngineTimeout(default_s=60, max_s=300, long_running=True),
    "dscmd": EngineTimeout(default_s=90, max_s=600, long_running=True),
    "pbip-validator": EngineTimeout(default_s=15, max_s=60),
    "superbi-mcp": EngineTimeout(default_s=45, max_s=180),
}

# Per spec §3. Env var name → engine key. Uppercase + underscores replace
# hyphens for shell-friendliness.
ENV_TIMEOUT_OVERRIDES: dict[str, str] = {
    "PBI_ENGINE_TIMEOUT_POWERBI_MODELING_MCP_S": "powerbi-modeling-mcp",
    "PBI_ENGINE_TIMEOUT_TE_S": "te",
    "PBI_ENGINE_TIMEOUT_DSCMD_S": "dscmd",
    "PBI_ENGINE_TIMEOUT_PBIP_VALIDATOR_S": "pbip-validator",
    "PBI_ENGINE_TIMEOUT_SUPERBI_S": "superbi-mcp",
}


class UnknownEngineError(ValueError):
    """Raised when resolving a timeout for an unregistered engine name."""


class InvalidTimeoutError(ValueError):
    """Raised when a caller-provided timeout is out of range."""


def _env_override_for(engine: str) -> int | None:
    """Return the env-overridden default for an engine, or None."""
    for env_var, mapped_engine in ENV_TIMEOUT_OVERRIDES.items():
        if mapped_engine == engine:
            raw = os.environ.get(env_var)
            if raw is None:
                return None
            try:
                value = int(raw)
            except ValueError as exc:
                raise ValueError(
                    f"env var {env_var} must be integer seconds, got {raw!r}"
                ) from exc
            if value <= 0:
                raise ValueError(
                    f"env var {env_var} must be positive, got {value}"
                )
            return value
    return None


def resolve_timeout(
    engine: str,
    *,
    requested_s: int | None = None,
    use_env_override: bool = True,
) -> int:
    """Resolve the effective timeout (in seconds) for an engine subprocess.

    Args:
        engine: Engine identifier (must be a key of ``DEFAULT_TIMEOUTS``).
        requested_s: Caller-specified timeout. ``None`` means use the
            default (with optional env override).
        use_env_override: Whether to apply env var overrides to the
            default. Disable in tests that need deterministic values.

    Returns:
        The timeout to use, in seconds. Always ``>= 1`` and ``<= max_s``.

    Raises:
        UnknownEngineError: engine not in DEFAULT_TIMEOUTS.
        InvalidTimeoutError: requested_s < 1 or > max_s.
    """
    cfg = DEFAULT_TIMEOUTS.get(engine)
    if cfg is None:
        raise UnknownEngineError(
            f"no timeout config registered for engine {engine!r}; "
            f"known engines: {sorted(DEFAULT_TIMEOUTS)}"
        )

    if requested_s is None:
        env_value = _env_override_for(engine) if use_env_override else None
        return env_value if env_value is not None else cfg.default_s

    if requested_s < 1:
        raise InvalidTimeoutError(
            f"requested timeout {requested_s}s must be >= 1"
        )
    if requested_s > cfg.max_s:
        raise InvalidTimeoutError(
            f"requested timeout {requested_s}s exceeds max {cfg.max_s}s "
            f"for engine {engine!r}"
        )
    return requested_s
