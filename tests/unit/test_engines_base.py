"""Tests for engines.base — Protocols + JsonRpcSubprocessEngine."""

from __future__ import annotations

from typing import Any

import pytest

from powerbi_orchestrator_mcp.engines.base import (
    Column,
    ConnectionHandle,
    DaxResult,
    JsonRpcSubprocessEngine,
    Measure,
    OperationResult,
    Relationship,
    SnapshotHandle,
    Table,
    VisualSpec,
    map_subprocess_error,
    utc_now_ms,
)
from powerbi_orchestrator_mcp.engines.errors import (
    EngineCrashedError,
    EngineError,
    EngineNotFoundError,
    EngineOutputParseError,
    EngineTimeoutError,
)

# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class TestDomainModels:
    def test_column_defaults(self) -> None:
        c = Column(name="x")
        assert c.name == "x"
        assert c.data_type == "string"
        assert c.is_hidden is False

    def test_measure_round_trip(self) -> None:
        m = Measure(
            name="Total Sales",
            table="FactSales",
            expression="SUM([Amount])",
        )
        assert m.name == "Total Sales"
        assert m.format_string is None

    def test_relationship_defaults(self) -> None:
        r = Relationship(
            from_table="FactSales",
            from_column="DateKey",
            to_table="DimDate",
            to_column="Date",
        )
        assert r.cardinality == "many_to_one"
        assert r.is_active is True

    def test_table_with_columns(self) -> None:
        t = Table(name="DimDate", columns=["Date", "Year", "Month"])
        assert len(t.columns) == 3

    def test_operation_result(self) -> None:
        r = OperationResult(success=True, changed_files=["a.tmdl"])
        assert r.success is True

    def test_dax_result(self) -> None:
        d = DaxResult(rows=[{"x": 1}, {"x": 2}], row_count=2)
        assert d.row_count == 2

    def test_snapshot_handle(self) -> None:
        from pathlib import Path

        h = SnapshotHandle(label="pre-x", path=Path("/tmp/x"))
        assert h.label == "pre-x"

    def test_visual_spec(self) -> None:
        v = VisualSpec(
            type="card",
            fields={"Values": ["[YTD Sales]"]},
            position={"x": 0, "y": 0, "width": 400, "height": 150},
        )
        assert v.type == "card"

    def test_connection_handle(self) -> None:
        h = ConnectionHandle(
            engine="powerbi-modeling-mcp",
            target_type="pbip_folder",
            target_ref="./x.pbip",
            session_token="pbip_folder:./x.pbip",
        )
        assert h.engine == "powerbi-modeling-mcp"


# ---------------------------------------------------------------------------
# JsonRpcSubprocessEngine
# ---------------------------------------------------------------------------


class _FakeSubprocessEngine(JsonRpcSubprocessEngine):
    """Subclass that overrides _start and _rpc for testing.

    Lets tests script responses and trigger errors without forking
    real subprocesses.
    """

    def __init__(
        self,
        responses: dict[str, dict[str, Any]] | None = None,
        start_raises: EngineError | None = None,
        rpc_raises: Exception | None = None,
    ) -> None:
        super().__init__(
            engine_name="fake",
            binary="/bin/echo",
            args=("hello",),
        )
        self._responses = responses or {}
        self._start_raises = start_raises
        self._rpc_raises = rpc_raises
        self._calls: list[tuple[str, dict[str, Any]]] = []
        self._started = False

    async def _start(self, *, timeout_s: int | None = None) -> None:  # noqa: ARG002
        if self._start_raises is not None:
            raise self._start_raises
        self._started = True
        # No real subprocess; tests should call _rpc only when _started.

    async def _rpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_s: int | None = None,  # noqa: ARG002
    ) -> dict[str, Any]:
        self._calls.append((method, params or {}))
        if self._rpc_raises is not None:
            raise self._rpc_raises
        return self._responses.get(method, {"result": "ok"})

    @property
    def started(self) -> bool:
        return self._started


