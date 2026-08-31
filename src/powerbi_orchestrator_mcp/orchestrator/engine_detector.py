"""Engine detector - discovers which subprocess engines are installed.

Implements the discovery portion of specs/05-engines-adapters.md §3 +
the per-target-type behavior in §11.

For each known engine, this module:
1. Resolves the binary name (binary or ``npx`` for npm packages).
2. Checks PATH (``shutil.which``).
3. Optionally probes ``--version`` via subprocess with timeout.

Per-engine configuration is centralized here so adding a new engine
later is a single constant.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass

from powerbi_orchestrator_mcp.engines.timeouts import (
    DEFAULT_TIMEOUTS,
    resolve_timeout,
)
from powerbi_orchestrator_mcp.orchestrator.context import EngineStatus

# ---------------------------------------------------------------------------
# Engine detection config
# ---------------------------------------------------------------------------

# Windows + Linux + Mac binary names to try, in order.
# Each entry maps an engine key to its detection strategy.
#
# A "version_cmd" of None means we only check existence (PATH lookup),
# which is the right thing for engines whose ``--version`` is unreliable
# or expensive (e.g. spawning `npx` for an MCP package).
@dataclass(frozen=True)
class EngineProbe:
    """How to detect a single engine."""

    engine: str
    binary_names: tuple[str, ...]  # try in order
    version_args: tuple[str, ...] = ()  # empty → existence check only
    npx_package: str | None = None  # for npm-based MCP servers


_ENGINE_PROBES: tuple[EngineProbe, ...] = (
    EngineProbe(
        engine="powerbi-modeling-mcp",
        binary_names=("powerbi-modeling-mcp",),
        npx_package="@microsoft/powerbi-modeling-mcp",
    ),
    EngineProbe(
        engine="te",
        binary_names=("TabularEditor", "te", "te2"),
        version_args=("--version",),
    ),
    EngineProbe(
        engine="dscmd",
        binary_names=("dscmd", "daxstudio"),
        version_args=("--version",),
    ),
    EngineProbe(
        engine="pbip-validator",
        binary_names=("pbip-validator",),
        version_args=("--version",),
    ),
    EngineProbe(
        engine="superbi-mcp",
        binary_names=("superbi-mcp",),
        npx_package="superbi-mcp",
    ),
)


def _resolve_binary(binary_names: tuple[str, ...]) -> str | None:
    """Find the first binary name present in PATH."""
    for name in binary_names:
        path = shutil.which(name)
        if path is not None:
            return path
    return None


async def _probe_version(binary_path: str, args: tuple[str, ...]) -> str | None:
    """Run ``binary_path args`` with timeout and return stdout stripped."""
    if not args:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            binary_path,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, FileNotFoundError):
        return None
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
    except (TimeoutError, OSError):
        proc.kill()
        await proc.wait()
        return None
    if proc.returncode != 0:
        return None
    text = stdout.decode("utf-8", errors="replace").strip()
    return text.splitlines()[0] if text else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def detect_engine(probe: EngineProbe) -> EngineStatus:
    """Detect a single engine. Returns an ``EngineStatus`` (never raises).

    Strategy:
    1. Try direct binary lookup in PATH.
    2. If not found and engine is npm-based, note it as unavailable
       (we do NOT auto-install; user must run ``npx`` manually).
    3. If found and has version_args, probe version (best-effort,
       fail silently — engines that crash on --version still count).
    """
    binary_path = _resolve_binary(probe.binary_names)

    if binary_path is None:
        reason = (
            f"binary not found in PATH (tried {', '.join(probe.binary_names)})"
        )
        if probe.npx_package is not None:
            reason += f"; for npm package '{probe.npx_package}', run `npx {probe.npx_package}` to verify"
        return EngineStatus(
            name=probe.engine,
            available=False,
            version=None,
            reason_unavailable=reason,
        )

    version: str | None = None
    if probe.version_args:
        version = await _probe_version(binary_path, probe.version_args)

    return EngineStatus(
        name=probe.engine,
        available=True,
        version=version,
        reason_unavailable=None,
    )


async def detect_all_engines() -> dict[str, EngineStatus]:
    """Detect all known engines in parallel.

    Returns:
        Mapping ``engine_name → EngineStatus``. Always includes every
        known engine (available or not) so the caller can show users
        what they're missing.
    """
    statuses = await asyncio.gather(
        *(detect_engine(probe) for probe in _ENGINE_PROBES),
        return_exceptions=False,
    )
    return {status.name: status for status in statuses}


def get_known_engines() -> tuple[str, ...]:
    """Return the canonical list of known engine names."""
    return tuple(probe.engine for probe in _ENGINE_PROBES)


def get_engine_timeout_s(engine: str) -> int:
    """Resolve default timeout (seconds) for a known engine.

    Raises ``UnknownEngineError`` if engine is not registered. Used by
    adapters that don't want to depend on the engine itself.
    """
    return resolve_timeout(engine, use_env_override=False)


def has_timeout_config(engine: str) -> bool:
    """True if the engine has a timeout entry (used for tests)."""
    return engine in DEFAULT_TIMEOUTS
