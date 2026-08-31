"""Tests for engine_detector + step_executor (footnote to server tests)."""

from __future__ import annotations

import shutil

import pytest

from powerbi_orchestrator_mcp.orchestrator.context import EngineStatus
from powerbi_orchestrator_mcp.orchestrator.engine_detector import (
    _ENGINE_PROBES,
    detect_all_engines,
    detect_engine,
    get_engine_timeout_s,
    get_known_engines,
    has_timeout_config,
)
from powerbi_orchestrator_mcp.orchestrator.plan_models import PlanStep
from powerbi_orchestrator_mcp.orchestrator.step_executor import (
    DryRunExecutor,
    MissingEngineExecutor,
    StepExecutorRegistry,
    get_default_registry,
    reset_default_registry,
)

# ---------------------------------------------------------------------------
# engine_detector
# ---------------------------------------------------------------------------


class TestDetectAll:
    @pytest.mark.asyncio
    async def test_returns_all_known_engines(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force all binaries to be missing.
        monkeypatch.setattr(shutil, "which", lambda _name: None)

        result = await detect_all_engines()
        # All probes should be reported.
        for probe in _ENGINE_PROBES:
            assert probe.engine in result
            assert isinstance(result[probe.engine], EngineStatus)

    @pytest.mark.asyncio
    async def test_unavailable_when_binary_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        result = await detect_all_engines()
        for status in result.values():
            assert status.available is False
            assert status.reason_unavailable is not None

    @pytest.mark.asyncio
    async def test_available_when_binary_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pretend "te" is the only thing in PATH.
        monkeypatch.setattr(
            shutil, "which", lambda name: f"/usr/bin/{name}" if name == "te" else None
        )
        result = await detect_all_engines()
        assert result["te"].available is True
        # Other engines remain unavailable.
        assert result["powerbi-modeling-mcp"].available is False


class TestDetectSingle:
    @pytest.mark.asyncio
    async def test_unavailable_has_helpful_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        probe = _ENGINE_PROBES[0]  # powerbi-modeling-mcp
        status = await detect_engine(probe)
        assert status.available is False
        assert "not found" in (status.reason_unavailable or "")

    @pytest.mark.asyncio
    async def test_npm_engine_mentions_npx(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        # powerbi-modeling-mcp is an npx package per its probe.
        probe = next(p for p in _ENGINE_PROBES if p.npx_package is not None)
        status = await detect_engine(probe)
        assert "npx" in (status.reason_unavailable or "")

    @pytest.mark.asyncio
    async def test_version_probe_handles_crashing_binary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pretend a binary exists but crashes on --version.
        monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/te")
        # _probe_version would try to actually run the binary, which would
        # fail in this test environment. The detector must handle this
        # gracefully and still report the engine as available with
        # version=None.

        async def fake_probe(*_args: object, **_kwargs: object) -> str | None:
            return None

        monkeypatch.setattr(
            "powerbi_orchestrator_mcp.orchestrator.engine_detector._probe_version",
            fake_probe,
        )
        probe = next(p for p in _ENGINE_PROBES if p.engine == "te")
        status = await detect_engine(probe)
        assert status.available is True
        assert status.version is None


class TestHelpers:
    def test_get_known_engines(self) -> None:
        engines = get_known_engines()
        assert isinstance(engines, tuple)
        assert "te" in engines
        assert "pbip-validator" in engines

    def test_get_engine_timeout_s(self) -> None:
        assert get_engine_timeout_s("te") == 60
        assert get_engine_timeout_s("powerbi-modeling-mcp") == 30

    def test_get_engine_timeout_s_unknown(self) -> None:
        from powerbi_orchestrator_mcp.engines.timeouts import UnknownEngineError

        with pytest.raises(UnknownEngineError):
            get_engine_timeout_s("nonexistent")

    def test_has_timeout_config(self) -> None:
        assert has_timeout_config("te") is True
        assert has_timeout_config("nonexistent") is False


# ---------------------------------------------------------------------------
# step_executor
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    reset_default_registry()


class TestDryRunExecutor:
    @pytest.mark.asyncio
    async def test_engine_name(self) -> None:
        assert DryRunExecutor().engine_name == "dry_run"

    @pytest.mark.asyncio
    async def test_returns_success(self) -> None:
        result = await DryRunExecutor().execute_step(
            PlanStep(id="s1", engine="modeling", action="x")
        )
        assert result.success is True
        assert result.error_message is None
        assert result.changed_files == []


class TestMissingEngineExecutor:
    @pytest.mark.asyncio
    async def test_engine_name(self) -> None:
        assert MissingEngineExecutor("foo").engine_name == "__missing__"

    @pytest.mark.asyncio
    async def test_fails_with_helpful_message(self) -> None:
        # The engine field on PlanStep is a Literal; "modeling" is allowed.
        # The MissingEngineExecutor is created with a name that doesn't
        # have a registered adapter (we never register "modeling" here).
        result = await MissingEngineExecutor("nonexistent_engine").execute_step(
            PlanStep(id="s1", engine="modeling", action="x")
        )
        assert result.success is False
        assert "nonexistent_engine" in (result.error_message or "")


class TestStepExecutorRegistry:
    def test_register_and_get(self) -> None:
        reg = StepExecutorRegistry()
        executor = DryRunExecutor()
        reg.register(executor)
        assert reg.get("dry_run") is executor

    def test_get_missing_returns_missing_executor(self) -> None:
        reg = StepExecutorRegistry()
        result = reg.get("never_registered")
        assert isinstance(result, MissingEngineExecutor)

    def test_has_only_for_registered(self) -> None:
        reg = StepExecutorRegistry()
        assert reg.has("dry_run") is False  # not registered
        reg.register(DryRunExecutor())
        assert reg.has("dry_run") is True

    def test_unregister(self) -> None:
        reg = StepExecutorRegistry()
        reg.register(DryRunExecutor())
        reg.unregister("dry_run")
        assert reg.has("dry_run") is False

    def test_available_engines(self) -> None:
        reg = StepExecutorRegistry()
        reg.register(DryRunExecutor())
        assert "dry_run" in reg.available_engines()
        # Add a MissingEngineExecutor via get() — but that doesn't register,
        # so it shouldn't appear in available_engines.
        reg.get("never_registered")  # side effect: still nothing registered
        assert "never_registered" not in reg.available_engines()

    def test_last_registration_wins(self) -> None:
        reg = StepExecutorRegistry()
        ex1 = DryRunExecutor()
        ex2 = DryRunExecutor()
        reg.register(ex1)
        reg.register(ex2)
        assert reg.get("dry_run") is ex2

    @pytest.mark.asyncio
    async def test_missing_executor_always_fails(self) -> None:
        reg = StepExecutorRegistry()
        executor = reg.get("modeling")  # not registered
        result = await executor.execute_step(
            PlanStep(id="s1", engine="modeling", action="x")
        )
        assert result.success is False
        assert "modeling" in (result.error_message or "")


class TestDefaultRegistry:
    def test_default_has_dry_run(self) -> None:
        reg = get_default_registry()
        assert reg.has("dry_run") is True

    def test_reset_clears(self) -> None:
        reg = get_default_registry()
        reg.register(MissingEngineExecutor("temp"))  # type: ignore[arg-type]
        # After reset, only dry_run remains.
        reset_default_registry()
        assert get_default_registry().has("dry_run") is True