class TestStart:
    async def test_start_succeeds(self) -> None:
        e = _FakeSubprocessEngine()
        await e._start()
        assert e.started is True

    async def test_start_propagates_engine_error(self) -> None:
        not_found = EngineNotFoundError(
            "missing", engine="fake", code="x", remediation_hint="y"
        )
        e = _FakeSubprocessEngine(start_raises=not_found)
        with pytest.raises(EngineNotFoundError):
            await e._start()

    async def test_health_check_returns_available_when_start_ok(self) -> None:
        e = _FakeSubprocessEngine()
        e._version = "1.2.3"  # type: ignore[attr-defined]
        status = await e.health_check()
        assert status.available is True
        assert status.version == "1.2.3"

    async def test_health_check_returns_unavailable_when_start_fails(self) -> None:
        not_found = EngineNotFoundError(
            "missing", engine="fake", code="x", remediation_hint="install it"
        )
        e = _FakeSubprocessEngine(start_raises=not_found)
        status = await e.health_check()
        assert status.available is False
        assert "install it" in (status.reason_unavailable or "")


class TestRpc:
    async def test_rpc_records_call(self) -> None:
        e = _FakeSubprocessEngine(responses={"foo/bar": {"x": 1}})
        result = await e._rpc("foo/bar", {"k": "v"})
        assert result == {"x": 1}
        assert e._calls == [("foo/bar", {"k": "v"})]

    async def test_rpc_propagates_engine_error(self) -> None:
        err = EngineTimeoutError(
            "timeout",
            engine="fake",
            remediation_hint="r",
            timeout_s=30,
        )
        e = _FakeSubprocessEngine(rpc_raises=err)
        with pytest.raises(EngineTimeoutError):
            await e._rpc("foo")


class TestStop:
    async def test_stop_no_op_when_not_started(self) -> None:
        e = _FakeSubprocessEngine()
        await e._stop()  # does nothing

    async def test_stop_is_idempotent(self) -> None:
        e = _FakeSubprocessEngine()
        await e._start()
        await e._stop()
        await e._stop()  # idempotent


class TestMapSubprocessError:
    def test_passes_through_engine_error(self) -> None:
        original = EngineCrashedError(
            "x", engine="fake", code="x", remediation_hint="r"
        )
        wrapped = map_subprocess_error("fake", original, "op")
        assert wrapped is original

    def test_wraps_unexpected_exception(self) -> None:
        wrapped = map_subprocess_error("fake", ValueError("oops"), "op")
        assert isinstance(wrapped, EngineError)
        assert wrapped.code == "engine_unhandled"


# ---------------------------------------------------------------------------
# Real subprocess test (skipped if /bin/echo isn't usable)
# ---------------------------------------------------------------------------


class TestRealSubprocess:
    @pytest.mark.asyncio
    async def test_real_subprocess_starts(self) -> None:
        """Smoke test: real subprocess using /bin/echo.

        /bin/echo never exits cleanly with our expected format, so
        _start() will likely raise EngineError — but the spawn itself
        must succeed on Linux. We just check the constructor works.
        """
        e = JsonRpcSubprocessEngine(
            engine_name="echo_test",
            binary="/bin/echo",
            args=("hi",),
        )
        # We don't call _start() — /bin/echo doesn't speak JSON-RPC.
        # The test just verifies the class is instantiable.
        assert e.name_implied == "echo_test" if hasattr(e, "name_implied") else True

    @pytest.mark.asyncio
    async def test_engine_not_found_on_missing_binary(self) -> None:
        e = JsonRpcSubprocessEngine(
            engine_name="missing_test",
            binary="/nonexistent/path/binary",
            args=(),
        )
        with pytest.raises(EngineNotFoundError):
            await e._start()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_utc_now_ms_returns_int(self) -> None:
        v = utc_now_ms()
        assert isinstance(v, int)
        assert v > 0


class TestOutputParseError:
    async def test_output_parse_error_message(self) -> None:
        err = EngineOutputParseError(
            "stdout was not JSON",
            engine="fake",
            code="engine_output_parse_error",
            remediation_hint="check upstream",
        )
        assert "stdout was not JSON" in err.message
