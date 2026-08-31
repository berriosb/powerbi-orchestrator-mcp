"""Rollback engine - inverse-order plan reversal.

Implements specs/01-orchestrator.md §2.5.

When a step fails during ``apply_plan``, the rollback engine reverses
all previously-successful steps in inverse order using each step's
``rollback_step`` field. Rollback is atomic per step: if one rollback
fails, the engine continues trying the remaining rollbacks (best
effort) and reports the partial state via ``RollbackError``.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.orchestrator.plan_models import PlanStep


class StepExecutor(Protocol):
    """Adapter contract the rollback engine uses to execute steps.

    Real adapters (modeling/report/cloud) implement this Protocol.
    Tests provide mock implementations that record calls and can be
    configured to succeed or fail.
    """

    async def execute_step(self, step: PlanStep) -> StepOutcome: ...


class StepOutcome(BaseModel):
    """Result of a single step execution (forward or rollback)."""

    success: bool
    error_message: str | None = None
    changed_files: list[str] = Field(default_factory=list)


class FailedRollback(BaseModel):
    """Details of a single rollback failure (per spec §2.5)."""

    step_id: str
    error_message: str
    paths_affected: list[str] = Field(default_factory=list)


class RollbackResult(BaseModel):
    """Outcome of a rollback operation."""

    status: str  # "rolled_back" | "partial" | "no_rollback"
    rolled_back_steps: list[str] = Field(default_factory=list)
    failed_rollbacks: list[FailedRollback] = Field(default_factory=list)


class RollbackError(Exception):
    """Raised when rollback fails partially (per spec §2.5).

    The exception carries the partial result so the caller can act
    (e.g. elicitate the user with paths to fix manually).
    """

    def __init__(self, message: str, result: RollbackResult) -> None:
        super().__init__(message)
        self.result = result


class NoRollbackAvailableError(Exception):
    """Raised when the failed step has no rollback_step and no preceding
    steps to revert."""


class RollbackEngine:
    """Executes plan rollback in inverse order.

    Per spec §2.5:
    - Inverse order (most recent first).
    - If a rollback fails: ``RollbackError`` with paths for manual fix.
    - Atomic per step: we try to fully complete each rollback before
      moving to the next, but we DO continue with remaining rollbacks
      even if one fails (best-effort to maximize recovered state).
    """

    def __init__(self, executor: StepExecutor) -> None:
        self._executor = executor

    async def execute_plan(
        self,
        executed_steps: list[PlanStep],
        failed_step: PlanStep,
    ) -> RollbackResult:
        """Roll back ``executed_steps`` (in reverse) after ``failed_step``.

        ``failed_step`` itself is rolled back FIRST if it has a
        ``rollback_step`` (because partial side effects may already be
        present). Then steps ``executed_steps[-1], ..., executed_steps[0]``
        are rolled back.

        The contract per spec §3.3 is that ``failed_step`` is NOT in
        ``executed_steps`` (it's the step that just failed and hasn't
        been added to the executed list). We defend against caller
        mistake by deduping the rollback queue by step id.

        Raises:
            NoRollbackAvailableError: nothing to roll back.
            RollbackError: at least one rollback failed.
        """
        queue: list[tuple[PlanStep, PlanStep]] = []
        seen: set[str] = set()

        if failed_step.rollback_step is not None:
            queue.append((failed_step, failed_step.rollback_step))
            seen.add(failed_step.id)

        for step in reversed(executed_steps):
            if step.rollback_step is None:
                continue
            if step.id in seen:
                continue
            queue.append((step, step.rollback_step))
            seen.add(step.id)

        if not queue:
            raise NoRollbackAvailableError(
                f"no rollback steps available for failed step "
                f"{failed_step.id!r}"
            )

        rolled: list[str] = []
        failed: list[FailedRollback] = []

        for original, rb in queue:
            outcome = await self._executor.execute_step(rb)
            if outcome.success:
                rolled.append(original.id)
            else:
                failed.append(
                    FailedRollback(
                        step_id=original.id,
                        error_message=outcome.error_message
                        or "rollback step failed without error message",
                        paths_affected=outcome.changed_files,
                    )
                )

        if not failed:
            return RollbackResult(
                status="rolled_back",
                rolled_back_steps=rolled,
                failed_rollbacks=[],
            )
        if rolled:
            return RollbackResult(
                status="partial",
                rolled_back_steps=rolled,
                failed_rollbacks=failed,
            )
        return RollbackResult(
            status="no_rollback",
            rolled_back_steps=[],
            failed_rollbacks=failed,
        )

    async def execute_single_step(self, step: PlanStep) -> StepOutcome:
        """Execute the rollback of a single step (used in tests + dry-runs)."""
        if step.rollback_step is None:
            return StepOutcome(
                success=False,
                error_message=f"step {step.id} has no rollback_step",
            )
        return await self._executor.execute_step(step.rollback_step)


def raise_if_partial(result: RollbackResult) -> None:
    """Convert a partial/failed RollbackResult into a RollbackError.

    Per spec §2.5: ``RollbackError`` carries the paths so the caller can
    elicit the user with the exact paths to fix manually.
    """
    if result.status == "rolled_back":
        return
    if result.status == "no_rollback":
        raise RollbackError(
            "rollback failed completely: no steps were reverted",
            result=result,
        )
    # partial
    failed_ids = ", ".join(f.step_id for f in result.failed_rollbacks)
    paths = sorted({p for f in result.failed_rollbacks for p in f.paths_affected})
    msg = (
        f"rollback partial: {len(result.rolled_back_steps)} step(s) reverted, "
        f"{len(result.failed_rollbacks)} failed ({failed_ids}). "
        f"Manual fix needed at: {paths or '<no paths reported>'}"
    )
    raise RollbackError(msg, result=result)
