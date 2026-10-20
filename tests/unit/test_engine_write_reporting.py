"""Tests for what the engine adapters report back.

An adapter that says a write happened when the engine refused it, or
that a report is valid when the engine returned no verdict, turns a
failure into a green result. These tests pin the reporting contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.engines.base import ConnectionHandle, Measure
from powerbi_orchestrator_mcp.engines.errors import (
    EngineCrashedError,
    EngineMethodNotFoundError,
    EngineTimeoutError,
)
from powerbi_orchestrator_mcp.engines.modeling_mcp import PowerBiModelingMcpEngine
from powerbi_orchestrator_mcp.engines.superbi_mcp import SuperBiMcpEngine


def _conn() -> ConnectionHandle:
    return ConnectionHandle(
        engine="test",
        target_type="pbip_folder",
        target_ref="./x",
        session_token="x",
    )


def _modeling(responses: dict[str, object]) -> PowerBiModelingMcpEngine:
    return PowerBiModelingMcpEngine(
        binary="/bin/echo",
        version="1.0.0",
        mock_responses=responses,
    )


def _superbi(responses: dict[str, object]) -> SuperBiMcpEngine:
    return SuperBiMcpEngine(
        binary="/bin/echo",
        version="1.5.0",
        mock_responses=responses,
    )


class TestModelingWritesReportTheEnginesVerdict:
    async def test_a_refused_write_is_a_failure(self) -> None:
        engine = _modeling(
            {
                "column_operations/update": {
                    "success": False,
                    "error": "column not found",
                }
            }
        )

        result = await engine.update_column(_conn(), "Customer", "ID", {"new_name": "X"})

        assert result.success is False
        assert result.error_message == "column not found"

    async def test_a_refused_measure_create_is_a_failure(self) -> None:
        engine = _modeling(
            {"measure_operations/create": {"success": False, "message": "measure exists"}}
        )

        result = await engine.create_measure(
            _conn(), "FactSales", Measure(name="YTD", table="FactSales", expression="X")
        )

        assert result.success is False
        assert result.error_message == "measure exists"

    async def test_a_refused_measure_delete_is_a_failure(self) -> None:
        engine = _modeling({"measure_operations/delete": {"success": False}})

        result = await engine.delete_measure(_conn(), "FactSales", "Total Sales")

        assert result.success is False
        assert result.error_message

    async def test_an_accepted_write_is_a_success(self) -> None:
        engine = _modeling({"column_operations/update": {"changed_files": ["model.tmdl"]}})

        result = await engine.update_column(_conn(), "Customer", "ID", {"new_name": "X"})

        assert result.success is True
        assert result.changed_files == ["model.tmdl"]


class TestSuperbiWritesReportTheEnginesVerdict:
    async def test_a_refused_write_is_a_failure(self) -> None:
        engine = _superbi({"report/add_page": {"success": False, "error": "nope"}})

        result = await engine.add_page(_conn(), "NewPage")

        assert result.success is False

    async def test_a_validation_without_a_verdict_is_not_a_pass(self) -> None:
        """A validation that returned no verdict has proved nothing."""
        engine = _superbi({"report/validate": {"findings": []}})

        result = await engine.validate_pbir(_conn())

        assert result.valid is False

    async def test_a_validation_that_reports_valid_is_a_pass(self) -> None:
        engine = _superbi({"report/validate": {"valid": True, "findings": []}})

        result = await engine.validate_pbir(_conn())

        assert result.valid is True


class TestSuperbiDoesNotHideFailures:
    async def test_a_crash_is_not_hidden_behind_the_builtin_engine(self, tmp_path: Path) -> None:
        """Falling back to the built-in engine is for a missing method only.

        A crash, a timeout or a contract error is a real problem: it
        must not be reported as a successful write by the built-in
        engine.
        """
        engine = _superbi({})

        async def raise_crash(*_a: object, **_k: object) -> dict[str, object]:
            raise EngineCrashedError(
                "superbi-mcp exited immediately",
                engine="superbi-mcp",
                code="engine_exited_early",
                remediation_hint="check the engine logs",
            )

        engine._dispatch = raise_crash  # type: ignore[method-assign]  # noqa: SLF001

        with pytest.raises(EngineCrashedError):
            await engine.add_page(_conn(), "NewPage")

    async def test_a_timeout_is_not_hidden_behind_the_builtin_engine(self) -> None:
        engine = _superbi({})

        async def raise_timeout(*_a: object, **_k: object) -> dict[str, object]:
            raise EngineTimeoutError(
                "superbi-mcp timed out",
                engine="superbi-mcp",
                remediation_hint="increase the timeout",
                timeout_s=30,
            )

        engine._dispatch = raise_timeout  # type: ignore[method-assign]  # noqa: SLF001

        with pytest.raises(EngineTimeoutError):
            await engine.add_page(_conn(), "NewPage")

    async def test_a_missing_method_does_fall_back(self, tmp_path: Path) -> None:
        """The documented fallback still works."""
        pbip = tmp_path / "sample.pbip"
        pbip.mkdir()
        (pbip / "sample.pbip").write_text("{}", encoding="utf-8")
        report = pbip / "sample.Report"
        report.mkdir()
        (report / "report.json").write_text("{}", encoding="utf-8")
        engine = _superbi({})

        async def raise_method_not_found(*_a: object, **_k: object) -> dict[str, object]:
            raise EngineMethodNotFoundError(
                "superbi-mcp does not implement report/add_page()",
                engine="superbi-mcp",
                code="engine_method_not_found",
                remediation_hint="use the built-in engine",
            )

        engine._dispatch = raise_method_not_found  # type: ignore[method-assign]  # noqa: SLF001

        result = await engine.add_page(
            ConnectionHandle(
                engine="superbi-mcp",
                target_type="pbip_folder",
                target_ref=str(pbip),
                session_token="x",
            ),
            "NewPage",
        )

        # The built-in engine served the call.
        assert result.success is True
        assert (report / "pages" / "NewPage" / "page.json").exists()
