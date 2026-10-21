"""Build the callables that two tools need and cannot be given over MCP.

``add_measure_with_validation`` wants a ``measure_writer`` and
``refactor_to_calculation_groups`` wants an ``inspector`` and a
``measure_writer``. Those are Python callables, and an MCP client cannot
inject one: over the protocol they were always ``None``, so the first
tool answered "no measure_writer available; measure was NOT persisted"
and the second "no inspector provided — cannot enumerate measures".

Both failed honestly, which is what the fix for the old silent-success
bug was supposed to achieve. But neither could do its job: a tool that
can only report failure is not a working tool.

This module builds those callables from the engines the orchestrator
actually has, through the same selection the plan path uses — a real
engine when one is installed, the built-in adapter otherwise — so the
tools work over MCP the same way they work in a plan.

The engines are asynchronous and the tools are synchronous, so the
coroutine runs on a worker thread with its own event loop. The whole
engine interaction (connect, operate, disconnect) happens on that one
loop: a subprocess engine's session is bound to the loop that started
it, and splitting the interaction across two loops would break it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol, TypeVar

T = TypeVar("T")

__all__ = [
    "CalcGroupWriter",
    "MeasureInspector",
    "MeasureWriter",
    "build_calc_group_writer",
    "build_measure_inspector",
    "build_measure_writer",
    "run_coroutine",
]


def run_coroutine(coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine to completion from synchronous code.

    The MCP server runs every tool inside an event loop, so
    ``asyncio.run()`` cannot be called from a tool body — it raises
    "asyncio.run() cannot be called from a running event loop". A worker
    thread with its own loop can.
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class MeasureWriter(Protocol):
    """The ``measure_writer`` contract ``add_measure_with_validation`` uses."""

    def __call__(
        self,
        *,
        target: str,
        table: str,
        name: str,
        expression: str,
        format_string: str | None = None,
        description: str | None = None,
        is_hidden: bool = False,
    ) -> dict[str, Any]: ...


class MeasureInspector(Protocol):
    """The ``inspector`` contract ``refactor_to_calculation_groups`` uses."""

    def list_measures(self) -> list[dict[str, Any]]: ...


class CalcGroupWriter(Protocol):
    """The calc-group writer contract of ``refactor_to_calculation_groups``.

    The tool checks for this method by name and, when it finds it, hands
    the engine the full spec (skeleton plus items) and merges the engine's
    own remappings back into the result.
    """

    async def refactor_to_calculation_groups(
        self,
        pbip_path: str,
        spec: list[Any],
        *,
        auto_apply: bool,
    ) -> Any: ...


# ---------------------------------------------------------------------------
# Engine plumbing
# ---------------------------------------------------------------------------


def _select_modeling_engine(target_ref: str, operation: str) -> Any:
    """Pick the modeling engine for ``target_ref``.

    Same selection the plan path uses: the first available engine in the
    chain, which ends with the built-in adapter that always works on the
    files on disk.
    """
    from powerbi_orchestrator_mcp.engines.registry import build_default_selector
    from powerbi_orchestrator_mcp.orchestrator.context import Target

    target = Target(
        target_type="pbip_folder",
        target_ref=target_ref,
        auth_mode="interactive",
    )
    return build_default_selector().select_modeling_engine(operation, target)


async def _with_connection(target_ref: str, operation: str, run: Any) -> Any:
    """Connect a modeling engine, run ``run(engine, conn)``, disconnect."""
    engine = _select_modeling_engine(target_ref, operation)
    conn = await engine.connect(
        _target_for(target_ref),
    )
    try:
        return await run(engine, conn)
    finally:
        await engine.disconnect(conn)


def _target_for(target_ref: str) -> Any:
    from powerbi_orchestrator_mcp.orchestrator.context import Target

    return Target(
        target_type="pbip_folder",
        target_ref=target_ref,
        auth_mode="interactive",
    )


def _validated_target(target: str) -> str:
    """Bound a target the engine is about to write in to the workspace.

    The engine writes wherever it is told, and the path arrives from the
    caller. Validating it as a **write** keeps it inside the workspace
    whatever the read opt-out says — the same rule the plan path follows
    before it hands a path to an engine.
    """
    from powerbi_orchestrator_mcp.validation.path_safety import validate_write_path

    return str(validate_write_path(target, must_exist=False))


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def build_measure_writer() -> MeasureWriter:
    """A measure writer backed by the orchestrator's modeling engine."""

    def write_measure(
        *,
        target: str,
        table: str,
        name: str,
        expression: str,
        format_string: str | None = None,
        description: str | None = None,
        is_hidden: bool = False,
    ) -> dict[str, Any]:
        from powerbi_orchestrator_mcp.engines.base import Measure

        # A target outside the workspace raises: it is a policy refusal,
        # not an engine failure, and it must not be reported as "the
        # write failed" — nothing was attempted.
        target_ref = _validated_target(target)
        measure = Measure(
            name=name,
            table=table,
            expression=expression,
            format_string=format_string,
            description=description,
            is_hidden=is_hidden,
        )

        async def run(engine: Any, conn: Any) -> Any:
            return await engine.create_measure(conn, table, measure)

        try:
            result = run_coroutine(
                _with_connection(target_ref, "create_measure", run),
            )
        except Exception as exc:  # noqa: BLE001
            # The engine failed. The tool surfaces this as the error
            # message of a failed write. Raising would take the whole
            # call down; the caller needs to know *what* failed.
            return {
                "changed_files": [],
                "error_message": f"measure write failed: {exc}",
            }
        return {
            "changed_files": list(result.changed_files),
            "error_message": result.error_message,
        }

    return write_measure


