"""Engine selector — picks the best available engine with graceful degradation.

Implements ``specs/05-engines-adapters.md`` §3 + §4.

For MVP, only ``ModelingEngine`` selection is implemented (no
``ReportEngine`` selection yet — Week 2 task).

Selection rules:
1. Try the preferred engine for the operation.
2. On ``EngineNotFoundError``, walk a fallback chain.
3. On other EngineErrors (timeout, crash, validation), propagate.
4. Cache the picked engine per (operation, target) so we don't
   re-probe on every call.
"""

from __future__ import annotations

from typing import Any

from powerbi_orchestrator_mcp.engines.base import ModelingEngine
from powerbi_orchestrator_mcp.engines.errors import (
    EngineError,
    EngineNotFoundError,
)
from powerbi_orchestrator_mcp.orchestrator.context import EngineStatus, Target

# ---------------------------------------------------------------------------
# Selection config per operation
# ---------------------------------------------------------------------------

# Per-operation preferred engine + fallback chain.
# Each entry: (preferred_engine_name, [fallback_engine_names]).
# ``None`` for an engine name means "the caller wants the default".
DEFAULT_MODELING_CHAIN: tuple[str, tuple[str, ...]] = (
    # Order matters: first available in the chain wins.
    # Currently only modeling_mcp; fallbacks would be te (CLI fallback)
    # when the model lives in a PBIP we can read directly.
    "powerbi-modeling-mcp",
    (),  # no fallback in MVP; this is where te would go in Week 2
)


# ---------------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------------


class EngineSelector:
    """Resolves which engine to use for a given operation.

    Construction takes a registry of candidate engines (the orchestrator's
    step_executor registry is reused for this purpose in server.py).
    """

    def __init__(
        self,
        engines: dict[str, ModelingEngine] | None = None,
    ) -> None:
        self._engines: dict[str, ModelingEngine] = dict(engines or {})
        self._cache: dict[tuple[str, str], str] = {}
        # cache key: (operation, target_type) → engine_name picked

    def register(self, name: str, engine: ModelingEngine) -> None:
        self._engines[name] = engine

    def unregister(self, name: str) -> None:
        self._engines.pop(name, None)
        # Drop cache entries that pointed at this engine.
        for key, picked in list(self._cache.items()):
            if picked == name:
                self._cache.pop(key)

    def available(self) -> dict[str, EngineStatus]:
        """Best-effort health check across all registered engines."""
        import asyncio

        async def _probe_all() -> dict[str, EngineStatus]:
            statuses: dict[str, EngineStatus] = {}
            for name, engine in self._engines.items():
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
        cache_key = (operation, target.target_type)
        if cache_key in self._cache:
            name = self._cache[cache_key]
            engine = self._engines.get(name)
            if engine is not None:
                return engine

        preferred_name, fallback_chain = DEFAULT_MODELING_CHAIN
        chain = (preferred_name, *fallback_chain)

        last_not_found: EngineNotFoundError | None = None
        for name in chain:
            engine = self._engines.get(name)
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
                f"EngineSelector.register(name, engine_instance)"
            ),
        )

    # ------------------------------------------------------------------
    # Convenience: pre-flight for an entire plan
    # ------------------------------------------------------------------

    def plan_compatibility(
        self, plan_operations: list[tuple[str, Target]]
    ) -> dict[str, Any]:
        """Pre-flight: check if all ops can be served by registered engines.

        Returns a dict with ``ready`` (bool) and a ``missing`` list of
        operations that have no available engine. Useful for plan_change
        to surface "this plan needs X engine which isn't installed".
        """
        missing: list[str] = []
        for op, target in plan_operations:
            try:
                self.select_modeling_engine(op, target)
            except EngineNotFoundError:
                missing.append(op)
        return {
            "ready": not missing,
            "missing": missing,
        }
