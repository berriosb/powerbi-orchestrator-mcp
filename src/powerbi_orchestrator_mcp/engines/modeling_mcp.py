"""Adapter for the ``@microsoft/powerbi-modeling-mcp`` package.

Implements ``specs/05-engines-adapters.md`` §3.

The package is an MCP server (Node.js) that we run as a subprocess and
talk to via JSON-RPC over stdio. Our wrapper translates the spec's
``ModelingEngine`` Protocol into the actual MCP tool calls.

For MVP this implementation focuses on the operations needed by
``safe_rename`` (the showpiece tool of MVP):
- ``list_tables`` / ``list_columns`` (impact analysis)
- ``update_column`` (the rename)
- ``snapshot`` / ``restore_snapshot`` (rollback)
- ``execute_dax`` (validation)

Other operations (``create_measure``, ``update_measure``,
``delete_measure``, ``list_measures``, ``list_relationships``) are
also wired through ``_dispatch``; the MCP method names are
discoverable via ``_MCP_METHOD_NAMES`` below — they will need to be
verified against a real binary before the v2 E2E test runs land
(see ``tests/integration/`` when the CI gets a Windows runner with
the package installed).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from powerbi_orchestrator_mcp.engines.base import (
    Column,
    ConnectionHandle,
    DaxResult,
    JsonRpcSubprocessEngine,
    Measure,
    ModelingEngine,  # noqa: F401  (Protocol used as type hint)
    OperationResult,
    Relationship,
    SnapshotHandle,
    Table,
    utc_now_ms,
)
from powerbi_orchestrator_mcp.engines.errors import EngineError
from powerbi_orchestrator_mcp.orchestrator.context import (
    Target,
)

# Pinned version per specs/05 §3 + src/engines/versions.py.
DEFAULT_PINNED_VERSION = "0.1.9"


class PowerBiModelingMcpEngine(JsonRpcSubprocessEngine):
    """Adapter for the ``@microsoft/powerbi-modeling-mcp`` MCP server.

    The MCP package is launched as a subprocess (``npx -y
    @microsoft/powerbi-modeling-mcp@<version>``) and we communicate via
    JSON-RPC over its stdio. Connection handles in this adapter are
    thin wrappers around the MCP session token — no real subprocess
    is forked per ``connect()`` (we share one for the session).
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
            engine_name="powerbi-modeling-mcp",
            binary=binary,
            args=("-y", f"@microsoft/powerbi-modeling-mcp@{version}"),
            env=env,
        )
        self._version = version
        self._mock_responses = mock_responses or {}
        # Records every (method, params) passed to _dispatch — useful
        # for tests that need to assert the params built by the adapter
        # (since mock_responses short-circuit _rpc).
        self.dispatch_calls: list[tuple[str, dict[str, Any]]] = []

    # ------------------------------------------------------------------
    # ModelingEngine Protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._engine_name

    @property
    def version(self) -> str:
        return self._version

    async def connect(self, target: Target) -> ConnectionHandle:
        """Open a logical connection to the target via the MCP server.

        Starts the subprocess if not running. The connection handle
        carries the target identity but doesn't bind to a specific
        subprocess (the subprocess is shared across connections).
        """
        await self._start()
        return ConnectionHandle(
            engine=self._engine_name,
            target_type=target.target_type,
            target_ref=target.target_ref,
            session_token=f"{target.target_type}:{target.target_ref}",
        )

    async def disconnect(self, conn: ConnectionHandle) -> None:
        """Logical disconnect. Stops the subprocess if no conns remain.

        MVP: the subprocess is shared, so we only stop on full shutdown.
        Real implementation should track per-connection counts.
        """
        _ = conn  # unused for MVP

    # ------------------------------------------------------------------
    # Read operations (spec §3.2 mapping)
    # ------------------------------------------------------------------

    async def list_tables(self, conn: ConnectionHandle) -> list[Table]:
        result = await self._dispatch("database_operations", "list_tables", conn)
        return [Table(**t) for t in result.get("tables", [])]

    async def list_measures(self, conn: ConnectionHandle) -> list[Measure]:
        result = await self._dispatch("measure_operations", "list", conn)
        return [Measure(**m) for m in result.get("measures", [])]

    async def list_columns(
        self, conn: ConnectionHandle, table: str
    ) -> list[Column]:
        result = await self._dispatch(
            "column_operations", "list", conn, extra={"table": table}
        )
        return [Column(**c) for c in result.get("columns", [])]

    async def list_relationships(
        self, conn: ConnectionHandle
    ) -> list[Relationship]:
        result = await self._dispatch(
            "database_operations", "list_relationships", conn
        )
        return [
            Relationship(
                from_table=r["from_table"],
                from_column=r["from_column"],
                to_table=r["to_table"],
                to_column=r["to_column"],
                cardinality=r.get("cardinality", "many_to_one"),
                cross_filter=r.get("cross_filter", "single"),
                is_active=r.get("is_active", True),
            )
            for r in result.get("relationships", [])
        ]

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def update_column(
        self,
        conn: ConnectionHandle,
        table: str,
        column: str,
        changes: dict[str, Any],
    ) -> OperationResult:
        result = await self._dispatch(
            "column_operations",
            "update",
            conn,
            extra={"table": table, "column": column, "changes": changes},
        )
        return OperationResult(
            success=True,
            changed_files=result.get("changed_files", []),
        )

    async def create_measure(
        self, conn: ConnectionHandle, table: str, measure: Measure
    ) -> OperationResult:
        result = await self._dispatch(
            "measure_operations",
            "create",
            conn,
            extra={"table": table, "measure": measure.model_dump(mode="json")},
        )
        return OperationResult(
            success=True,
            changed_files=result.get("changed_files", []),
        )

    async def update_measure(
        self,
        conn: ConnectionHandle,
        table: str,
        measure: str,
        changes: dict[str, Any],
    ) -> OperationResult:
        result = await self._dispatch(
            "measure_operations",
            "update",
            conn,
            extra={"table": table, "measure": measure, "changes": changes},
        )
        return OperationResult(
            success=True,
            changed_files=result.get("changed_files", []),
        )

    async def delete_measure(
        self, conn: ConnectionHandle, table: str, measure: str
    ) -> OperationResult:
        result = await self._dispatch(
            "measure_operations",
            "delete",
            conn,
            extra={"table": table, "measure": measure},
        )
        return OperationResult(
            success=True,
            changed_files=result.get("changed_files", []),
        )

    # ------------------------------------------------------------------
    # DAX execution
    # ------------------------------------------------------------------

    async def execute_dax(
        self,
        conn: ConnectionHandle,
        query: str,
        effective_identity: dict[str, Any] | None = None,
    ) -> DaxResult:
        start = utc_now_ms()
        result = await self._dispatch(
            "dax_query_operations",
            "run",
            conn,
            extra={"query": query, "effective_identity": effective_identity},
        )
        rows = result.get("rows", [])
        return DaxResult(
            rows=rows,
            row_count=len(rows),
            duration_ms=utc_now_ms() - start,
        )

    # ------------------------------------------------------------------
    # Snapshot / rollback
    # ------------------------------------------------------------------

    async def snapshot(
        self, conn: ConnectionHandle, label: str
    ) -> SnapshotHandle:
        """Export the TMDL to a temp dir tagged with ``label``.

        Returns a SnapshotHandle pointing to the exported directory.
        """
        result = await self._dispatch(
            "database_operations",
            "export_tmdl",
            conn,
            extra={"label": label},
        )
        return SnapshotHandle(label=label, path=Path(result["path"]))

    async def restore_snapshot(self, handle: SnapshotHandle) -> None:
        await self._dispatch(
            "database_operations",
            "import_tmdl",
            # No conn needed — the handle carries everything.
            conn=None,
            extra={"label": handle.label, "path": str(handle.path)},
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
        """Dispatch a JSON-RPC call.

        If ``mock_responses`` was set on the adapter, return the canned
        response (used by tests). Otherwise send via JSON-RPC.
        """
        rpc_method = f"{namespace}/{method}"
        params: dict[str, Any] = dict(extra or {})
        if conn is not None:
            params["target"] = {
                "target_type": conn.target_type,
                "target_ref": conn.target_ref,
                "session_token": conn.session_token,
            }
        self.dispatch_calls.append((rpc_method, params))
        if rpc_method in self._mock_responses:
            return self._mock_responses[rpc_method]  # type: ignore[no-any-return]
        try:
            return await self._rpc(rpc_method, params)
        except EngineError:
            raise
        except Exception as exc:
            from powerbi_orchestrator_mcp.engines.base import map_subprocess_error

            raise map_subprocess_error(self._engine_name, exc, rpc_method) from exc
