"""The engines the orchestrator can actually run.

`engine_detector` discovers what is installed; this module builds the
adapters for those engines and hands them to the `EngineSelector`.

Until now the real adapters — `PowerBiModelingMcpEngine`,
`TabularEditorAdapter` and `SuperBiMcpEngine` — were defined but never
instantiated anywhere in `src/`. The selector was therefore always
empty, every selection raised "no registered engine", and the plan
path silently degraded to the in-memory adapter with no signal that it
had.

The chain ends with the built-in adapters, which are always available
and work on the files on disk. When one of them serves an operation,
the step says so: a degraded path is a fact the caller needs, not a
detail to hide.

Tabular Editor is not in the modeling chain: its adapter implements
the specification operations (`apply_model_spec`, `refactor_to_calc_groups`)
rather than the `ModelingEngine` protocol, so it cannot serve a plan's
modeling steps. It is reached through the BPA runner, which is where
it is actually used.
"""

from __future__ import annotations

from typing import Any

from powerbi_orchestrator_mcp.engines.base import ModelingEngine, ReportEngine

# The built-in modeling adapter. Always available: it reads and writes
# the PBIP on disk with no subprocess and no external dependency.
BUILTIN_MODELING_ENGINE = "python_modeling"
BUILTIN_REPORT_ENGINE = "python_report"


def build_modeling_engines() -> dict[str, ModelingEngine]:
    """Build the modeling adapters, in the order the chain prefers them.

    The subprocess engines are only offered when they are actually
    installed — see `engine_detector`: the orchestrator does not
    install engines by itself.
    """
    from powerbi_orchestrator_mcp.engines.modeling_mcp import (
        PowerBiModelingMcpEngine,
    )
    from powerbi_orchestrator_mcp.engines.te_adapter import (
        InMemoryModelingAdapter,
    )

    engines: dict[str, ModelingEngine] = {
        BUILTIN_MODELING_ENGINE: InMemoryModelingAdapter(),
    }
    # The Microsoft modeling MCP server, when it is installed.
    modeling_mcp = PowerBiModelingMcpEngine()
    if modeling_mcp.is_available():
        engines["powerbi-modeling-mcp"] = modeling_mcp
    return engines


def build_report_engines() -> dict[str, ReportEngine]:
    """Build the report adapters, in the order the chain prefers them."""
    from powerbi_orchestrator_mcp.engines.report_python import PythonReportEngine
    from powerbi_orchestrator_mcp.engines.superbi_mcp import SuperBiMcpEngine

    engines: dict[str, ReportEngine] = {
        BUILTIN_REPORT_ENGINE: PythonReportEngine(),
    }
    superbi = SuperBiMcpEngine()
    if superbi.is_available():
        engines["superbi-mcp"] = superbi
    return engines


def build_default_selector() -> Any:
    """Build an `EngineSelector` populated with the available engines."""
    from powerbi_orchestrator_mcp.engines.selector import EngineSelector

    return EngineSelector(
        modeling_engines=build_modeling_engines(),
        report_engines=build_report_engines(),
    )


def is_builtin_engine(engine_name: str) -> bool:
    """True when the engine is one of the orchestrator's built-in ones."""
    return engine_name in {BUILTIN_MODELING_ENGINE, BUILTIN_REPORT_ENGINE}


__all__ = [
    "BUILTIN_MODELING_ENGINE",
    "BUILTIN_REPORT_ENGINE",
    "build_default_selector",
    "build_modeling_engines",
    "build_report_engines",
    "is_builtin_engine",
]
