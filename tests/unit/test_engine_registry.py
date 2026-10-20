"""Tests for the engine registry — the adapters the orchestrator can run.

Until now the real adapters were defined but never instantiated, so the
selector was always empty and every plan silently degraded to the
built-in adapter. These tests pin that the real adapters are registered
when they are installed, and that a step says which engine served it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from powerbi_orchestrator_mcp.engines.registry import (
    BUILTIN_MODELING_ENGINE,
    BUILTIN_REPORT_ENGINE,
    build_default_selector,
    build_modeling_engines,
    build_report_engines,
    is_builtin_engine,
)
from powerbi_orchestrator_mcp.orchestrator.plan_models import PlanStep
from powerbi_orchestrator_mcp.orchestrator.step_executor import (
    ModelingStepExecutor,
    ReportStepExecutor,
)


class TestRegistry:
    def test_the_builtin_modeling_engine_is_always_available(self) -> None:
        engines = build_modeling_engines()
        assert BUILTIN_MODELING_ENGINE in engines

    def test_the_builtin_report_engine_is_always_available(self) -> None:
        engines = build_report_engines()
        assert BUILTIN_REPORT_ENGINE in engines

    def test_the_selector_is_populated(self) -> None:
        selector = build_default_selector()
        assert selector._modeling  # noqa: SLF001
        assert selector._report  # noqa: SLF001

    def test_the_real_modeling_engine_is_registered_when_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The modeling MCP engine joins the registry once it is installed."""
        import powerbi_orchestrator_mcp.engines.modeling_mcp as modeling_mcp

        monkeypatch.setattr(
            modeling_mcp.PowerBiModelingMcpEngine,
            "is_available",
            lambda self: True,
        )
        engines = build_modeling_engines()
        assert "powerbi-modeling-mcp" in engines

    def test_the_npm_engines_are_not_offered_when_the_package_is_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`npx` alone does not make an npm engine available.

        The adapter launches the package with `npx -y`, which downloads
        it when it is missing. The orchestrator does not install engines
        by itself, so an uninstalled package is reported as absent.
        """
        import shutil

        import powerbi_orchestrator_mcp.engines.modeling_mcp as modeling_mcp
        import powerbi_orchestrator_mcp.engines.superbi_mcp as superbi_mcp

        def fake_which(name: str) -> str | None:
            # The launcher is present; the package is not.
            return "/usr/bin/npx" if name in ("npx",) else None

        monkeypatch.setattr(shutil, "which", fake_which)

        assert modeling_mcp.PowerBiModelingMcpEngine().is_available() is False
        assert superbi_mcp.SuperBiMcpEngine().is_available() is False

    def test_the_npm_engine_is_available_once_the_package_is_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import shutil

        import powerbi_orchestrator_mcp.engines.modeling_mcp as modeling_mcp

        def fake_which(name: str) -> str | None:
            return f"/usr/bin/{name}" if name in ("npx", "powerbi-modeling-mcp") else None

        monkeypatch.setattr(shutil, "which", fake_which)

        assert modeling_mcp.PowerBiModelingMcpEngine().is_available() is True

    def test_builtin_engines_are_recognised(self) -> None:
        assert is_builtin_engine(BUILTIN_MODELING_ENGINE) is True
        assert is_builtin_engine(BUILTIN_REPORT_ENGINE) is True
        assert is_builtin_engine("powerbi-modeling-mcp") is False


class _FakeEngine:
    name = "fake-engine"
    version = "0.0.1"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def connect(self, target: Any) -> Any:
        self.calls.append("connect")
        return "conn"

    async def disconnect(self, conn: Any) -> None:
        self.calls.append("disconnect")

    async def update_column(self, conn: Any, table: str, column: str, changes: Any) -> Any:
        from powerbi_orchestrator_mcp.engines.base import OperationResult

        self.calls.append("update_column")
        return OperationResult(success=True, changed_files=["model.bim"])


class TestStepsReportTheirEngine:
    @pytest.mark.asyncio
    async def test_the_modeling_step_says_which_engine_served_it(self) -> None:
        engine = _FakeEngine()
        executor = ModelingStepExecutor(engine=engine)
        step = PlanStep(
            id="p1:s1",
            engine="modeling",
            action="column.update",
            args={"old_path": "T[A]", "new_name": "B"},
        )

        outcome = await executor.execute_step(step)

        assert outcome.success is True
        assert outcome.details["engine"] == "fake-engine"
        assert outcome.details["builtin_engine"] is False

    @pytest.mark.asyncio
    async def test_the_report_step_says_which_engine_served_it(self, tmp_path: Path) -> None:
        class _FakeReportEngine(_FakeEngine):
            async def propagate_rename(
                self, conn: Any, old_path: str, new_path: str, scope: str
            ) -> Any:
                from powerbi_orchestrator_mcp.engines.base import OperationResult

                return OperationResult(success=True, changed_files=["page.json"])

        executor = ReportStepExecutor(engine=_FakeReportEngine())
        step = PlanStep(
            id="p1:s2",
            engine="report",
            action="propagate_rename",
            args={"old_path": "T[A]", "new_path": "T[B]", "pbip_path": str(tmp_path)},
        )

        outcome = await executor.execute_step(step)

        assert outcome.success is True
        assert outcome.details["engine"] == "fake-engine"

    @pytest.mark.asyncio
    async def test_the_builtin_engine_is_flagged_as_degraded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A step served by the built-in adapter says so."""
        import powerbi_orchestrator_mcp.orchestrator.step_executor as step_executor

        class _BuiltinEngine(_FakeEngine):
            name = "python_modeling"

        monkeypatch.setattr(
            step_executor.ModelingStepExecutor,
            "_get_engine",
            lambda self, target, operation="column.update": _BuiltinEngine(),
        )
        executor = ModelingStepExecutor()
        step = PlanStep(
            id="p1:s1",
            engine="modeling",
            action="column.update",
            args={"old_path": "T[A]", "new_name": "B"},
        )

        outcome = await executor.execute_step(step)

        assert outcome.details["engine"] == "python_modeling"
        assert outcome.details["builtin_engine"] is True
