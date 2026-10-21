"""Tests for the engine wiring that makes the callable-taking tools work.

``add_measure_with_validation`` and ``refactor_to_calculation_groups``
take a Python callable that an MCP client cannot inject. Over the
protocol they were always ``None``, so both tools could only report
that they had nothing to work with. The wiring builds them from the
engines the orchestrator has.

These tests cover the wiring itself: that it writes a real measure, that
it reads a real model back, and that it refuses a target outside the
workspace.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.orchestrator.engine_wiring import (
    build_calc_group_writer,
    build_measure_inspector,
    build_measure_writer,
    run_coroutine,
)

MODEL = {
    "model": {
        "tables": [
            {
                "name": "FactSales",
                "columns": [{"name": "TotalAmount"}],
                "measures": [
                    {
                        "name": "Total Sales",
                        "table": "FactSales",
                        "expression": "SUM(FactSales[TotalAmount])",
                    },
                    {
                        "name": "Order Count",
                        "table": "FactSales",
                        "expression": "COUNTROWS(FactSales)",
                    },
                ],
            }
        ]
    }
}


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(root))
    return root


@pytest.fixture()
def pbip(workspace: Path) -> Path:
    """A PBIP folder with one dataset holding two measures."""
    target = workspace / "sample.pbip"
    dataset = target / "sample.Dataset"
    dataset.mkdir(parents=True)
    (dataset / "definition.pbism").write_text(json.dumps(MODEL), encoding="utf-8")
    return target


@pytest.fixture()
def outside_dir() -> Iterator[Path]:
    """A directory outside the workspace root."""
    path = Path(tempfile.mkdtemp(prefix="pbi_outside_"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _measures(target: Path) -> list[dict]:
    model = json.loads((target / "sample.Dataset" / "definition.pbism").read_text(encoding="utf-8"))
    obj = model.get("model", model)
    return [m for t in obj.get("tables", []) for m in t.get("measures", [])]


class TestRunCoroutine:
    def test_returns_the_result(self) -> None:
        async def coro() -> str:
            return "done"

        assert run_coroutine(coro()) == "done"

    def test_runs_inside_an_already_running_loop(self) -> None:
        """The MCP server's tools run inside a loop; asyncio.run cannot."""

        async def outer() -> str:
            async def inner() -> str:
                return "nested"

            return run_coroutine(inner())

        import asyncio

        assert asyncio.run(outer()) == "nested"


class TestBuildMeasureWriter:
    def test_writes_the_measure_to_disk(self, pbip: Path) -> None:
        writer = build_measure_writer()
        result = writer(
            target=str(pbip),
            table="FactSales",
            name="Revenue",
            expression="SUM(FactSales[TotalAmount])",
        )

        assert result["error_message"] is None
        assert result["changed_files"]
        names = [m["name"] for m in _measures(pbip)]
        assert "Revenue" in names

    def test_persists_the_optional_attributes(self, pbip: Path) -> None:
        """A measure written without its format string is not the measure
        that was asked for, and the tool reports what it persisted."""
        writer = build_measure_writer()
        writer(
            target=str(pbip),
            table="FactSales",
            name="Revenue",
            expression="SUM(FactSales[TotalAmount])",
            format_string="#,##0.00",
            description="total revenue",
            is_hidden=True,
        )

        created = [m for m in _measures(pbip) if m["name"] == "Revenue"][0]
        assert created["formatString"] == "#,##0.00"
        assert created["description"] == "total revenue"
        assert created["isHidden"] is True

    def test_refuses_a_target_outside_the_workspace(
        self, workspace: Path, outside_dir: Path
    ) -> None:
        dataset = outside_dir / "evil.Dataset"
        dataset.mkdir(parents=True)
        (dataset / "definition.pbism").write_text(json.dumps(MODEL), encoding="utf-8")

        writer = build_measure_writer()
        # A target outside the workspace is a policy refusal, not an
        # engine failure: it raises rather than coming back as a failed
        # write, because nothing was attempted.
        with pytest.raises(ValueError, match="outside allowed workspace root"):
            writer(
                target=str(outside_dir),
                table="FactSales",
                name="Exfiltrated",
                expression="1",
            )

        # Nothing was written outside the workspace.
        assert "Exfiltrated" not in (dataset / "definition.pbism").read_text(encoding="utf-8")


class TestBuildMeasureInspector:
    def test_lists_the_measures_of_the_model(self, pbip: Path) -> None:
        inspector = build_measure_inspector(str(pbip))
        measures = inspector.list_measures()

        names = {m["name"] for m in measures}
        assert "Total Sales" in names
        assert "Order Count" in names
        first = [m for m in measures if m["name"] == "Total Sales"][0]
        assert first["expression"] == "SUM(FactSales[TotalAmount])"

    def test_refuses_a_target_outside_the_workspace(
        self, workspace: Path, outside_dir: Path
    ) -> None:
        with pytest.raises(ValueError, match="outside allowed workspace root"):
            build_measure_inspector(str(outside_dir))


class TestBuildCalcGroupWriter:
    def test_does_not_touch_the_files_without_auto_apply(self, pbip: Path) -> None:
        writer = build_calc_group_writer()
        result = run_coroutine(
            writer.refactor_to_calculation_groups(
                pbip_path=str(pbip),
                spec=[],
                auto_apply=False,
            )
        )

        assert result.success is True
        assert result.simulated is True
        assert result.changed_files == []

    def test_refuses_a_target_outside_the_workspace(
        self, workspace: Path, outside_dir: Path
    ) -> None:
        writer = build_calc_group_writer()
        with pytest.raises(ValueError, match="outside allowed workspace root"):
            run_coroutine(
                writer.refactor_to_calculation_groups(
                    pbip_path=str(outside_dir),
                    spec=[],
                    auto_apply=True,
                )
            )
