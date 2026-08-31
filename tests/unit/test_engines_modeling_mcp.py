"""Tests for engines.modeling_mcp — PowerBiModelingMcpEngine adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from powerbi_orchestrator_mcp.engines.base import (
    ConnectionHandle,
    DaxResult,
    Measure,
    OperationResult,
    SnapshotHandle,
)
from powerbi_orchestrator_mcp.engines.errors import (
    EngineError,
    EngineNotFoundError,
)
from powerbi_orchestrator_mcp.engines.modeling_mcp import (
    DEFAULT_PINNED_VERSION,
    PowerBiModelingMcpEngine,
)
from powerbi_orchestrator_mcp.orchestrator.context import Target


@pytest.fixture()
def target_pbip() -> Target:
    return Target(
        target_type="pbip_folder",
        target_ref="./out/sales.pbip",
        auth_mode="interactive",
    )


@pytest.fixture()
def target_fabric() -> Target:
    return Target(
        target_type="fabric_workspace",
        target_ref="ws-abc-def",
        auth_mode="service_principal",
        tenant_id="00000000-1111-2222-3333-444444444444",
    )


def _engine(responses: dict[str, Any] | None = None) -> PowerBiModelingMcpEngine:
    """Build an adapter with mocked responses (no real subprocess)."""
    return PowerBiModelingMcpEngine(
        binary="/bin/echo",
        version="0.1.9",
        mock_responses=responses or {},
    )


class TestProperties:
    def test_name(self) -> None:
        assert _engine().name == "powerbi-modeling-mcp"

    def test_version_default(self) -> None:
        assert _engine().version == "0.1.9"

    def test_default_pinned_version_constant(self) -> None:
        assert DEFAULT_PINNED_VERSION == "0.1.9"


class TestConnect:
    async def test_connect_with_mocked_start(self) -> None:
        target = Target(
            target_type="pbip_folder",
            target_ref="./out/sales.pbip",
            auth_mode="interactive",
        )
        engine = _engine()
        # Override _start to skip the real spawn (would fail on /bin/echo).
        called = {"n": 0}

        async def fake_start(*, timeout_s: int | None = None) -> None:  # noqa: ARG001
            called["n"] += 1

        engine._start = fake_start  # type: ignore[method-assign]
        conn = await engine.connect(target)
        assert isinstance(conn, ConnectionHandle)
        assert conn.engine == "powerbi-modeling-mcp"
        assert conn.target_type == "pbip_folder"
        assert conn.session_token == "pbip_folder:./out/sales.pbip"
        assert called["n"] == 1

    async def test_disconnect_is_noop_for_mvp(self) -> None:
        engine = _engine()
        conn = ConnectionHandle(
            engine="powerbi-modeling-mcp",
            target_type="pbip_folder",
            target_ref="./x",
            session_token="x",
        )
        # Should not raise.
        await engine.disconnect(conn)


class TestReadOps:
    async def test_list_tables(self) -> None:
        engine = _engine(
            responses={
                "database_operations/list_tables": {
                    "tables": [
                        {"name": "DimDate"},
                        {"name": "FactSales", "is_hidden": True},
                    ]
                }
            }
        )
        conn = ConnectionHandle(
            engine="powerbi-modeling-mcp",
            target_type="pbip_folder",
            target_ref="./x",
            session_token="x",
        )
        tables = await engine.list_tables(conn)
        assert len(tables) == 2
        assert tables[0].name == "DimDate"
        assert tables[1].is_hidden is True

    async def test_list_columns(self) -> None:
        engine = _engine(
            responses={
                "column_operations/list": {
                    "columns": [
                        {"name": "Date", "data_type": "dateTime"},
                        {"name": "Year", "data_type": "int64"},
                    ]
                }
            }
        )
        conn = ConnectionHandle(
            engine="powerbi-modeling-mcp",
            target_type="pbip_folder",
            target_ref="./x",
            session_token="x",
        )
        cols = await engine.list_columns(conn, "DimDate")
        assert len(cols) == 2
        assert cols[0].data_type == "dateTime"

    async def test_list_measures(self) -> None:
        engine = _engine(
            responses={
                "measure_operations/list": {
                    "measures": [
                        {
                            "name": "Total Sales",
                            "table": "FactSales",
                            "expression": "SUM([Amount])",
                        }
                    ]
                }
            }
        )
        conn = ConnectionHandle(
            engine="powerbi-modeling-mcp",
            target_type="pbip_folder",
            target_ref="./x",
            session_token="x",
        )
        measures = await engine.list_measures(conn)
        assert len(measures) == 1
        assert measures[0].name == "Total Sales"

    async def test_list_relationships(self) -> None:
        engine = _engine(
            responses={
                "database_operations/list_relationships": {
                    "relationships": [
                        {
                            "from_table": "FactSales",
                            "from_column": "DateKey",
                            "to_table": "DimDate",
                            "to_column": "Date",
                        }
                    ]
                }
            }
        )
        conn = ConnectionHandle(
            engine="powerbi-modeling-mcp",
            target_type="pbip_folder",
            target_ref="./x",
            session_token="x",
        )
        rels = await engine.list_relationships(conn)
        assert len(rels) == 1
        assert rels[0].from_table == "FactSales"


class TestWriteOps:
    async def test_update_column_returns_changed_files(self) -> None:
        engine = _engine(
            responses={
                "column_operations/update": {
                    "changed_files": ["model/tables/Customer.tmdl"]
                }
            }
        )
        conn = ConnectionHandle(
            engine="powerbi-modeling-mcp",
            target_type="pbip_folder",
            target_ref="./x",
            session_token="x",
        )
        result = await engine.update_column(
            conn, "Customer", "ID", {"new_name": "CustomerKey"}
        )
        assert isinstance(result, OperationResult)
        assert result.success is True
        assert result.changed_files == ["model/tables/Customer.tmdl"]

    async def test_create_measure(self) -> None:
        engine = _engine(
            responses={
                "measure_operations/create": {"changed_files": ["x.tmdl"]}
            }
        )
        conn = ConnectionHandle(
            engine="powerbi-modeling-mcp",
            target_type="pbip_folder",
            target_ref="./x",
            session_token="x",
        )
        m = Measure(name="YTD", table="FactSales", expression="TOTALYTD([X])")
        result = await engine.create_measure(conn, "FactSales", m)
        assert result.success is True

    async def test_update_measure(self) -> None:
        engine = _engine(
            responses={
                "measure_operations/update": {"changed_files": ["x.tmdl"]}
            }
        )
        conn = ConnectionHandle(
            engine="powerbi-modeling-mcp",
            target_type="pbip_folder",
            target_ref="./x",
            session_token="x",
        )
        result = await engine.update_measure(
            conn, "FactSales", "Total Sales", {"expression": "SUM([Amount])"}
        )
        assert result.success is True

    async def test_delete_measure(self) -> None:
        engine = _engine(
            responses={
                "measure_operations/delete": {"changed_files": ["x.tmdl"]}
            }
        )
        conn = ConnectionHandle(
            engine="powerbi-modeling-mcp",
            target_type="pbip_folder",
            target_ref="./x",
            session_token="x",
        )
        result = await engine.delete_measure(conn, "FactSales", "Total Sales")
        assert result.success is True


class TestDax:
    async def test_execute_dax_rows(self) -> None:
        engine = _engine(
            responses={
                "dax_query_operations/run": {
                    "rows": [{"Total": 100000}, {"Total": 200000}]
                }
            }
        )
        conn = ConnectionHandle(
            engine="powerbi-modeling-mcp",
            target_type="pbip_folder",
            target_ref="./x",
            session_token="x",
        )
        result = await engine.execute_dax(conn, "EVALUATE ROW(...)")
        assert isinstance(result, DaxResult)
        assert result.row_count == 2
        assert result.rows[0]["Total"] == 100000

    async def test_execute_dax_with_effective_identity(self) -> None:
        engine = _engine(
            responses={"dax_query_operations/run": {"rows": []}}
        )
        conn = ConnectionHandle(
            engine="powerbi-modeling-mcp",
            target_type="pbip_folder",
            target_ref="./x",
            session_token="x",
        )
        await engine.execute_dax(
            conn, "EVALUATE ROW(...)", effective_identity={"username": "alice"}
        )
        # Inspect the dispatch call: params should include effective_identity.
        last_method, last_params = engine.dispatch_calls[-1]
        assert last_method == "dax_query_operations/run"
        assert last_params["effective_identity"] == {"username": "alice"}


class TestSnapshot:
    async def test_snapshot_returns_handle(self) -> None:
        engine = _engine(
            responses={
                "database_operations/export_tmdl": {"path": "/tmp/snap.tmdl"}
            }
        )
        conn = ConnectionHandle(
            engine="powerbi-modeling-mcp",
            target_type="pbip_folder",
            target_ref="./x",
            session_token="x",
        )
        handle = await engine.snapshot(conn, "pre-rename")
        assert isinstance(handle, SnapshotHandle)
        assert handle.label == "pre-rename"
        assert handle.path == Path("/tmp/snap.tmdl")

    async def test_restore_snapshot(self) -> None:
        engine = _engine(responses={"database_operations/import_tmdl": {}})
        handle = SnapshotHandle(label="pre-x", path=Path("/tmp/snap"))
        await engine.restore_snapshot(handle)
        assert engine.dispatch_calls[-1][0] == "database_operations/import_tmdl"


class TestDispatch:
    async def test_dispatch_uses_mock_response(self) -> None:
        engine = _engine(
            responses={"foo/bar": {"ok": True}}
        )
        result = await engine._dispatch(
            "foo", "bar", conn=None, extra={"x": 1}
        )
        assert result == {"ok": True}

    async def test_dispatch_wraps_unexpected_exception(self) -> None:
        engine = _engine()
        # Inject a mock that raises non-EngineError.
        async def fake_rpc(method: str, params: dict | None = None) -> dict:
            raise RuntimeError("boom")

        engine._rpc = fake_rpc  # type: ignore[method-assign]
        with pytest.raises(EngineError) as exc_info:
            await engine._dispatch("foo", "bar", conn=None)
        assert exc_info.value.code == "engine_unhandled"

    async def test_dispatch_passes_target_in_params(self) -> None:
        engine = _engine()
        captured: dict = {}

        async def fake_rpc(method: str, params: dict | None = None) -> dict:
            captured.update(params or {})
            return {}

        engine._rpc = fake_rpc  # type: ignore[method-assign]
        conn = ConnectionHandle(
            engine="powerbi-modeling-mcp",
            target_type="pbip_folder",
            target_ref="./x",
            session_token="x",
        )
        await engine._dispatch("ns", "op", conn=conn, extra={"k": "v"})
        assert "target" in captured
        assert captured["target"]["target_type"] == "pbip_folder"
        assert captured["k"] == "v"


class TestRealSubprocess:
    async def test_engine_not_found_when_binary_missing(self) -> None:
        target = Target(
            target_type="pbip_folder",
            target_ref="./x",
            auth_mode="interactive",
        )
        engine = PowerBiModelingMcpEngine(binary="/nonexistent/binary")
        with pytest.raises(EngineNotFoundError):
            await engine.connect(target)
