"""Engine selector — picks the best available engine with graceful degradation.

Implements ``specs/05-engines-adapters.md`` §3 + §4.

Selection rules (for both Modeling and Report engines):
1. Try the preferred engine for the operation.
2. On ``EngineNotFoundError``, walk a fallback chain.
3. On other EngineErrors (timeout, crash, validation), propagate.
4. Cache the picked engine per (operation, target) so we don't
   re-probe on every call.

As of v1.8.0:
- ``ModelingEngine`` chain: ``powerbi-modeling-mcp`` (preferred) →
  ``te`` (fallback via the Tabular Editor adapter, Sprint 14A).
- ``ReportEngine`` chain: ``python_report`` (built-in, cross-platform) →
  ``superbi-mcp`` (Windows-only, FSL).
"""

from __future__ import annotations

from typing import Any, TypeVar

from powerbi_orchestrator_mcp.engines.base import ModelingEngine, ReportEngine
from powerbi_orchestrator_mcp.engines.errors import (
    EngineError,
    EngineNotFoundError,
)
from powerbi_orchestrator_mcp.orchestrator.context import EngineStatus, Target

_T = TypeVar("_T", ModelingEngine, ReportEngine)
_E = TypeVar("_E", bound=Any)


# ---------------------------------------------------------------------------
# Selection config per operation
# ---------------------------------------------------------------------------

# Per-operation preferred engine + fallback chain.
# Each entry: (preferred_engine_name, [fallback_engine_names]).
DEFAULT_MODELING_CHAIN: tuple[str, tuple[str, ...]] = (
    # Order matters: first available in the chain wins.
    # Sprint 14A: te CLI is the documented fallback when
    # powerbi-modeling-mcp is not installed.
    "powerbi-modeling-mcp",
    ("te",),
)

# Report chain: python_report first (cross-platform, always available),
# then superbi-mcp (Windows-only, FSL) for richer operations.
DEFAULT_REPORT_CHAIN: tuple[str, tuple[str, ...]] = (
    "python_report",
    ("superbi-mcp",),
)

# ---------------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------------


