"""Tests for engines.timeouts — timeout configuration (spec §3)."""

from __future__ import annotations

import pytest

from powerbi_orchestrator_mcp.engines.timeouts import (
    DEFAULT_TIMEOUTS,
    ENV_TIMEOUT_OVERRIDES,
    InvalidTimeoutError,
    UnknownEngineError,
    resolve_timeout,
)


class TestResolveTimeout:
    def test_known_engine_returns_default(self) -> None:
        assert resolve_timeout("powerbi-modeling-mcp", use_env_override=False) == 30
        assert resolve_timeout("te", use_env_override=False) == 60
        assert resolve_timeout("dscmd", use_env_override=False) == 90
        assert resolve_timeout("pbip-validator", use_env_override=False) == 15
        assert resolve_timeout("superbi-mcp", use_env_override=False) == 45

    def test_unknown_engine_raises(self) -> None:
        with pytest.raises(UnknownEngineError):
            resolve_timeout("nonexistent-engine", use_env_override=False)

    def test_requested_within_range(self) -> None:
        assert resolve_timeout("te", requested_s=120, use_env_override=False) == 120

    def test_requested_at_max(self) -> None:
        assert resolve_timeout("te", requested_s=300, use_env_override=False) == 300

    def test_requested_exceeds_max_raises(self) -> None:
        with pytest.raises(InvalidTimeoutError):
            resolve_timeout("te", requested_s=301, use_env_override=False)

    def test_requested_zero_raises(self) -> None:
        with pytest.raises(InvalidTimeoutError):
            resolve_timeout("te", requested_s=0, use_env_override=False)

    def test_requested_negative_raises(self) -> None:
        with pytest.raises(InvalidTimeoutError):
            resolve_timeout("te", requested_s=-1, use_env_override=False)

    def test_env_override_applies_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PBI_ENGINE_TIMEOUT_TE_S", "120")
        assert resolve_timeout("te", use_env_override=True) == 120

    def test_env_override_ignored_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PBI_ENGINE_TIMEOUT_TE_S", "120")
        assert resolve_timeout("te", use_env_override=False) == 60

    def test_env_override_explicit_request_wins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Per spec §3: caller-specified timeout beats env var default."""
        monkeypatch.setenv("PBI_ENGINE_TIMEOUT_TE_S", "120")
        # Explicit requested_s=90 should be returned, not the env default.
        assert resolve_timeout("te", requested_s=90, use_env_override=True) == 90

    def test_invalid_env_value_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PBI_ENGINE_TIMEOUT_TE_S", "not-a-number")
        with pytest.raises(ValueError):
            resolve_timeout("te", use_env_override=True)

    def test_negative_env_value_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PBI_ENGINE_TIMEOUT_TE_S", "0")
        with pytest.raises(ValueError):
            resolve_timeout("te", use_env_override=True)


class TestDefaults:
    def test_all_known_engines_have_config(self) -> None:
        for env_var, engine in ENV_TIMEOUT_OVERRIDES.items():
            assert engine in DEFAULT_TIMEOUTS, f"{engine} (env {env_var}) missing"

    def test_max_exceeds_default(self) -> None:
        for engine, cfg in DEFAULT_TIMEOUTS.items():
            assert cfg.max_s > cfg.default_s, (
                f"{engine}: max_s ({cfg.max_s}) must exceed "
                f"default_s ({cfg.default_s})"
            )

    def test_long_running_flag_consistent(self) -> None:
        # Per spec §3: te and dscmd have long-running=True (trace FE/SE).
        assert DEFAULT_TIMEOUTS["te"].long_running is True
        assert DEFAULT_TIMEOUTS["dscmd"].long_running is True
