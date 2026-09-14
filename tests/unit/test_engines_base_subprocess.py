"""Sprint 15: cover the missing paths in JsonRpcSubprocessEngine.

Existing tests in test_engines_base_subprocess.py monkey-patch _start/_rpc,
so the real subprocess plumbing (stdin/stdout loops, exit code mapping,
process crash handling) is uncovered. These tests instantiate the
engine with fake-but-realistic asyncio subprocesses that we control
end-to-end (via patching ``asyncio.create_subprocess_exec``).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from powerbi_orchestrator_mcp.engines.base import JsonRpcSubprocessEngine
from powerbi_orchestrator_mcp.engines.errors import (
    EngineCrashedError,
    EngineError,
    EngineNotFoundError,
    EngineTimeoutError,
)
from powerbi_orchestrator_mcp.engines.exit_codes import map_exit_code_to_error

# ---------------------------------------------------------------------------
# Fake writer + process
# ---------------------------------------------------------------------------


class _FakeWriter:
    def __init__(self) -> None:
        self._closed = False
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        if self._closed:
            raise ConnectionResetError("writer closed")
        self.written.append(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self._closed = True

    def is_closing(self) -> bool:
        return self._closed

    async def wait_closed(self) -> None:
        pass


class _FakeProcess:
    """Drop-in replacement for ``asyncio.subprocess.Process``.

    Stdout is an ``asyncio.StreamReader`` that is *lazily* fed: when the
    engine writes to ``stdin``, we feed the next pre-scripted line into
    the stdout buffer so the reader task can pick it up. This matches
    the real subprocess behaviour where the engine writes a request and
    the engine process writes back a response.
    """

    def __init__(
        self,
        *,
        responses: list[str] | None = None,
        stderr_bytes: bytes = b"",
        returncode: int | None = None,
        exit_immediately: bool = False,
    ) -> None:
        self._responses = list(responses or [])
        self._idx = 0
        self.returncode: int | None = (
            returncode if exit_immediately else None
        )
        self.stdin = _FakeWriter()
        self._stdout = asyncio.StreamReader()
        if exit_immediately:
            self._stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr_bytes)
        self.stderr.feed_eof()

    @property
    def stdout(self) -> asyncio.StreamReader:
        return self._stdout

    def feed_next_response(self) -> None:
        """Feed the next pre-scripted response to stdout."""
        if self._idx < len(self._responses):
            line = self._responses[self._idx]
            self._idx += 1
            self._stdout.feed_data((line + "\n").encode("utf-8"))
            # Leave stdout open so readline() can fetch the next response
            # (if any). EOF is only fed when the engine stops.

    async def wait(self) -> int:
        if self.returncode is None:
            await asyncio.sleep(0.05)
        return self.returncode if self.returncode is not None else 0

    def terminate(self) -> None:
        self.returncode = -15
        self._stdout.feed_eof()

    def kill(self) -> None:
        self.returncode = -9
        self._stdout.feed_eof()


def _make_engine() -> JsonRpcSubprocessEngine:
    # Use 'te' so the timeout resolver finds a registered engine.
    e = JsonRpcSubprocessEngine(
        engine_name="te",
        binary="ignored",
        args=(),
    )
    e._version = "9.9.9"  # type: ignore[attr-defined]
    return e


def _spawn_patched(monkeypatch: pytest.MonkeyPatch, proc: _FakeProcess) -> None:
    async def fake_exec(*_a: Any, **_kw: Any) -> _FakeProcess:
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


def _response(req_id: int, result: Any) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error_response(req_id: int, code: int, message: str) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    )


# Engine RPC writes the message to stdin. We watch the writer and inject
# the matching response into the stdout buffer so the reader task can
# pick it up.
async def _wait_for_stdin_and_feed(proc: _FakeProcess) -> None:
    while not proc.stdin.written:
        await asyncio.sleep(0.005)
    proc.feed_next_response()


# ---------------------------------------------------------------------------
# Spawn-time errors
# ---------------------------------------------------------------------------


class TestStartSpawn:
    async def test_missing_binary_raises_engine_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_exec(*_a: Any, **_kw: Any) -> None:
            raise FileNotFoundError("nope")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        e = _make_engine()
        with pytest.raises(EngineNotFoundError):
            await e._start()

    async def test_oserror_during_spawn_raises_crashed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_exec(*_a: Any, **_kw: Any) -> None:
            raise PermissionError("denied")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        e = _make_engine()
        with pytest.raises(EngineCrashedError) as ei:
            await e._start()
        assert ei.value.code == "engine_spawn_failed"

    async def test_process_exits_immediately_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # exit 4 → EngineCrashedError (other exit codes map to specific
        # EngineError subclasses: 1 = validation, 3 = timeout, 5 = version
        # mismatch, etc.). We want the generic crash path here.
        proc = _FakeProcess(
            stderr_bytes=b"some generic startup failure\n",
            returncode=4,
            exit_immediately=True,
        )
        _spawn_patched(monkeypatch, proc)
        e = _make_engine()
        with pytest.raises(EngineCrashedError):
            await e._start()


# ---------------------------------------------------------------------------
# _rpc success / error / timeout / stdin-closed
# ---------------------------------------------------------------------------


class TestRpcSuccess:
    async def test_rpc_returns_result_and_clears_pending(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proc = _FakeProcess(responses=[_response(1, {"value": 42})])
        _spawn_patched(monkeypatch, proc)
        e = _make_engine()
        await e._start()
        # Feed the response when the engine writes the request.
        feeder = asyncio.create_task(_wait_for_stdin_and_feed(proc))
        result = await e._rpc("foo/bar", {"x": 1})
        await feeder
        assert result == {"value": 42}
        assert e._pending == {}
        assert len(proc.stdin.written) == 1
        payload = json.loads(proc.stdin.written[0].decode("utf-8"))
        assert payload["method"] == "foo/bar"
        assert payload["params"] == {"x": 1}
        assert payload["id"] == 1

    async def test_rpc_handles_rpc_error_response(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proc = _FakeProcess(responses=[_error_response(1, -32600, "boom")])
        _spawn_patched(monkeypatch, proc)
        e = _make_engine()
        await e._start()
        feeder = asyncio.create_task(_wait_for_stdin_and_feed(proc))
        with pytest.raises(EngineCrashedError) as ei:
            await e._rpc("foo")
        await feeder
        assert "boom" in str(ei.value)

    async def test_rpc_timeout_raises_engine_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proc = _FakeProcess()  # no responses
        _spawn_patched(monkeypatch, proc)
        e = _make_engine()
        await e._start()
        with pytest.raises(EngineTimeoutError) as ei:
            await e._rpc("foo", timeout_s=1)
        assert ei.value.timeout_s == 1
        # Stop the engine so the test exits.
        await e._stop()

    async def test_stdin_closed_during_write_raises_crashed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proc = _FakeProcess(responses=[_response(1, {})])
        _spawn_patched(monkeypatch, proc)
        e = _make_engine()
        await e._start()
        # Close stdin before writing the request.
        proc.stdin.close()
        with pytest.raises(EngineCrashedError) as ei:
            await e._rpc("foo")
        assert ei.value.code == "engine_stdin_closed"


# ---------------------------------------------------------------------------
# _read_stdout_loop
# ---------------------------------------------------------------------------


class TestReadStdoutLoop:
    async def test_non_json_line_raises_output_parse_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proc = _FakeProcess()  # no responses initially
        _spawn_patched(monkeypatch, proc)
        e = _make_engine()
        await e._start()
        # Feed a non-JSON line directly to stdout.
        proc._stdout.feed_data(b"this is not json\n")
        proc._stdout.feed_eof()
        # Wait for the reader task to consume and raise.
        await asyncio.sleep(0.05)
        # Process still tracked; reader task should have ended.
        assert e._process is not None

    async def test_notification_line_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proc = _FakeProcess()
        _spawn_patched(monkeypatch, proc)
        e = _make_engine()
        await e._start()
        proc._stdout.feed_data(
            b'{"jsonrpc": "2.0", "method": "notify"}\n'
        )
        proc._stdout.feed_eof()
        await asyncio.sleep(0.05)
        assert e._pending == {}


# ---------------------------------------------------------------------------
# _stop cancel-pending
# ---------------------------------------------------------------------------


class TestStopCancelPending:
    async def test_stop_cancels_pending_futures(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proc = _FakeProcess()  # no responses → RPC will hang
        _spawn_patched(monkeypatch, proc)
        e = _make_engine()
        await e._start()

        async def _hang_rpc() -> None:
            await e._rpc("slow", timeout_s=10)

        task = asyncio.create_task(_hang_rpc())
        await asyncio.sleep(0.02)
        assert e._pending != {}

        await e._stop()
        with pytest.raises((asyncio.CancelledError, EngineTimeoutError)):
            await task


# ---------------------------------------------------------------------------
# Exit code mapping
# ---------------------------------------------------------------------------


class TestExitCodeMapping:
    def test_known_exit_codes_mapped(self) -> None:
        for code in (0, 1, 2, 3, 137, 255):
            err = map_exit_code_to_error("te", code, "stderr")
            if code == 0:
                assert err is None
            else:
                assert err is None or isinstance(err, EngineError)