def build_measure_inspector(target: str) -> MeasureInspector:
    """An inspector that lists the measures of the model at ``target``.

    Validated as a write: the refactor tool may write to the model it
    inspects, so the model has to be inside the workspace either way.
    """

    class _EngineInspector:
        def __init__(self, target_ref: str) -> None:
            self._target_ref = target_ref

        def list_measures(self) -> list[dict[str, Any]]:
            async def run(engine: Any, conn: Any) -> Any:
                return await engine.list_measures(conn)

            measures = run_coroutine(
                _with_connection(self._target_ref, "list_measures", run),
            )
            out: list[dict[str, Any]] = []
            for m in measures:
                if hasattr(m, "model_dump"):
                    out.append(m.model_dump())
                elif isinstance(m, dict):
                    out.append(dict(m))
                else:  # pragma: no cover - an engine with a third shape
                    out.append(
                        {
                            "name": getattr(m, "name", ""),
                            "table": getattr(m, "table", ""),
                            "expression": getattr(m, "expression", ""),
                        }
                    )
            return out

    return _EngineInspector(_validated_target(target))


def build_calc_group_writer() -> CalcGroupWriter:
    """A calc-group writer backed by the orchestrator's modeling engine.

    The tool looks for a ``refactor_to_calculation_groups`` method by
    name and, when it finds one, hands it the full spec — the skeleton
    and the items — instead of the narrower legacy contract. The engine's
    own remappings come back and are merged into the result.
    """

    class _EngineCalcGroupWriter:
        async def refactor_to_calculation_groups(
            self,
            pbip_path: str,
            spec: list[Any],
            *,
            auto_apply: bool,
        ) -> Any:
            from powerbi_orchestrator_mcp.engines.te_adapter import (
                RefactorCalcGroupsResult,
            )

            target_ref = _validated_target(pbip_path)
            if not auto_apply:
                return RefactorCalcGroupsResult(
                    success=True,
                    changed_files=[],
                    simulated=True,
                    error_message=None,
                )

            async def run(engine: Any, _conn: Any) -> Any:
                # The calc-group operation addresses the model by path
                # rather than through the connection handle.
                return await engine.refactor_to_calculation_groups(
                    pbip_path=target_ref,
                    spec=spec,
                    auto_apply=auto_apply,
                )

            try:
                result = await _with_connection(
                    target_ref,
                    "refactor_to_calculation_groups",
                    run,
                )
            except Exception as exc:  # noqa: BLE001
                return RefactorCalcGroupsResult(
                    success=False,
                    changed_files=[],
                    error_message=f"calc group refactor failed: {exc}",
                )
            return result

    return _EngineCalcGroupWriter()
