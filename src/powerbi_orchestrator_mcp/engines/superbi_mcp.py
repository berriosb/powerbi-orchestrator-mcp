"""Adapter for the ``superbi-mcp`` package (Windows-focused report engine).

Implements the second-tier ``ReportEngine`` in
``specs/05-engines-adapters.md`` §3 + §10.

The adapter is structurally identical to ``PowerBiModelingMcpEngine``
(same MCP-over-JSON-RPC pattern) but bound to ``superbi-mcp``'s tool
namespace. Differences from ``python_report``:

- Richer report operations (M transformations, page-level queries).
- Writes the legacy ``.pbix`` binary in addition to PBIR (Windows only).
- FSL-licensed (non-commercial only).

For MVP we ship a structural adapter with the right shape. Page CRUD
methods (``add_page`` / ``add_visual`` / ``update_visual``) dispatch
to superbi-mcp's ``report.*`` namespace via ``_dispatch`` and fall back
to ``PythonReportEngine`` (built-in file I/O) when the method is not
exposed by the underlying binary — that covers the ``propagate_rename``
and PBIR-write workflows while preserving the seam for future Windows
upgrades. ``propagate_rename`` and ``validate_pbir`` go through
``_dispatch`` so the wiring is correct; if the binary doesn't expose
them, the mock responses (or upstream EngineError) surface to the
caller.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from powerbi_orchestrator_mcp.engines.base import (
    ConnectionHandle,
    JsonRpcSubprocessEngine,
    OperationResult,
    PageLayout,
    ReportEngine,  # noqa: F401
    ValidationResult,
    VisualSpec,
    map_subprocess_error,
)
from powerbi_orchestrator_mcp.engines.errors import (
    EngineError,
    EngineMethodNotFoundError,
    EngineNotFoundError,
)
from powerbi_orchestrator_mcp.orchestrator.context import EngineStatus

# Pinned version per spec 05 §10 (versions.py in engines/).
DEFAULT_PINNED_VERSION = "1.5.0"

# The executable the package installs. Its presence on PATH is what
# tells the orchestrator the engine is installed.
_PACKAGE_BIN = "superbi-mcp"


def _method_unavailable(exc: Exception) -> bool:
    """True when the engine simply does not offer the operation.

    The built-in engine is the documented fallback for that case, and
    only for that case. A crash, a timeout, an authentication problem
    or a contract drift is a real error: falling back to the built-in
    engine would hide it behind a success.
    """
    return isinstance(exc, (EngineNotFoundError, EngineMethodNotFoundError))


def _write_succeeded(result: dict[str, Any]) -> bool:
    """True when the engine reported the write as successful.

    The adapter used to default to success, so a write the engine had
    refused was reported as done. A payload that states the write
    failed is honoured; a payload without a verdict means the call was
    accepted without raising, which is the success signal the MCP
    contract gives.
    """
    if not isinstance(result, dict):
        return False
    return result.get("success") is not False


def _validation_passed(result: dict[str, Any]) -> bool:
    """True when the engine reported the report as valid.

    The default used to be valid, so a response without a verdict was
    read as a clean report. A validation that returned no verdict has
    proved nothing, so it is not a pass.
    """
    if not isinstance(result, dict):
        return False
    value = result.get("valid")
    if value is None:
        return False
    return bool(value)


class SuperBiMcpEngine(JsonRpcSubprocessEngine):
    """Adapter for the ``superbi-mcp`` MCP server.

    Windows-primary but runs on macOS/Linux via ``npx``. License is FSL;
    commercial users must acquire a separate license from the upstream.
    """

    def __init__(
        self,
        binary: str = "npx",
        version: str = DEFAULT_PINNED_VERSION,
        *,
        env: dict[str, str] | None = None,
        mock_responses: dict[str, Any] | None = None,
    ) -> None:
        """Configure the adapter.

        Args:
            binary: Path to ``npx`` (or any compatible launcher).
            version: Pinned package version.
            env: Optional environment overrides.
            mock_responses: If set, ``_rpc()`` returns these canned
                responses instead of dispatching JSON-RPC. Used by tests.
        """
        super().__init__(
            engine_name="superbi-mcp",
            binary=binary,
            args=("-y", f"superbi-mcp@{version}"),
            env=env,
            mcp_handshake=True,
        )
        self._version = version
        self._mock_responses = mock_responses or {}
        self.dispatch_calls: list[tuple[str, dict[str, Any]]] = []

    def is_available(self) -> bool:
        if os.environ.get("PBI_DISABLE_FSL_ENGINES", "").lower() in (
            "1",
            "true",
            "yes",
        ) or os.environ.get("PBI_COMMERCIAL_MODE", "").lower() in ("1", "true", "yes"):
            return False
        import shutil

        if not (shutil.which(self._binary) or os.path.exists(self._binary)):
            return False
        # The adapter launches the package through `npx -y`, which
        # downloads it when it is missing. The orchestrator does not
        # install engines by itself — see `engine_detector` — so the
        # package has to be installed before this engine is selected.
        return shutil.which(_PACKAGE_BIN) is not None

    # ------------------------------------------------------------------
    # ReportEngine Protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._engine_name

    @property
    def version(self) -> str:
        return self._version

    async def health_check(self) -> EngineStatus:
        if os.environ.get("PBI_DISABLE_FSL_ENGINES", "").lower() in (
            "1",
            "true",
            "yes",
        ) or os.environ.get("PBI_COMMERCIAL_MODE", "").lower() in ("1", "true", "yes"):
            return EngineStatus(
                name=self._engine_name,
                available=False,
                version=None,
                reason_unavailable="disabled in commercial mode (FSL license; use python_report)",
            )
        try:
            await self._start()
        except EngineError as err:
            return EngineStatus(
                name=self._engine_name,
                available=False,
                version=None,
                reason_unavailable=err.remediation_hint,
            )
        return EngineStatus(
            name=self._engine_name,
            available=True,
            version=self._version,
            reason_unavailable=None,
        )

    async def connect(self, pbip_path: Path) -> ConnectionHandle:
        """Open a logical connection to the PBIP via superbi-mcp."""
        if os.environ.get("PBI_DISABLE_FSL_ENGINES", "").lower() in (
            "1",
            "true",
            "yes",
        ) or os.environ.get("PBI_COMMERCIAL_MODE", "").lower() in ("1", "true", "yes"):
            from powerbi_orchestrator_mcp.engines.errors import (
                EngineValidationError,
            )

            raise EngineValidationError(
                "superbi-mcp is disabled in commercial mode due to FSL license. Use python_report for MIT-compliant operation.",
                engine=self._engine_name,
                code="engine_validation_failed",
                remediation_hint="Use python_report for MIT-compliant operation or unset PBI_COMMERCIAL_MODE",
            )
        await self._start()
        if not pbip_path.exists():
            from powerbi_orchestrator_mcp.engines.errors import (
                EngineValidationError,
            )

            raise EngineValidationError(
                f"PBIP path does not exist: {pbip_path}",
                engine=self._engine_name,
                code="engine_validation_failed",
                remediation_hint="Check the PBIP path",
            )
        return ConnectionHandle(
            engine=self._engine_name,
            target_type="pbip_folder",
            target_ref=str(pbip_path),
            session_token=str(pbip_path),
        )

    async def disconnect(self, conn: ConnectionHandle) -> None:
        _ = conn  # MVP: no-op

    # ------------------------------------------------------------------
    # Page CRUD — dispatch with python_report fallback
    # ------------------------------------------------------------------

    async def add_page(
        self,
        conn: ConnectionHandle,
        page_name: str,
        layout: PageLayout | None = None,
    ) -> OperationResult:
        """Add a page via superbi-mcp's ``report.add_page``.

        Falls back to ``PythonReportEngine.add_page`` when the binary
        doesn't expose this method (no mock and RPC fails). This keeps
        the wiring correct for future Windows upgrades while ensuring
        MVP coverage.
        """
        from powerbi_orchestrator_mcp.engines.report_python import (
            PythonReportEngine,
        )

        try:
            result = await self._dispatch(
                "report",
                "add_page",
                conn,
                extra={"page_name": page_name, "layout": layout},
            )
            return OperationResult(
                success=_write_succeeded(result),
                changed_files=result.get("changed_files", []),
            )
        except Exception as exc:
            if not _method_unavailable(exc):
                raise
            return await PythonReportEngine().add_page(conn, page_name, layout)

    async def add_visual(
        self,
        conn: ConnectionHandle,
        page: str,
        visual_spec: VisualSpec,
    ) -> OperationResult:
        """Add a visual via superbi-mcp's ``report.add_visual``.

        Falls back to ``PythonReportEngine.add_visual`` if the binary
        doesn't expose the method.
        """
        from powerbi_orchestrator_mcp.engines.report_python import (
            PythonReportEngine,
        )

        try:
            result = await self._dispatch(
                "report",
                "add_visual",
                conn,
                extra={"page": page, "visual_spec": visual_spec},
            )
            return OperationResult(
                success=_write_succeeded(result),
                changed_files=result.get("changed_files", []),
            )
        except Exception as exc:
            if not _method_unavailable(exc):
                raise
            return await PythonReportEngine().add_visual(conn, page, visual_spec)

    async def update_visual(
        self,
        conn: ConnectionHandle,
        page: str,
        visual_id: str,
        changes: dict[str, Any],
    ) -> OperationResult:
        """Update a visual via superbi-mcp's ``report.update_visual``.

        Falls back to ``PythonReportEngine.update_visual`` if the binary
        doesn't expose the method.
        """
        from powerbi_orchestrator_mcp.engines.report_python import (
            PythonReportEngine,
        )

        try:
            result = await self._dispatch(
                "report",
                "update_visual",
                conn,
                extra={"page": page, "visual_id": visual_id, "changes": changes},
            )
            return OperationResult(
                success=_write_succeeded(result),
                changed_files=result.get("changed_files", []),
            )
        except Exception as exc:
            if not _method_unavailable(exc):
                raise
            return await PythonReportEngine().update_visual(conn, page, visual_id, changes)

    async def propagate_rename(
        self,
        conn: ConnectionHandle,
        old_path: str,
        new_path: str,
        scope: str,
    ) -> OperationResult:
        """propagate_rename via superbi-mcp (richer semantics than python_report).

        Uses the MCP method ``report.propagate_rename`` which understands
        M-query references and cross-measure dependencies. If the binary
        doesn't expose this method (no mock and RPC fails), the
        ``PythonReportEngine`` fallback path handles it via direct
        file I/O. The dispatcher records every call so tests can assert
        on the routing.
        """
        from powerbi_orchestrator_mcp.engines.report_python import (
            PythonReportEngine,
        )

        try:
            result = await self._dispatch(
                "report",
                "propagate_rename",
                conn,
                extra={"old_path": old_path, "new_path": new_path, "scope": scope},
            )
            return OperationResult(
                success=_write_succeeded(result),
                changed_files=result.get("changed_files", []),
            )
        except Exception as exc:
            if not _method_unavailable(exc):
                raise
            return await PythonReportEngine().propagate_rename(conn, old_path, new_path, scope)

    async def validate_pbir(self, conn: ConnectionHandle) -> ValidationResult:
        """Validate PBIR via superbi-mcp's ``report.validate`` method.

        Typically more thorough than ``python_report`` (cross-references
        M expressions, validates against the .pbix binary if present).
        """
        result = await self._dispatch("report", "validate", conn)
        return ValidationResult(
            valid=_validation_passed(result),
            findings=result.get("findings", []),
        )

    # ------------------------------------------------------------------
    # Internal dispatch
    # ------------------------------------------------------------------

    async def _dispatch(
        self,
        namespace: str,
        method: str,
        conn: ConnectionHandle | None,
        *,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Dispatch a JSON-RPC call with mock-fallback to python_report.

        If the method has no mock and the real superbi-mcp doesn't expose
        it, fall back to PythonReportEngine for MVP coverage.
        """
        rpc_method = f"{namespace}/{method}"
        params: dict[str, Any] = dict(extra or {})
        if conn is not None:
            params["pbip_path"] = conn.target_ref
        self.dispatch_calls.append((rpc_method, params))

        if rpc_method in self._mock_responses:
            return self._mock_responses[rpc_method]  # type: ignore[no-any-return]

        try:
            return await self._rpc(rpc_method, params)
        except EngineError:
            raise
        except Exception as exc:
            raise map_subprocess_error(self._engine_name, exc, rpc_method) from exc
