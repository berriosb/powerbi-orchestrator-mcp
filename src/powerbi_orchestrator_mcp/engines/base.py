"""Engine adapters — base Protocols and subprocess helper.

Implements the abstract interface from ``specs/05-engines-adapters.md`` §2.

Two Protocols are defined here:
- ``ModelingEngine`` — operations on Power BI semantic models (Capa 1).
- ``ReportEngine`` — operations on report files (Capa 2).

Plus a base class ``JsonRpcSubprocessEngine`` that handles:
- subprocess lifecycle (start, stop, restart on error).
- JSON-RPC request/response framing over stdin/stdout.
- timeout enforcement (using ``engines/timeouts.py``).
- error mapping (using ``engines/exit_codes.py``).

The ``modeling_mcp.py`` concrete adapter inherits from
``JsonRpcSubprocessEngine`` and implements the actual JSON-RPC methods
for the ``@microsoft/powerbi-modeling-mcp`` package.

Per spec 06 §7: adapters never expose subprocess implementation details
to the orchestrator — they all surface as ``EngineError`` subclasses.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.engines.errors import (
    EngineCrashedError,
    EngineError,
    EngineOutputParseError,
    EngineTimeoutError,
)
from powerbi_orchestrator_mcp.engines.exit_codes import map_exit_code_to_error
from powerbi_orchestrator_mcp.engines.timeouts import resolve_timeout
from powerbi_orchestrator_mcp.orchestrator.context import EngineStatus, Target

# ---------------------------------------------------------------------------
# Domain models shared across Modeling + Report engines
# ---------------------------------------------------------------------------


class ConnectionHandle(BaseModel):
    """Opaque token returned by ``connect()``; passed to subsequent ops.

    The handle is the engine's internal state (e.g. a Python wrapper around
    an MCP subprocess session). Serialization is not supported — handles
    are session-scoped.
    """

    engine: str
    target_type: str
    target_ref: str
    session_token: str  # engine-specific


class Table(BaseModel):
    """A table in a semantic model."""

    name: str
    description: str | None = None
    is_hidden: bool = False
    columns: list[str] = Field(default_factory=list)


class Column(BaseModel):
    """A column in a semantic model."""

    name: str
    data_type: str = "string"
    description: str | None = None
    is_hidden: bool = False
    is_key: bool = False
    format_string: str | None = None


class Measure(BaseModel):
    """A measure in a semantic model."""

    name: str
    table: str
    expression: str
    description: str | None = None
    format_string: str | None = None
    is_hidden: bool = False


class Relationship(BaseModel):
    """A relationship between two tables."""

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    cardinality: str = "many_to_one"
    cross_filter: str = "single"
    is_active: bool = True


class OperationResult(BaseModel):
    """Result of a write operation on the engine."""

    success: bool
    changed_files: list[str] = Field(default_factory=list)
    error_message: str | None = None


class DaxResult(BaseModel):
    """Result of a DAX query execution."""

    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    duration_ms: int = 0


class SnapshotHandle(BaseModel):
    """Pointer to a saved model snapshot (for rollback)."""

    label: str
    path: Path


class ValidationResult(BaseModel):
    """Result of a PBIR/PBIP validation."""

    valid: bool
    findings: list[dict[str, Any]] = Field(default_factory=list)


class PageLayout(BaseModel):
    """Layout config for a report page."""

    width: int = 1280
    height: int = 720
    mobile_first: bool = True


class VisualSpec(BaseModel):
    """Minimal visual spec for ReportEngine.add_visual."""

    visual_id: str | None = None
    type: str
    fields: dict[str, list[str]] = Field(default_factory=dict)
    position: dict[str, int] = Field(default_factory=dict)
    format: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class ModelingEngine(Protocol):
    """Engine for Capa 1 (model) operations.

    All methods are async. Methods that modify the model return
    ``OperationResult`` with ``changed_files`` for rollback to use.
    """

    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    async def health_check(self) -> EngineStatus: ...

    async def connect(self, target: Target) -> ConnectionHandle: ...

    async def disconnect(self, conn: ConnectionHandle) -> None: ...

    async def list_tables(self, conn: ConnectionHandle) -> list[Table]: ...

    async def list_measures(self, conn: ConnectionHandle) -> list[Measure]: ...

    async def list_columns(
        self, conn: ConnectionHandle, table: str
    ) -> list[Column]: ...

    async def list_relationships(
        self, conn: ConnectionHandle
    ) -> list[Relationship]: ...

    async def update_column(
        self,
        conn: ConnectionHandle,
        table: str,
        column: str,
        changes: dict[str, Any],
    ) -> OperationResult: ...

    async def create_measure(
        self, conn: ConnectionHandle, table: str, measure: Measure
    ) -> OperationResult: ...

    async def update_measure(
        self,
        conn: ConnectionHandle,
        table: str,
        measure: str,
        changes: dict[str, Any],
    ) -> OperationResult: ...

    async def delete_measure(
        self, conn: ConnectionHandle, table: str, measure: str
    ) -> OperationResult: ...

    async def execute_dax(
        self,
        conn: ConnectionHandle,
        query: str,
        effective_identity: dict[str, Any] | None = None,
    ) -> DaxResult: ...

    async def snapshot(
        self, conn: ConnectionHandle, label: str
    ) -> SnapshotHandle: ...

    async def restore_snapshot(self, handle: SnapshotHandle) -> None: ...


class ReportEngine(Protocol):
    """Engine for Capa 2 (report) operations."""

    @property
    def name(self) -> str: ...

    async def health_check(self) -> EngineStatus: ...

    async def connect(self, pbip_path: Path) -> ConnectionHandle: ...

    async def disconnect(self, conn: ConnectionHandle) -> None: ...

    async def add_page(
        self,
        conn: ConnectionHandle,
        page_name: str,
        layout: PageLayout | None = None,
    ) -> OperationResult: ...

    async def add_visual(
        self,
        conn: ConnectionHandle,
        page: str,
        visual_spec: VisualSpec,
    ) -> OperationResult: ...

    async def update_visual(
        self,
        conn: ConnectionHandle,
        page: str,
        visual_id: str,
        changes: dict[str, Any],
    ) -> OperationResult: ...

    async def propagate_rename(
        self,
        conn: ConnectionHandle,
        old_path: str,
        new_path: str,
        scope: str,
    ) -> OperationResult: ...

    async def validate_pbir(self, conn: ConnectionHandle) -> ValidationResult: ...


# ---------------------------------------------------------------------------
# Subprocess helper base class
# ---------------------------------------------------------------------------


class JsonRpcSubprocessEngine:
    """Base class for adapters that talk to engines via subprocess + JSON-RPC.

    Handles:
    - Subprocess lifecycle (start with timeout, terminate on shutdown).
    - JSON-RPC request/response correlation by id.
    - Stderr capture (truncated, attached to EngineError on failure).
    - Exit code mapping (via ``map_exit_code_to_error``).

    Subclasses implement ``_build_command()`` and the actual JSON-RPC
    method dispatch in ``_call_rpc()``.
    """

    def __init__(
        self,
        engine_name: str,
        binary: str,
        args: tuple[str, ...],
        *,
        env: dict[str, str] | None = None,
    ) -> None:
        self._engine_name = engine_name
        self._binary = binary
        self._args = args
        self._env = env or dict(os.environ)
        self._process: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Subprocess lifecycle
    # ------------------------------------------------------------------

    async def _start(self, *, timeout_s: int | None = None) -> None:
        """Start the subprocess. Idempotent (no-op if already running)."""
        if self._process is not None and self._process.returncode is None:
            return
        # NOTE: timeout_s here is a placeholder for future start-time probes
        # (e.g. "did the engine print 'ready' within 5s?"). For now the
        # spawn itself is sync; the per-RPC timeout is applied in _rpc().
        _ = timeout_s
        try:
            self._process = await asyncio.create_subprocess_exec(
                self._binary,
                *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
            )
        except FileNotFoundError as exc:
            from powerbi_orchestrator_mcp.engines.errors import EngineNotFoundError

            raise EngineNotFoundError(
                f"{self._engine_name} binary not found: {self._binary}",
                engine=self._engine_name,
                code="engine_not_found",
                remediation_hint=(
                    f"Install {self._engine_name} per docs/engines-setup.md"
                ),
            ) from exc
        except OSError as exc:
            raise EngineCrashedError(
                f"failed to spawn {self._engine_name}: {exc}",
                engine=self._engine_name,
                code="engine_spawn_failed",
                remediation_hint=(
                    f"Check binary permissions and PATH for {self._engine_name}"
                ),
            ) from exc

        # Spawn reader task for stdout.
        asyncio.create_task(self._read_stdout_loop())

        # Wait briefly for the process to stabilize.
        await asyncio.sleep(0.05)
        if self._process.returncode is not None:
            stderr_bytes = await self._process.stderr.read() if self._process.stderr else b""
            stderr = stderr_bytes.decode("utf-8", errors="replace")[:500]
            err = map_exit_code_to_error(
                self._engine_name, self._process.returncode, stderr
            )
            if err is None:
                err = EngineCrashedError(
                    f"{self._engine_name} exited immediately",
                    engine=self._engine_name,
                    code="engine_exited_early",
                    remediation_hint="Check engine logs / installation",
                )
            raise err

    async def _stop(self) -> None:
        """Gracefully stop the subprocess."""
        proc = self._process
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.is_closing():
                proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass
        import contextlib

        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (TimeoutError, ProcessLookupError):
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except (TimeoutError, ProcessLookupError):
                proc.kill()
                with contextlib.suppress(ProcessLookupError):
                    await proc.wait()
        finally:
            self._process = None
            # Cancel any pending RPCs.
            for fut in self._pending.values():
                if not fut.done():
                    fut.cancel()
            self._pending.clear()

    async def health_check(self) -> EngineStatus:
        """Probe the subprocess: start it if not running, run --version probe.

        Returns EngineStatus with available=True/False and version if found.
        """
        try:
            await self._start()
        except EngineError as err:
            return EngineStatus(
                name=self._engine_name,
                available=False,
                version=None,
                reason_unavailable=err.remediation_hint,
            )
        # For now, version detection is best-effort via env or hard-coded.
        # Real implementations would RPC a `version` method.
        return EngineStatus(
            name=self._engine_name,
            available=True,
            version=getattr(self, "_version", None),
            reason_unavailable=None,
        )

    # ------------------------------------------------------------------
    # JSON-RPC over stdio
    # ------------------------------------------------------------------

    async def _rpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_s: int | None = None,
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and await the matching response.

        Timeout / output / exit errors are mapped to EngineError subclasses.
        """
        if self._process is None or self._process.returncode is not None:
            await self._start()

        request_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = fut

        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        message = json.dumps(payload) + "\n"
        try:
            self._process.stdin.write(message.encode("utf-8"))  # type: ignore[union-attr]
            await self._process.stdin.drain()  # type: ignore[union-attr]
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            del self._pending[request_id]
            raise EngineCrashedError(
                f"{self._engine_name} stdin closed: {exc}",
                engine=self._engine_name,
                code="engine_stdin_closed",
                remediation_hint="Restart the engine process",
            ) from exc

        effective_timeout = resolve_timeout(self._engine_name, requested_s=timeout_s)
        try:
            response = await asyncio.wait_for(fut, timeout=effective_timeout)
        except TimeoutError as exc:
            del self._pending[request_id]
            raise EngineTimeoutError(
                f"{self._engine_name} {method}() timed out after "
                f"{effective_timeout}s",
                engine=self._engine_name,
                code="engine_timeout",
                remediation_hint=(
                    "Increase PBI_ENGINE_TIMEOUT_<ENGINE>_S or reduce the "
                    "operation size"
                ),
                timeout_s=effective_timeout,
            ) from exc

        if "error" in response:
            err = response["error"]
            raise EngineCrashedError(
                f"{self._engine_name} RPC {method} error: "
                f"{err.get('message', err)}",
                engine=self._engine_name,
                code=f"rpc_error_{err.get('code', 'unknown')}",
                remediation_hint=(
                    f"Inspect {self._engine_name} stderr in audit log; "
                    f"may be a contract drift"
                ),
            )

        return response.get("result", {})  # type: ignore[no-any-return]

    async def _read_stdout_loop(self) -> None:
        """Background task: read JSON-RPC responses from subprocess stdout."""
        proc = self._process
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break  # EOF: subprocess closed stdout
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue
                try:
                    response = json.loads(line_str)
                except json.JSONDecodeError as exc:
                    raise EngineOutputParseError(
                        f"{self._engine_name} stdout not JSON: {line_str[:200]}",
                        engine=self._engine_name,
                        code="engine_output_parse_error",
                        remediation_hint=(
                            f"Inspect raw stdout; likely a {self._engine_name} "
                            f"contract violation"
                        ),
                    ) from exc
                request_id = response.get("id")
                if request_id is None:
                    # Notification or invalid; ignore for MVP.
                    continue
                fut = self._pending.pop(request_id, None)
                if fut is not None and not fut.done():
                    fut.set_result(response)
        except (asyncio.CancelledError, GeneratorExit):
            pass
        except Exception:  # noqa: BLE001
            # Any unexpected read error → fail all pending RPCs.
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(
                        EngineCrashedError(
                            f"{self._engine_name} stdout reader crashed",
                            engine=self._engine_name,
                            code="engine_stdout_reader_crashed",
                            remediation_hint="Restart the engine",
                        )
                    )
            self._pending.clear()


