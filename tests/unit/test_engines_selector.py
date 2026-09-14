"""Tests for engines.selector — EngineSelector with graceful degradation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from powerbi_orchestrator_mcp.engines.base import (
    Column,
    ConnectionHandle,
    OperationResult,
    Table,
    ValidationResult,
)
from powerbi_orchestrator_mcp.engines.errors import (
    EngineNotFoundError,
)
from powerbi_orchestrator_mcp.engines.selector import (
    DEFAULT_MODELING_CHAIN,
    DEFAULT_REPORT_CHAIN,
    EngineSelector,
)
from powerbi_orchestrator_mcp.orchestrator.context import EngineStatus, Target

# ---------------------------------------------------------------------------
# Fake engines
# ---------------------------------------------------------------------------


class _FakeModelingEngine:
    """Minimal ModelingEngine stand-in for selector tests."""

    def __init__(self, name: str = "powerbi-modeling-mcp", version: str = "1.0.0") -> None:
        self._name = name
        self._version = version

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return self._version

    async def health_check(self) -> EngineStatus:
        return EngineStatus(
            name=self._name, available=True, version=self._version
        )

    async def connect(self, target: Target) -> ConnectionHandle:
        return ConnectionHandle(
            engine=self._name,
            target_type=target.target_type,
            target_ref=target.target_ref,
            session_token="x",
        )

    async def disconnect(self, conn: ConnectionHandle) -> None:
        pass

    async def list_tables(self, conn: ConnectionHandle) -> list[Table]:
        return []

    async def list_measures(self, conn: ConnectionHandle) -> list:
        return []

    async def list_columns(
        self, conn: ConnectionHandle, table: str
    ) -> list[Column]:
        return []

    async def list_relationships(self, conn: ConnectionHandle) -> list:
        return []

    async def update_column(
        self,
        conn: ConnectionHandle,
        table: str,
        column: str,
        changes: dict[str, Any],
    ) -> OperationResult:
        return OperationResult(success=True)

    async def create_measure(
        self, conn: ConnectionHandle, table: str, measure: Any
    ) -> OperationResult:
        return OperationResult(success=True)

    async def update_measure(
        self,
        conn: ConnectionHandle,
        table: str,
        measure: str,
        changes: dict[str, Any],
    ) -> OperationResult:
        return OperationResult(success=True)

    async def delete_measure(
        self, conn: ConnectionHandle, table: str, measure: str
    ) -> OperationResult:
        return OperationResult(success=True)

    async def execute_dax(
        self,
        conn: ConnectionHandle,
        query: str,
        effective_identity: dict[str, Any] | None = None,
    ) -> Any:
        return None

    async def snapshot(
        self, conn: ConnectionHandle, label: str
    ) -> Any:
        return None

    async def restore_snapshot(self, handle: Any) -> None:
        return None


class _FakeReportEngine:
    """Minimal ReportEngine stand-in for selector tests."""

    def __init__(self, name: str = "python_report", version: str = "1.0.0") -> None:
        self._name = name
        self._version = version

    @property
    def name(self) -> str:
        return self._name

    async def health_check(self) -> EngineStatus:
        return EngineStatus(
            name=self._name, available=True, version=self._version
        )

    async def connect(self, pbip_path: Path) -> ConnectionHandle:
        return ConnectionHandle(
            engine=self._name,
            target_type="pbip_folder",
            target_ref=str(pbip_path),
            session_token="x",
        )

    async def disconnect(self, conn: ConnectionHandle) -> None:
        pass

    async def add_page(
        self,
        conn: ConnectionHandle,
        page_name: str,
        layout: Any = None,
    ) -> OperationResult:
        return OperationResult(success=True)

    async def add_visual(
        self,
        conn: ConnectionHandle,
        page: str,
        visual_spec: Any,
    ) -> OperationResult:
        return OperationResult(success=True)

    async def update_visual(
        self,
        conn: ConnectionHandle,
        page: str,
        visual_id: str,
        changes: dict[str, Any],
    ) -> OperationResult:
        return OperationResult(success=True)

    async def propagate_rename(
        self,
        conn: ConnectionHandle,
        old_path: str,
        new_path: str,
        scope: str,
    ) -> OperationResult:
        return OperationResult(success=True)

    async def validate_pbir(self, conn: ConnectionHandle) -> ValidationResult:
        return ValidationResult(valid=True)


@pytest.fixture()
def target() -> Target:
    return Target(target_type="pbip_folder", target_ref="./x")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSelection:
    def test_picks_preferred_when_registered(self, target: Target) -> None:
        sel = EngineSelector()
        sel.register_modeling("powerbi-modeling-mcp", _FakeModelingEngine())
        engine = sel.select_modeling_engine("update_column", target)
        assert engine.name == "powerbi-modeling-mcp"

    def test_caches_selection(self, target: Target) -> None:
        sel = EngineSelector()
        sel.register_modeling("powerbi-modeling-mcp", _FakeModelingEngine())
        engine1 = sel.select_modeling_engine("update_column", target)
        engine2 = sel.select_modeling_engine("update_column", target)
        assert engine1 is engine2

    def test_cache_invalidated_by_unregister(self, target: Target) -> None:
        sel = EngineSelector()
        sel.register_modeling("powerbi-modeling-mcp", _FakeModelingEngine())
        sel.select_modeling_engine("update_column", target)  # populates cache
        sel.unregister("powerbi-modeling-mcp")
        with pytest.raises(EngineNotFoundError):
            sel.select_modeling_engine("update_column", target)

    def test_cache_key_includes_target_type(self, target: Target) -> None:
        sel = EngineSelector()
        sel.register_modeling("powerbi-modeling-mcp", _FakeModelingEngine())
        sel.select_modeling_engine("op", target)  # cache key: (op, pbip_folder)
        # Different target_type, second call should still find the engine.
        target2 = Target(target_type="fabric_workspace", target_ref="ws-1")
        engine = sel.select_modeling_engine("op", target2)
        assert engine.name == "powerbi-modeling-mcp"

    def test_generic_register_dispatches_to_modeling(self, target: Target) -> None:
        sel = EngineSelector()
        sel.register("powerbi-modeling-mcp", _FakeModelingEngine())
        # Should be in the modeling registry.
        engine = sel.select_modeling_engine("update_column", target)
        assert hasattr(engine, "list_tables") and hasattr(engine, "snapshot")

    def test_generic_register_dispatches_to_report(self, target: Target) -> None:
        sel = EngineSelector()
        sel.register("python_report", _FakeReportEngine())
        # Should be in the report registry.
        engine = sel.select_report_engine("propagate_rename", target)
        assert hasattr(engine, "add_page") and hasattr(engine, "add_visual")


class TestReportSelection:
    def test_picks_python_report_first(self, target: Target) -> None:
        sel = EngineSelector()
        sel.register_report("python_report", _FakeReportEngine("python_report"))
        sel.register_report("superbi-mcp", _FakeReportEngine("superbi-mcp"))
        engine = sel.select_report_engine("propagate_rename", target)
        assert engine.name == "python_report"

    def test_falls_back_to_superbi_mcp(self, target: Target) -> None:
        sel = EngineSelector()
        # Only superbi-mcp registered.
        sel.register_report("superbi-mcp", _FakeReportEngine("superbi-mcp"))
        engine = sel.select_report_engine("propagate_rename", target)
        assert engine.name == "superbi-mcp"

    def test_raises_when_no_report_engines(self, target: Target) -> None:
        sel = EngineSelector()
        with pytest.raises(EngineNotFoundError):
            sel.select_report_engine("propagate_rename", target)

    def test_plan_compatibility_kind_report(self, target: Target) -> None:
        sel = EngineSelector()
        sel.register_report("python_report", _FakeReportEngine())
        result = sel.plan_compatibility(
            [("propagate_rename", target)], kind="report"
        )
        assert result["ready"] is True


class TestNotRegistered:
    def test_raises_not_found_when_no_engines(self, target: Target) -> None:
        sel = EngineSelector()
        with pytest.raises(EngineNotFoundError):
            sel.select_modeling_engine("update_column", target)


class TestPlanCompatibility:
    def test_ready_when_all_engines_registered(self, target: Target) -> None:
        sel = EngineSelector()
        sel.register("powerbi-modeling-mcp", _FakeModelingEngine())
        result = sel.plan_compatibility(
            [
                ("update_column", target),
                ("create_measure", target),
            ]
        )
        assert result["ready"] is True
        assert result["missing"] == []

    def test_missing_lists_unregistered_ops(self, target: Target) -> None:
        sel = EngineSelector()
        # No engines registered.
        result = sel.plan_compatibility(
            [
                ("update_column", target),
                ("execute_dax", target),
            ]
        )
        assert result["ready"] is False
        assert "update_column" in result["missing"]
        assert "execute_dax" in result["missing"]


class TestAvailable:
    def test_available_returns_status_per_engine(self) -> None:
        sel = EngineSelector()
        sel.register("powerbi-modeling-mcp", _FakeModelingEngine())
        statuses = sel.available()
        assert "powerbi-modeling-mcp" in statuses
        assert statuses["powerbi-modeling-mcp"].available is True

    def test_available_handles_unhealthy_engine(self) -> None:
        class BrokenEngine(_FakeModelingEngine):
            async def health_check(self) -> EngineStatus:
                raise ValueError("boom")

        sel = EngineSelector()
        sel.register("broken", BrokenEngine("broken"))
        statuses = sel.available()
        assert statuses["broken"].available is False
        assert "boom" in (statuses["broken"].reason_unavailable or "")


class TestDefaultChain:
    def test_default_chain_prefers_modeling_mcp(self) -> None:
        # If the chain config changes, update tests + ADR.
        assert DEFAULT_MODELING_CHAIN[0] == "powerbi-modeling-mcp"

    def test_default_chain_falls_back_to_te(self) -> None:
        # As of Sprint 14A, Tabular Editor is the documented fallback
        # for powerbi-modeling-mcp.
        preferred, fallbacks = DEFAULT_MODELING_CHAIN
        assert preferred == "powerbi-modeling-mcp"
        assert "te" in fallbacks

    def test_default_report_chain_prefers_python_report(self) -> None:
        assert DEFAULT_REPORT_CHAIN[0] == "python_report"

    def test_default_report_chain_falls_back_to_superbi_mcp(self) -> None:
        preferred, fallbacks = DEFAULT_REPORT_CHAIN
        assert preferred == "python_report"
        assert "superbi-mcp" in fallbacks
