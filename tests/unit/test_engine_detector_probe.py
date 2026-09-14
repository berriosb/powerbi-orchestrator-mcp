"""Sprint 15 follow-up: cover _probe_version directly.

Existing tests in test_engine_and_executor.py monkey-patch
_probe_version, so the real subprocess plumbing (OSError during
spawn, TimeoutError, non-zero exit, stdout capture, multi-line
split) is uncovered.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from powerbi_orchestrator_mcp.orchestrator.engine_detector import (
    _probe_version,
)


def _spawn(monkeypatch: pytest.MonkeyPatch, proc: Any) -> None:
    async def fake_exec(*_a: Any, **_kw: Any) -> Any:
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


class TestProbeVersion:
    async def test_empty_args_returns_none(self) -> None:
        # The detector skips probing entirely when there are no args.
        assert await _probe_version("/bin/echo", ()) is None

    async def test_successful_run_returns_first_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeProc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return (b"version 1.2.3\nmore output\n", b"")

            async def wait(self) -> None:
                return None

            def kill(self) -> None:
                pass

        _spawn(monkeypatch, FakeProc())
        result = await _probe_version("/bin/echo", ("--version",))
        assert result == "version 1.2.3"

    async def test_empty_stdout_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeProc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return (b"", b"")

            async def wait(self) -> None:
                return None

            def kill(self) -> None:
                pass

        _spawn(monkeypatch, FakeProc())
        assert await _probe_version("/bin/echo", ("--version",)) is None

    async def test_nonzero_exit_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeProc:
            returncode = 1

            async def communicate(self) -> tuple[bytes, bytes]:
                return (b"should not be used", b"")

            async def wait(self) -> None:
                return None

            def kill(self) -> None:
                pass

        _spawn(monkeypatch, FakeProc())
        assert await _probe_version("/bin/echo", ("--version",)) is None

    async def test_oserror_during_spawn_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_exec(*_a: Any, **_kw: Any) -> None:
            raise FileNotFoundError("nope")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        assert await _probe_version("/bin/missing", ("--version",)) is None

    async def test_timeout_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeProc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                raise TimeoutError

            async def wait(self) -> None:
                return None

            def kill(self) -> None:
                pass

        _spawn(monkeypatch, FakeProc())
        assert await _probe_version("/bin/echo", ("--version",)) is None

    async def test_decode_error_returns_stripped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeProc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                # UTF-8 invalid byte — should still produce a usable string.
                return (b"v1.0\xc3\x28invalid", b"")

            async def wait(self) -> None:
                return None

            def kill(self) -> None:
                pass

        _spawn(monkeypatch, FakeProc())
        result = await _probe_version("/bin/echo", ("--version",))
        assert result is not None
        assert result.startswith("v1.0")