# ---------------------------------------------------------------------------
# Error-mapping helper
# ---------------------------------------------------------------------------


def map_subprocess_error(
    engine: str, exc: Exception, op: str
) -> EngineError:
    """Convert a raw subprocess exception into an EngineError subclass.

    Used by adapter methods that call ``_rpc()`` and want a uniform
    error path even if the underlying call raises a non-EngineError
    (e.g. ``asyncio.IncompleteReadError``).
    """
    if isinstance(exc, EngineError):
        return exc
    return EngineCrashedError(
        f"{engine} {op} unhandled error: {exc}",
        engine=engine,
        code="engine_unhandled",
        remediation_hint=f"Check {engine} state; may need restart",
    )


__all__ = [
    "Column",
    "ConnectionHandle",
    "DaxResult",
    "JsonRpcSubprocessEngine",
    "Measure",
    "ModelingEngine",
    "OperationResult",
    "PageLayout",
    "Relationship",
    "ReportEngine",
    "SnapshotHandle",
    "Table",
    "ValidationResult",
    "VisualSpec",
    "map_subprocess_error",
]


# ---------------------------------------------------------------------------
# Development helpers
# ---------------------------------------------------------------------------


def utc_now_ms() -> int:
    """Current epoch in milliseconds — handy for ``DaxResult.duration_ms``."""
    return int(time.time() * 1000)
