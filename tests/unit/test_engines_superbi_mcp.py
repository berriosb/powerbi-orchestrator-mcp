"""Tests for engines.superbi_mcp — SuperBiMcpEngine adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.engines.base import (
    ConnectionHandle,
    OperationResult,
    ValidationResult,
    VisualSpec,
)
from powerbi_orchestrator_mcp.engines.errors import (
    EngineError,
    EngineValidationError,
)
from powerbi_orchestrator_mcp.engines.superbi_mcp import (
    DEFAULT_PINNED_VERSION,
    SuperBiMcpEngine,
)


def _engine(
    responses: dict[str, object] | None = None,
) -> SuperBiMcpEngine:
    """Build an adapter with mocked responses (no real subprocess)."""
    return SuperBiMcpEngine(
        binary="/bin/echo",
        version="1.5.0",
        mock_responses=responses or {},
    )


@pytest.fixture()
def pbip_dir(tmp_path: Path) -> Path:
    p = tmp_path / "sample.pbip"
    p.mkdir()
    (p / "sample.pbip").write_text("{}", encoding="utf-8")
    (p / "sample.Report").mkdir()
    (p / "sample.Report" / "report.json").write_text("{}", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Properties + health
# ---------------------------------------------------------------------------


class TestProperties:
    def test_name(self) -> None:
        assert _engine().name == "superbi-mcp"

    def test_version_default(self) -> None:
        assert _engine().version == "1.5.0"

    def test_default_pinned_version(self) -> None:
        assert DEFAULT_PINNED_VERSION == "1.5.0"


class TestHealth:
    async def test_health_check_records_unavailable_when_start_fails(self) -> None:
        # The _FakeStart pattern is reused via mocking _start.
        engine = _engine()

        async def fake_start_raises(*_a: object, **_k: object) -> None:
            raise EngineError(
                "spawn fail",
                engine="superbi-mcp",
                code="x",
                remediation_hint="not installed",
            )

        engine._start = fake_start_raises  # type: ignore[method-assign]  # noqa: SLF001
        status = await engine.health_check()
        assert status.available is False
        assert status.reason_unavailable == "not installed"

    async def test_health_check_available(self) -> None:
        engine = _engine()

        async def fake_start_ok(*_a: object, **_k: object) -> None:
            return None

        engine._start = fake_start_ok  # type: ignore[method-assign]  # noqa: SLF001
        status = await engine.health_check()
        assert status.available is True


# ---------------------------------------------------------------------------
# Connect / disconnect
# ---------------------------------------------------------------------------


class TestConnect:
    async def test_connect_validates_pbip_exists(self, pbip_dir: Path) -> None:
        engine = _engine()

        async def fake_start(*_a: object, **_k: object) -> None:
            return None

        engine._start = fake_start  # type: ignore[method-assign]  # noqa: SLF001
        conn = await engine.connect(pbip_dir)
        assert conn.engine == "superbi-mcp"
        assert conn.target_type == "pbip_folder"

    async def test_connect_raises_for_missing_path(self, tmp_path: Path) -> None:
        engine = _engine()

        async def fake_start(*_a: object, **_k: object) -> None:
            return None

        engine._start = fake_start  # type: ignore[method-assign]  # noqa: SLF001
        with pytest.raises(EngineValidationError):
            await engine.connect(tmp_path / "nonexistent")

    async def test_disconnect_noop(self, pbip_dir: Path) -> None:
        engine = _engine()
        conn = ConnectionHandle(
            engine="superbi-mcp",
            target_type="pbip_folder",
            target_ref=str(pbip_dir),
            session_token="x",
        )
        await engine.disconnect(conn)  # no exception


# ---------------------------------------------------------------------------
# Page CRUD — delegates to PythonReportEngine until Week 2 wiring
# ---------------------------------------------------------------------------


class TestPageCrud:
    async def test_add_page_delegates_to_python_report(
        self, pbip_dir: Path
    ) -> None:
        engine = _engine()
        conn = ConnectionHandle(
            engine="superbi-mcp",
            target_type="pbip_folder",
            target_ref=str(pbip_dir),
            session_token="x",
        )
        result = await engine.add_page(conn, "NewPage")
        assert isinstance(result, OperationResult)
        assert result.success is True

    async def test_add_visual_delegates(self, pbip_dir: Path) -> None:
        engine = _engine()
        conn = ConnectionHandle(
            engine="superbi-mcp",
            target_type="pbip_folder",
            target_ref=str(pbip_dir),
            session_token="x",
        )
        # Create the page first (add_visual appends, doesn't create).
        await engine.add_page(conn, "NewPage")
        spec = VisualSpec(
            type="card",
            fields={"Values": ["[X]"]},
            position={"x": 0, "y": 0, "width": 400, "height": 200},
        )
        result = await engine.add_visual(conn, "NewPage", spec)
        assert result.success is True

    async def test_update_visual_delegates(self, pbip_dir: Path) -> None:
        engine = _engine()
        conn = ConnectionHandle(
            engine="superbi-mcp",
            target_type="pbip_folder",
            target_ref=str(pbip_dir),
            session_token="x",
        )
        # Create page + visual first to get a known visual_id.
        await engine.add_page(conn, "NewPage")
        spec = VisualSpec(
            visual_id="known_id_1",
            type="card",
            fields={"Values": ["[X]"]},
            position={"x": 0, "y": 0, "width": 400, "height": 200},
        )
        await engine.add_visual(conn, "NewPage", spec)
        result = await engine.update_visual(
            conn, "NewPage", "known_id_1", {"x": 50}
        )
        assert result.success is True


# ---------------------------------------------------------------------------
# propagate_rename via _dispatch (the real one, not the stub)
# ---------------------------------------------------------------------------


class TestDispatch:
    async def test_propagate_rename_uses_mock_response(
        self, pbip_dir: Path
    ) -> None:
        engine = _engine(
            responses={
                "report/propagate_rename": {
                    "changed_files": ["sample.Report/pages/Overview/page.json"]
                }
            }
        )

        async def fake_start(*_a: object, **_k: object) -> None:
            return None

        engine._start = fake_start  # type: ignore[method-assign]  # noqa: SLF001

        conn = await engine.connect(pbip_dir)
        result = await engine.propagate_rename(
            conn, "Customer[ID]", "Customer[CustomerKey]", "report_bindings"
        )
        assert result.success is True
        assert result.changed_files == [
            "sample.Report/pages/Overview/page.json"
        ]

        # The dispatch should have recorded the call with pbip_path.
        last_method, last_params = engine.dispatch_calls[-1]
        assert last_method == "report/propagate_rename"
        assert last_params["pbip_path"] == str(pbip_dir)
        assert last_params["old_path"] == "Customer[ID]"

    async def test_validate_pbir_via_mock(
        self, pbip_dir: Path
    ) -> None:
        engine = _engine(
            responses={
                "report/validate": {
                    "valid": False,
                    "findings": [
                        {
                            "severity": "error",
                            "rule_id": "missing_alt_text",
                            "message": "Visual v1 has no alt text",
                        }
                    ],
                }
            }
        )

        async def fake_start(*_a: object, **_k: object) -> None:
            return None

        engine._start = fake_start  # type: ignore[method-assign]  # noqa: SLF001
        conn = await engine.connect(pbip_dir)
        result = await engine.validate_pbir(conn)
        assert isinstance(result, ValidationResult)
        assert result.valid is False
        assert len(result.findings) == 1
        assert result.findings[0]["rule_id"] == "missing_alt_text"
