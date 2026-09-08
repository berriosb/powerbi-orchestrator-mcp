"""Tests for the Tabular Editor adapter (Sprint 14)."""

from __future__ import annotations

import asyncio

import pytest

from powerbi_orchestrator_mcp.engines.te_adapter import (
    ApplyModelSpecResult,
    CalcGroupSpec,
    InMemoryModelingAdapter,
    RefactorCalcGroupsResult,
    TabularEditorAdapter,
)


class TestInMemoryModelingAdapterBasics:
    def test_name_and_version_defaults(self) -> None:
        a = InMemoryModelingAdapter()
        assert a.name == "in-memory-modeling"
        assert a.version == "0.1.0"

    def test_health_check_returns_available(self) -> None:
        a = InMemoryModelingAdapter()
        status = asyncio.run(a.health_check())
        assert status.available is True

    def test_connect_returns_handle(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.context import Target

        a = InMemoryModelingAdapter()
        handle = asyncio.run(
            a.connect(
                Target(target_type="pbip", target_ref="/tmp/foo.pbip")
            )
        )
        assert handle.target_ref == "/tmp/foo.pbip"


class TestInMemoryModelingAdapterSpecOps:
    def test_apply_model_spec_creates_table(self) -> None:
        a = InMemoryModelingAdapter()
        res = asyncio.run(
            a.apply_model_spec(
                pbip_path="/tmp/x.pbip",
                tmdl_body="table Foo\n    column A\n    dataType: int64\n"
                + "table Bar\n    column B\n    dataType: string\n",
                model_name="m",
            )
        )
        assert isinstance(res, ApplyModelSpecResult)
        assert res.success is True
        assert any("definition.tmdl" in f for f in res.changed_files)
        # Recorded call so consumers can introspect.
        assert a.apply_spec_calls[0]["tmdl_size"] > 0

    def test_refactor_to_calc_groups(self) -> None:
        a = InMemoryModelingAdapter()
        spec = [
            CalcGroupSpec(
                skeleton="Total Sales",
                items=[
                    {"name": "YTD", "expression": "TOTALYTD(...)"},
                    {"name": "QTD", "expression": "TOTALQTD(...)"},
                ],
            ),
        ]
        res = asyncio.run(
            a.refactor_to_calculation_groups(
                pbip_path="/tmp/x.pbip", spec=spec, auto_apply=True
            )
        )
        assert isinstance(res, RefactorCalcGroupsResult)
        assert res.success is True
        assert any("TimeIntelligence" in g for g in res.groups_created)
        assert "Total Sales YTD" in res.measure_remappings
        assert a.refactor_calls[0]["n_spec"] == 1

    def test_apply_model_spec_returns_refactored_tables(self) -> None:
        a = InMemoryModelingAdapter()
        # First push a TMDL, then ask for the tables.
        asyncio.run(
            a.apply_model_spec(
                pbip_path="/tmp/x.pbip",
                tmdl_body="table Foo\n    column A\n    dataType: int64\n",
                model_name="m",
            )
        )
        from powerbi_orchestrator_mcp.orchestrator.context import Target

        handle = asyncio.run(
            a.connect(
                Target(target_type="pbip", target_ref="/tmp/x.pbip")
            )
        )
        tables = asyncio.run(a.list_tables(handle))
        names = {t.name for t in tables}
        assert "Foo" in names


class TestCreateSemanticModelDelegatesToEngine:
    def test_create_with_in_memory_adapter(self, tmp_path) -> None:
        # When an in-memory adapter is supplied, the tool should delegate
        # the actual write to it instead of writing TMDL itself.
        from powerbi_orchestrator_mcp.tools.create_semantic_model_from_schema import (
            create_semantic_model_from_schema,
        )

        yaml_spec = """\
name: demo
tables:
  - name: T1
    columns:
      - {name: Id, type: int64}
"""
        adapter = InMemoryModelingAdapter()
        result = create_semantic_model_from_schema(
            spec_yaml=yaml_spec,
            output_pbip_path=str(tmp_path / "demo.pbip"),
            dry_run=False,
            modeling_engine=adapter,
        )
        assert result.validation_passed is True
        # Adapter was called.
        assert len(adapter.apply_spec_calls) == 1
        # The string-template renderer did NOT write the PBIP file
        # (delegated to engine).
        # But we still keep `output_pbip_path` for callers.
        assert result.pbip_path == str(tmp_path / "demo.pbip")

    def test_create_with_engine_failure_records_warning(
        self, tmp_path
    ) -> None:
        from powerbi_orchestrator_mcp.tools.create_semantic_model_from_schema import (
            create_semantic_model_from_schema,
        )

        class _BrokenEngine:
            @property
            def name(self) -> str:
                return "broken"

            @property
            def version(self) -> str:
                return "x"

            async def apply_model_spec(self, **kw):
                return ApplyModelSpecResult(
                    success=False, error_message="binary missing"
                )

        yaml_spec = """\
name: d
tables:
  - name: t1
    columns:
      - {name: a, type: int64}
"""
        result = create_semantic_model_from_schema(
            spec_yaml=yaml_spec,
            output_pbip_path=str(tmp_path / "x.pbip"),
            dry_run=False,
            modeling_engine=_BrokenEngine(),
        )
        assert result.validation_passed is False
        assert any("binary missing" in w for w in result.warnings)

    def test_create_without_engine_uses_string_template_renderer(
        self, tmp_path
    ) -> None:
        from powerbi_orchestrator_mcp.tools.create_semantic_model_from_schema import (
            create_semantic_model_from_schema,
        )

        yaml_spec = """\
name: d
tables:
  - name: t1
    columns:
      - {name: a, type: int64}
"""
        result = create_semantic_model_from_schema(
            spec_yaml=yaml_spec,
            output_pbip_path=str(tmp_path / "x.pbip"),
            dry_run=False,
        )
        # Fallback path writes the PBIP layout locally.
        assert result.validation_passed is True
        assert (tmp_path / "x.pbip" / "d.pbip").exists()


class TestRefactorDelegatesToEngine:
    def test_refactor_with_te_engine_merges_remappings(
        self, tmp_path
    ) -> None:
        """When `measure_writer` is a TE adapter, the tool delegates."""
        from powerbi_orchestrator_mcp.tools.refactor_to_calculation_groups import (
            refactor_to_calculation_groups,
        )

        adapter = InMemoryModelingAdapter()

        class _Inspector:
            def list_measures(self) -> list[dict]:
                return [
                    {
                        "name": "Total Sales YTD",
                        "expression": "TOTALYTD([A])",
                    },
                    {
                        "name": "Total Sales QTD",
                        "expression": "TOTALQTD([A])",
                    },
                    {
                        "name": "Total Sales MTD",
                        "expression": "TOTALMTD([A])",
                    },
                ]

        result = refactor_to_calculation_groups(
            target=str(tmp_path / "x"),
            inspector=_Inspector(),  # type: ignore[arg-type]
            measure_writer=adapter,
            auto_apply=True,
        )

        assert len(result.groups_created) == 1
        # TE adapter populated remappings.
        assert len(adapter.refactor_calls) == 1
        assert adapter.refactor_calls[0]["auto_apply"] is True

    def test_refactor_with_plain_callable_still_works(self) -> None:
        """Backward compat: a plain callable measure_writer still works."""
        from powerbi_orchestrator_mcp.tools.refactor_to_calculation_groups import (
            refactor_to_calculation_groups,
        )

        class _Inspector:
            def list_measures(self) -> list[dict]:
                return [
                    {"name": "X YTD", "expression": "TOTALYTD(...)"},
                    {"name": "X QTD", "expression": "TOTALQTD(...)"},
                    {"name": "X MTD", "expression": "TOTALMTD(...)"},
                ]

        def writer(**kwargs):
            return {"changed_files": ["model.tmdl"]}

        result = refactor_to_calculation_groups(
            target="x",
            inspector=_Inspector(),  # type: ignore[arg-type]
            measure_writer=writer,
            auto_apply=True,
        )
        assert "model.tmdl" in result.changed_files


class TestTabularEditorAdapterSkeleton:
    def test_name_and_version(self) -> None:
        a = TabularEditorAdapter()
        assert a.name == "te"
        assert a.version == "stub-0.1.0"

    def test_apply_model_spec_without_subprocess(
        self, tmp_path
    ) -> None:
        # Without a real TE binary the stub path returns the
        # would-be-written file path.
        a = TabularEditorAdapter()
        result = asyncio.run(
            a.apply_model_spec(
                pbip_path=str(tmp_path / "demo.pbip"),
                tmdl_body="table T\n",
                model_name="m",
            )
        )
        assert result.success is True
        assert any("definition.tmdl" in f for f in result.changed_files)

    def test_apply_model_spec_missing_binary(self) -> None:
        # Missing binary → reported failure (not raised) so callers can
        # elicit / fall back to InMemoryModelingAdapter.
        a = TabularEditorAdapter(
            binary="/nonexistent/TabularEditor.exe",
            mode="subprocess",
        )
        result = asyncio.run(
            a.apply_model_spec(
                pbip_path="/tmp/x",
                tmdl_body="table T\n",
                model_name="m",
            )
        )
        assert result.success is False
        assert result.error_message and "not found" in result.error_message

    def test_refactor_with_skeleton_adapter(self) -> None:
        a = TabularEditorAdapter(mode="skeleton")
        spec = [
            CalcGroupSpec(
                skeleton="Total Sales",
                items=[
                    {"name": "YTD", "expression": "TOTALYTD(...)"},
                ],
            )
        ]
        result = asyncio.run(
            a.refactor_to_calculation_groups(
                pbip_path="/tmp/x",
                spec=spec,
                auto_apply=True,
            )
        )
        assert result.success is True
        assert "Total_Sales" in result.groups_created[0]
        # The skeleton mode prepends "TimeIntelligence_" to the
        # calculation-group name.
        assert result.groups_created[0].startswith("TimeIntelligence_")

    def test_apply_model_spec_with_skeleton_mode(self, tmp_path) -> None:
        a = TabularEditorAdapter(mode="skeleton")
        result = asyncio.run(
            a.apply_model_spec(
                pbip_path=str(tmp_path / "demo.pbip"),
                tmdl_body="table T\n",
                model_name="m",
            )
        )
        assert result.success is True
        assert result.tmdl_body is not None

    def test_invalid_mode_raises(self) -> None:
        with pytest.raises(ValueError):
            TabularEditorAdapter(mode="bogus")
