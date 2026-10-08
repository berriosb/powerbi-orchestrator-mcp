"""Coverage for the model-write paths of step_executor and te_adapter.

These are the highest-risk paths in the codebase: they mutate a semantic
model on disk. They were previously exercised only indirectly, so a silent
failure here would not have been caught by the suite.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from powerbi_orchestrator_mcp.engines.te_adapter import InMemoryModelingAdapter
from powerbi_orchestrator_mcp.orchestrator.context import Target
from powerbi_orchestrator_mcp.orchestrator.plan_models import PlanStep
from powerbi_orchestrator_mcp.orchestrator.step_executor import (
    ModelingStepExecutor,
    ReportStepExecutor,
)


def _pbism_model(tmp_path: Path) -> Path:
    """Create a PBIP tree with a two-table model."""
    dataset_dir = tmp_path / "model.Dataset"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    model_file = dataset_dir / "definition.pbism"
    model_data = {
        "model": {
            "tables": [
                {"name": "Sales", "measures": []},
                {"name": "Products", "measures": []},
            ]
        }
    }
    model_file.write_text(json.dumps(model_data), encoding="utf-8")
    return model_file


def _tmdl_model(tmp_path: Path) -> Path:
    """Create a PBIP tree with a TMDL table definition.

    The layout must match what the adapter globs for:
    ``<name>.Dataset/definition/tables/*.tmdl``.
    """
    tables_dir = tmp_path / "tmdl.Dataset" / "definition" / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    tmdl = tables_dir / "Sales.tmdl"
    tmdl.write_text("table Sales\n\tcolumn Amount\n", encoding="utf-8")
    return tmdl


def _executor() -> ModelingStepExecutor:
    return ModelingStepExecutor(engine=InMemoryModelingAdapter())


def _step(action: str, **args: Any) -> PlanStep:
    return PlanStep(id=f"s-{action}", engine="modeling", action=action, args=args)


# ---------------------------------------------------------------------------
# step_executor — measure write dispatch
# ---------------------------------------------------------------------------


class TestStepExecutorMeasureWrites:
    @pytest.mark.asyncio
    async def test_create_measure_persists_to_disk(self, tmp_path: Path) -> None:
        model_file = _pbism_model(tmp_path)
        out = await _executor().execute_step(
            _step(
                "measure.create",
                pbip_path=str(tmp_path),
                table="Sales",
                name="Total Sales",
                expression="SUM(Sales[Amount])",
            )
        )
        assert out.success is True, out.error_message
        assert str(model_file) in out.changed_files
        written = json.loads(model_file.read_text(encoding="utf-8"))
        sales = next(t for t in written["model"]["tables"] if t["name"] == "Sales")
        assert [m["name"] for m in sales["measures"]] == ["Total Sales"]
        assert sales["measures"][0]["expression"] == "SUM(Sales[Amount])"

    @pytest.mark.asyncio
    async def test_create_measure_into_tmdl(self, tmp_path: Path) -> None:
        tmdl = _tmdl_model(tmp_path)
        out = await _executor().execute_step(
            _step(
                "measure.create",
                pbip_path=str(tmp_path),
                table="Sales",
                name="Total Amount",
                expression="SUM(Sales[Amount])",
            )
        )
        assert out.success is True, out.error_message
        assert str(tmdl) in out.changed_files
        assert "measure 'Total Amount'" in tmdl.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_create_measure_unknown_table_fails(self, tmp_path: Path) -> None:
        """A write to a table that does not exist must fail, not silently no-op."""
        _pbism_model(tmp_path)
        out = await _executor().execute_step(
            _step(
                "measure.create",
                pbip_path=str(tmp_path),
                table="GhostTable",
                name="X",
                expression="SUM(GhostTable[A])",
            )
        )
        assert out.success is False
        assert out.error_message is not None
        assert "GhostTable" in out.error_message
        assert out.changed_files == []

    @pytest.mark.asyncio
    async def test_update_measure_rewrites_expression(self, tmp_path: Path) -> None:
        model_file = _pbism_model(tmp_path)
        data = json.loads(model_file.read_text(encoding="utf-8"))
        data["model"]["tables"][0]["measures"] = [
            {"name": "Total Sales", "table": "Sales", "expression": "SUM(Sales[Amount])"}
        ]
        model_file.write_text(json.dumps(data), encoding="utf-8")

        out = await _executor().execute_step(
            _step(
                "measure.update",
                pbip_path=str(tmp_path),
                table="Sales",
                name="Total Sales",
                changes={"expression": "DIVIDE(SUM(Sales[Amount]), 100)"},
            )
        )
        assert out.success is True, out.error_message
        written = json.loads(model_file.read_text(encoding="utf-8"))
        sales = written["model"]["tables"][0]
        assert "DIVIDE" in sales["measures"][0]["expression"]

    @pytest.mark.asyncio
    async def test_update_measure_rename(self, tmp_path: Path) -> None:
        model_file = _pbism_model(tmp_path)
        data = json.loads(model_file.read_text(encoding="utf-8"))
        data["model"]["tables"][0]["measures"] = [
            {"name": "Total Sales", "table": "Sales", "expression": "SUM(Sales[Amount])"}
        ]
        model_file.write_text(json.dumps(data), encoding="utf-8")

        out = await _executor().execute_step(
            _step(
                "measure.update",
                pbip_path=str(tmp_path),
                table="Sales",
                name="Total Sales",
                changes={"new_name": "Revenue"},
            )
        )
        assert out.success is True, out.error_message
        written = json.loads(model_file.read_text(encoding="utf-8"))
        names = [m["name"] for m in written["model"]["tables"][0]["measures"]]
        assert "Revenue" in names

    @pytest.mark.asyncio
    async def test_update_measure_unknown_fails(self, tmp_path: Path) -> None:
        _pbism_model(tmp_path)
        out = await _executor().execute_step(
            _step(
                "measure.update",
                pbip_path=str(tmp_path),
                table="Sales",
                name="Nonexistent",
                changes={"expression": "1"},
            )
        )
        assert out.success is False
        assert out.error_message is not None

    @pytest.mark.asyncio
    async def test_delete_measure_removes_from_model(self, tmp_path: Path) -> None:
        model_file = _pbism_model(tmp_path)
        data = json.loads(model_file.read_text(encoding="utf-8"))
        data["model"]["tables"][0]["measures"] = [
            {"name": "Total Sales", "table": "Sales", "expression": "SUM(Sales[Amount])"},
            {"name": "Total Qty", "table": "Sales", "expression": "SUM(Sales[Qty])"},
        ]
        model_file.write_text(json.dumps(data), encoding="utf-8")

        out = await _executor().execute_step(
            _step("measure.delete", pbip_path=str(tmp_path), table="Sales", name="Total Sales")
        )
        assert out.success is True, out.error_message
        written = json.loads(model_file.read_text(encoding="utf-8"))
        names = [m["name"] for m in written["model"]["tables"][0]["measures"]]
        assert names == ["Total Qty"]

    @pytest.mark.asyncio
    async def test_delete_measure_unknown_fails(self, tmp_path: Path) -> None:
        _pbism_model(tmp_path)
        out = await _executor().execute_step(
            _step("measure.delete", pbip_path=str(tmp_path), table="Sales", name="Nonexistent")
        )
        assert out.success is False
        assert out.error_message is not None


# ---------------------------------------------------------------------------
# step_executor — snapshot / restore
# ---------------------------------------------------------------------------


class TestStepExecutorSnapshot:
    @pytest.mark.asyncio
    async def test_snapshot_writes_handle_file(self, tmp_path: Path) -> None:
        _pbism_model(tmp_path)
        out = await _executor().execute_step(
            _step("snapshot", pbip_path=str(tmp_path), label="before_change")
        )
        assert out.success is True, out.error_message
        assert out.changed_files
        assert Path(out.changed_files[0]).exists()

    @pytest.mark.asyncio
    async def test_restore_snapshot_roundtrip(self, tmp_path: Path) -> None:
        model_file = _pbism_model(tmp_path)
        executor = _executor()

        snap = await executor.execute_step(_step("snapshot", pbip_path=str(tmp_path), label="pre"))
        assert snap.success is True, snap.error_message

        # Mutate the model after the snapshot.
        data = json.loads(model_file.read_text(encoding="utf-8"))
        data["model"]["tables"][0]["measures"] = [
            {"name": "Injected", "table": "Sales", "expression": "1"}
        ]
        model_file.write_text(json.dumps(data), encoding="utf-8")

        restored = await executor.execute_step(
            _step(
                "restore_snapshot",
                pbip_path=str(tmp_path),
                label="pre",
                path=snap.changed_files[0],
            )
        )
        assert restored.success is True, restored.error_message
        written = json.loads(model_file.read_text(encoding="utf-8"))
        assert written["model"]["tables"][0].get("measures") == []

    @pytest.mark.asyncio
    async def test_connect_failure_is_reported(self, tmp_path: Path) -> None:
        class _BrokenEngine(InMemoryModelingAdapter):
            async def connect(self, target: Target) -> Any:  # type: ignore[override]
                raise RuntimeError("engine unavailable")

        out = await ModelingStepExecutor(engine=_BrokenEngine()).execute_step(
            _step("snapshot", pbip_path=str(tmp_path))
        )
        assert out.success is False
        assert "engine unavailable" in (out.error_message or "")


# ---------------------------------------------------------------------------
# ReportStepExecutor — report write dispatch
# ---------------------------------------------------------------------------


class _StubReportEngine:
    """Minimal report engine capturing the dispatch the executor performs."""

    def __init__(self, *, fail: str | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._fail = fail

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        if self._fail == name:
            raise RuntimeError(f"{name} exploded")

    async def add_page(self, conn: Any, page_name: str, layout: Any) -> Any:
        self._record("add_page", page_name)
        return _Op(success=True, changed_files=[f"{page_name}/page.json"])

    async def add_visual(self, conn: Any, page: str, spec: Any) -> Any:
        self._record("add_visual", page, spec)
        return _Op(success=True, changed_files=[f"{page}/visuals.json"])

    async def update_visual(self, conn: Any, page: str, visual_id: str, changes: Any) -> Any:
        self._record("update_visual", page, visual_id)
        return _Op(success=True, changed_files=[f"{page}/visuals.json"])

    async def propagate_rename(self, conn: Any, old_path: str, new_path: str, scope: str) -> Any:
        self._record("propagate_rename", old_path, new_path, scope)
        return _Op(success=True, changed_files=["report.json"])

    async def validate_pbir(self, conn: Any) -> Any:
        self._record("validate_pbir")
        return type("V", (), {"valid": True})()

    async def disconnect(self, conn: Any) -> None:
        self.calls.append(("disconnect", ()))


class _Op:
    def __init__(self, *, success: bool, changed_files: list[str]) -> None:
        self.success = success
        self.error_message = None
        self.changed_files = changed_files


class TestReportStepExecutor:
    @pytest.mark.asyncio
    async def test_add_page_dispatch(self, tmp_path: Path) -> None:
        engine = _StubReportEngine()
        out = await ReportStepExecutor(engine=engine).execute_step(
            _step("add_page", pbip_path=str(tmp_path), page_name="Executive")
        )
        assert out.success is True, out.error_message
        assert out.changed_files == ["Executive/page.json"]
        assert any(c[0] == "add_page" and c[1][0] == "Executive" for c in engine.calls)
        # disconnect must always run.
        assert any(c[0] == "disconnect" for c in engine.calls)

    @pytest.mark.asyncio
    async def test_add_visual_dispatch(self, tmp_path: Path) -> None:
        engine = _StubReportEngine()
        out = await ReportStepExecutor(engine=engine).execute_step(
            _step(
                "add_visual",
                pbip_path=str(tmp_path),
                page="Executive",
                type="clusteredBarChart",
                name="v1",
            )
        )
        assert out.success is True, out.error_message
        assert any(c[0] == "add_visual" for c in engine.calls)

    @pytest.mark.asyncio
    async def test_update_visual_dispatch(self, tmp_path: Path) -> None:
        engine = _StubReportEngine()
        out = await ReportStepExecutor(engine=engine).execute_step(
            _step(
                "update_visual",
                pbip_path=str(tmp_path),
                page="Executive",
                visual_id="v1",
                changes={"x": 1},
            )
        )
        assert out.success is True, out.error_message
        assert any(c[0] == "update_visual" and c[1][1] == "v1" for c in engine.calls)

    @pytest.mark.asyncio
    async def test_propagate_rename_dispatch(self, tmp_path: Path) -> None:
        engine = _StubReportEngine()
        out = await ReportStepExecutor(engine=engine).execute_step(
            _step(
                "propagate_rename",
                pbip_path=str(tmp_path),
                old_path="Sales",
                new_path="Revenue",
            )
        )
        assert out.success is True, out.error_message
        assert any(
            c[0] == "propagate_rename" and c[1][:2] == ("Sales", "Revenue") for c in engine.calls
        )

    @pytest.mark.asyncio
    async def test_engine_exception_is_reported_not_raised(self, tmp_path: Path) -> None:
        engine = _StubReportEngine(fail="add_page")
        out = await ReportStepExecutor(engine=engine).execute_step(
            _step("add_page", pbip_path=str(tmp_path), page_name="Executive")
        )
        assert out.success is False
        assert "exploded" in (out.error_message or "")

    @pytest.mark.asyncio
    async def test_validate_pbir_reports_validity(self, tmp_path: Path) -> None:
        engine = _StubReportEngine()
        out = await ReportStepExecutor(engine=engine).execute_step(
            _step("validate_pbir", pbip_path=str(tmp_path))
        )
        assert out.success is True
        assert out.changed_files == []

    @pytest.mark.asyncio
    async def test_unknown_action_is_noop_success(self, tmp_path: Path) -> None:
        engine = _StubReportEngine()
        out = await ReportStepExecutor(engine=engine).execute_step(
            _step("no_such_action", pbip_path=str(tmp_path))
        )
        assert out.success is True
        assert out.changed_files == []


# ---------------------------------------------------------------------------
# te_adapter — direct write coverage
# ---------------------------------------------------------------------------


class TestAdapterMeasureMutations:
    def test_create_measure_without_model_files_reports_failure(self, tmp_path: Path) -> None:
        """An existing dir with no model files must fail loudly."""
        adapter = InMemoryModelingAdapter()
        handle = asyncio.run(adapter.connect(Target(target_type="pbip", target_ref=str(tmp_path))))
        from powerbi_orchestrator_mcp.engines.base import Measure

        res = asyncio.run(
            adapter.create_measure(
                handle, table="Sales", measure=Measure(name="M", table="Sales", expression="1")
            )
        )
        assert res.success is False
        assert "no dataset model files" in (res.error_message or "")

    def test_update_measure_without_model_files_reports_failure(self, tmp_path: Path) -> None:
        adapter = InMemoryModelingAdapter()
        handle = asyncio.run(adapter.connect(Target(target_type="pbip", target_ref=str(tmp_path))))
        res = asyncio.run(
            adapter.update_measure(handle, table="Sales", measure="M", changes={"expression": "2"})
        )
        assert res.success is False
        assert "no dataset model files" in (res.error_message or "")

    def test_list_measures_reflects_created_measure(self, tmp_path: Path) -> None:
        _pbism_model(tmp_path)
        adapter = InMemoryModelingAdapter()
        handle = asyncio.run(adapter.connect(Target(target_type="pbip", target_ref=str(tmp_path))))
        from powerbi_orchestrator_mcp.engines.base import Measure

        res = asyncio.run(
            adapter.create_measure(
                handle,
                table="Sales",
                measure=Measure(name="Net Sales", table="Sales", expression="SUM(Sales[Amount])"),
            )
        )
        assert res.success is True, res.error_message
        names = [m.name for m in asyncio.run(adapter.list_measures(handle))]
        assert "Net Sales" in names

    def test_corrupt_model_json_is_skipped_not_crashed(self, tmp_path: Path) -> None:
        """A malformed model file must not raise out of the adapter."""
        dataset_dir = tmp_path / "model.Dataset"
        dataset_dir.mkdir(parents=True)
        (dataset_dir / "definition.pbism").write_text("{not json", encoding="utf-8")

        adapter = InMemoryModelingAdapter()
        handle = asyncio.run(adapter.connect(Target(target_type="pbip", target_ref=str(tmp_path))))
        from powerbi_orchestrator_mcp.engines.base import Measure

        res = asyncio.run(
            adapter.create_measure(
                handle, table="Sales", measure=Measure(name="M", table="Sales", expression="1")
            )
        )
        # No crash; reports failure because nothing could be written.
        assert res.success is False
