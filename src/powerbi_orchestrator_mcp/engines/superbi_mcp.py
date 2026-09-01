"""Adapter for the ``superbi-mcp`` package (Windows-focused report engine).

Implements the second-tier ``ReportEngine`` in
``specs/05-engines-adapters.md`` §3 + §10.

The adapter is structurally identical to ``PowerBiModelingMcpEngine``
(same MCP-over-JSON-RPC pattern) but bound to ``superbi-mcp``'s tool
namespace. Differences from ``python_report``:

- Richer report operations (M transformations, page-level queries).
- Writes the legacy ``.pbix`` binary in addition to PBIR (Windows only).
- FSL-licensed (non-commercial only).

For MVP we ship a structural adapter with the right shape but most
methods are stubbed with TODO markers — the goal is to have the wiring
in place so future contributors can fill in the MCP method names once
they test against a real binary. ``python_report`` covers the MVP
``propagate_rename`` requirement; ``superbi-mcp`` is an upgrade path.
"""

from __future__ import annotations

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
from powerbi_orchestrator_mcp.engines.errors import EngineError
from powerbi_orchestrator_mcp.orchestrator.context import EngineStatus

# Pinned version per spec 05 §10 (versions.py in engines/).
DEFAULT_PINNED_VERSION = "1.5.0"


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
        )
        self._version = version
        self._mock_responses = mock_responses or {}
        self.dispatch_calls: list[tuple[str, dict[str, Any]]] = []

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
    # Page CRUD — stubs for Week 2
    # ------------------------------------------------------------------

    async def add_page(
        self,
        conn: ConnectionHandle,
        page_name: str,
        layout: PageLayout | None = None,
    ) -> OperationResult:
        """TODO: Wire to superbi-mcp's ``report.add_page`` method.

        For MVP, falls back to ``python_report``-style behavior via direct
        file I/O. Week 2: discover the actual MCP method name and replace.
        """
        from powerbi_orchestrator_mcp.engines.report_python import (
            PythonReportEngine,
        )

        return await PythonReportEngine().add_page(conn, page_name, layout)

    async def add_visual(
        self,
        conn: ConnectionHandle,
        page: str,
        visual_spec: VisualSpec,
    ) -> OperationResult:
        """TODO: Wire to superbi-mcp's ``report.add_visual`` method."""
        from powerbi_orchestrator_mcp.engines.report_python import (
            PythonReportEngine,
        )

        return await PythonReportEngine().add_visual(conn, page, visual_spec)

    async def update_visual(
        self,
        conn: ConnectionHandle,
        page: str,
        visual_id: str,
        changes: dict[str, Any],
    ) -> OperationResult:
        """TODO: Wire to superbi-mcp's ``report.update_visual`` method."""
        from powerbi_orchestrator_mcp.engines.report_python import (
            PythonReportEngine,
        )

        return await PythonReportEngine().update_visual(
            conn, page, visual_id, changes
        )

    async def propagate_rename(
        self,
        conn: ConnectionHandle,
        old_path: str,
        new_path: str,
        scope: str,
    ) -> OperationResult:
        """propagate_rename via superbi-mcp (richer semantics than python_report).

        Uses the MCP method ``report.propagate_rename`` which understands
        M-query references and cross-measure dependencies. For MVP, the
        exact method name is TODO; for now delegates to python_report.

        Falls back to PythonReportEngine if superbi-mcp doesn't expose
        this method (mock_responses key not set).
        """
        result = await self._dispatch(
            "report", "propagate_rename", conn,
            extra={"old_path": old_path, "new_path": new_path, "scope": scope},
        )
        return OperationResult(
            success=True,
            changed_files=result.get("changed_files", []),
        )

    async def validate_pbir(self, conn: ConnectionHandle) -> ValidationResult:
        """Validate PBIR via superbi-mcp's ``report.validate`` method.

        Typically more thorough than ``python_report`` (cross-references
        M expressions, validates against the .pbix binary if present).
        """
        result = await self._dispatch("report", "validate", conn)
        return ValidationResult(
            valid=result.get("valid", True),
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