class EngineSelector:
    """Resolves which engine to use for a given operation.

    Construction takes a registry of candidate engines (the orchestrator's
    step_executor registry is reused for this purpose in server.py).
    Holds Modeling engines and Report engines in separate dicts so
    selection is unambiguous.
    """

    def __init__(
        self,
        modeling_engines: dict[str, ModelingEngine] | None = None,
        report_engines: dict[str, ReportEngine] | None = None,
    ) -> None:
        self._modeling: dict[str, ModelingEngine] = dict(modeling_engines or {})
        self._report: dict[str, ReportEngine] = dict(report_engines or {})
        self._cache: dict[tuple[str, str], str] = {}
        # cache key: (operation, target_type) → engine_name picked

    def register_modeling(self, name: str, engine: ModelingEngine) -> None:
        self._modeling[name] = engine

    def register_report(self, name: str, engine: ReportEngine) -> None:
        self._report[name] = engine

    def register(self, name: str, engine: _E) -> None:
        """Generic register; routes based on engine capabilities.

        Kept for backward compatibility with tests that used a single
        dict. New code should prefer register_modeling / register_report.
        """
        # Protocol-based dispatch via duck-typing (Protocols aren't
        # runtime_checkable without decorator; tests don't subclass them).
        if hasattr(engine, "list_tables") and hasattr(engine, "snapshot"):
            self._modeling[name] = engine
        elif hasattr(engine, "add_page") and hasattr(engine, "add_visual"):
            self._report[name] = engine
        else:
            raise TypeError(
                f"engine {name!r} doesn't match ModelingEngine or ReportEngine"
            )

    def unregister(self, name: str) -> None:
        self._modeling.pop(name, None)
        self._report.pop(name, None)
        for key, picked in list(self._cache.items()):
            if picked == name:
                self._cache.pop(key)

    def available(self) -> dict[str, EngineStatus]:
        """Best-effort health check across all registered engines."""
        import asyncio

        async def _probe_all() -> dict[str, EngineStatus]:
            statuses: dict[str, EngineStatus] = {}
            all_engines: dict[str, Any] = {**self._modeling, **self._report}
            for name, engine in all_engines.items():
                try:
                    status = await engine.health_check()
                except Exception as exc:  # noqa: BLE001
                    status = EngineStatus(
                        name=name,
                        available=False,
                        version=None,
                        reason_unavailable=f"health_check raised: {exc}",
                    )
                statuses[name] = status
            return statuses

        return asyncio.run(_probe_all())

    def select_modeling_engine(
        self,
        operation: str,
        target: Target,
    ) -> ModelingEngine:
        """Pick the best engine for a modeling operation on ``target``.

        Honors the cache (operation, target_type) → engine_name. Falls
        back through the chain on EngineNotFoundError; re-raises other
        EngineErrors so the user is informed.

        Raises:
            EngineNotFoundError: no engine in the chain is available
                AND there's no fallback (this propagates the LAST
                engine's not-found error with remediation hints).
        """
        return self._select(
            operation,
            target,
            self._modeling,
            DEFAULT_MODELING_CHAIN,
        )

    def select_report_engine(
        self,
        operation: str,
        target: Target,
    ) -> ReportEngine:
        """Pick the best engine for a report operation on ``target``."""
        return self._select(
            operation,
            target,
            self._report,
            DEFAULT_REPORT_CHAIN,
        )

    def _select(
        self,
        operation: str,
        target: Target,
        registry: dict[str, _T],
        chain: tuple[str, tuple[str, ...]],
    ) -> _T:
        """Shared selection logic for Modeling and Report chains."""
        cache_key = (operation, target.target_type)
        if cache_key in self._cache:
            name = self._cache[cache_key]
            engine = registry.get(name)
            if engine is not None:
                return engine

        preferred_name = chain[0]
        fallback_chain: tuple[str, ...] = chain[1]
        candidates: tuple[str, ...] = (preferred_name, *fallback_chain)

        last_not_found: EngineNotFoundError | None = None
        for name in candidates:
            engine = registry.get(name)
            if engine is None:
                continue
            try:
                # We don't actually run health_check here (would block);
                # we rely on the engine's own lazy health.
                self._cache[cache_key] = name
                return engine
            except EngineNotFoundError as exc:
                last_not_found = exc
                continue
            except EngineError:
                # Non-availability errors propagate.
                raise

        if last_not_found is not None:
            raise last_not_found
        raise EngineNotFoundError(
            f"no registered engine for operation {operation!r}",
            engine=operation,
            code="engine_not_found",
            remediation_hint=(
                f"Register an adapter for {operation} via "
                f"EngineSelector.register_modeling() / register_report()"
            ),
        )

    # ------------------------------------------------------------------
    # Convenience: pre-flight for an entire plan
    # ------------------------------------------------------------------

    def plan_compatibility(
        self,
        plan_operations: list[tuple[str, Target]],
        kind: str = "modeling",
    ) -> dict[str, Any]:
        """Pre-flight: check if all ops can be served by registered engines.

        Returns a dict with ``ready`` (bool) and a ``missing`` list of
        operations that have no available engine. Useful for plan_change
        to surface "this plan needs X engine which isn't installed".

        Args:
            plan_operations: List of (operation, target) tuples.
            kind: "modeling" (default) or "report" — selects which
                chain to validate against.
        """
        missing: list[str] = []
        for op, target in plan_operations:
            try:
                if kind == "modeling":
                    self.select_modeling_engine(op, target)
                elif kind == "report":
                    self.select_report_engine(op, target)
                else:
                    raise ValueError(f"unknown kind: {kind!r}")
            except EngineNotFoundError:
                missing.append(op)
        return {
            "ready": not missing,
            "missing": missing,
        }
