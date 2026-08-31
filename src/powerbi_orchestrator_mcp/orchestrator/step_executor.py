"""Step executor registry + dry-run executor.

Implements the adapter dispatch layer for ``apply_plan`` (spec §3.3).

In MVP, only ``DryRunExecutor`` is registered by default. Real adapters
(modeling/report/cloud) plug in here in Week 2 — they implement the
``StepExecutor`` Protocol and register themselves at server boot.

Why a Protocol + registry (instead of hard-coded dispatch):
- Tests can swap in scripted executors (already used by rollback tests).
- Adapters are decoupled from the orchestrator (no imports of
  ``engine_detector`` etc.).
- Plugins (v3+) can register at runtime via configuration.
"""

from __future__ import annotations

from typing import Protocol

from powerbi_orchestrator_mcp.orchestrator.plan_models import PlanStep
from powerbi_orchestrator_mcp.orchestrator.rollback import StepOutcome


class StepExecutor(Protocol):
    """Adapter contract used by both ``PlanExecutor`` and ``RollbackEngine``.

    Adapters advertise which engine they handle via ``engine_name``.
    """

    engine_name: str

    async def execute_step(self, step: PlanStep) -> StepOutcome: ...


class DryRunExecutor:
    """Default executor: returns success without performing any side effects.

    Used when ``apply_plan(dry_run=true)`` OR when no real executor is
    registered for the step's engine. Print-style logging is fine here
    because the spec says dry-runs must never write.
    """

    engine_name = "dry_run"

    async def execute_step(self, step: PlanStep) -> StepOutcome:  # noqa: ARG002
        return StepOutcome(
            success=True,
            error_message=None,
            changed_files=[],
        )


class MissingEngineExecutor:
    """Executor returned when no adapter is registered for an engine.

    Always fails the step with a clear remediation hint pointing the
    user to docs/engines-setup.md. This is the orchestrator's safety net
    so apply_plan never silently swallows an unhandled engine.
    """

    engine_name = "__missing__"

    def __init__(self, missing_engine: str) -> None:
        self._missing_engine = missing_engine

    async def execute_step(self, step: PlanStep) -> StepOutcome:  # noqa: ARG002
        return StepOutcome(
            success=False,
            error_message=(
                f"no executor registered for engine {self._missing_engine!r}; "
                f"install per docs/engines-setup.md or remove this step"
            ),
            changed_files=[],
        )


class StepExecutorRegistry:
    """Maps engine names to executor instances."""

    def __init__(self) -> None:
        self._executors: dict[str, StepExecutor] = {}

    def register(self, executor: StepExecutor) -> None:
        """Register an executor. Last registration wins for a given engine."""
        self._executors[executor.engine_name] = executor

    def unregister(self, engine_name: str) -> None:
        """Remove an executor (used by tests + plugin unload in v3)."""
        self._executors.pop(engine_name, None)

    def get(self, engine_name: str) -> StepExecutor:
        """Return the executor for an engine, or a MissingEngineExecutor.

        Never returns ``None`` — callers can rely on always getting a
        usable StepExecutor. MissingEngineExecutor always fails the step
        with a clear error so the rollback engine kicks in cleanly.
        """
        executor = self._executors.get(engine_name)
        if executor is None:
            return MissingEngineExecutor(engine_name)
        return executor

    def has(self, engine_name: str) -> bool:
        """True iff a real (non-MissingEngine) executor is registered."""
        return engine_name in self._executors

    def available_engines(self) -> tuple[str, ...]:
        """Names of registered executors (excludes ``__missing__``)."""
        return tuple(
            name
            for name, executor in self._executors.items()
            if executor.engine_name != "__missing__"
        )


# ---------------------------------------------------------------------------
# Default registry (singleton)
# ---------------------------------------------------------------------------

_default_registry = StepExecutorRegistry()
# Always register the dry-run executor so apply_plan(dry_run=true) and
# apply_plan with no real adapters have a safe default.
_default_registry.register(DryRunExecutor())


def get_default_registry() -> StepExecutorRegistry:
    """Return the process-wide default registry."""
    return _default_registry


def reset_default_registry() -> None:
    """Reset to a fresh registry with only DryRunExecutor (for tests)."""
    global _default_registry  # noqa: PLW0603
    _default_registry = StepExecutorRegistry()
    _default_registry.register(DryRunExecutor())
