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

import json
from pathlib import Path
from typing import Any

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
    utc_now_ms,
)
from powerbi_orchestrator_mcp.engines.errors import EngineError
from powerbi_orchestrator_mcp.orchestrator.context import (
    Target,
)

DEFAULT_PINNED_VERSION = "1.0.0"

_RPC_TO_MCP_TOOL: dict[str, str] = {
    "database_operations/list_tables": "table_operations",
    "column_operations/list": "column_operations",
    "measure_operations/list": "measure_operations",
    "database_operations/list_relationships": "relationship_operations",
    "column_operations/update": "column_operations",
    "measure_operations/create": "measure_operations",
    "measure_operations/update": "measure_operations",
    "measure_operations/delete": "measure_operations",
    "dax_query_operations/run": "dax_query_operations",
    "database_operations/export_tmdl": "database_operations",
    "database_operations/import_tmdl": "database_operations",
}


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
        effective_env = dict(env or {})
        if "PBI_MODELING_MCP_ACCEPT_EULA" not in effective_env:
            effective_env["PBI_MODELING_MCP_ACCEPT_EULA"] = "true"

        super().__init__(
            engine_name="powerbi-modeling-mcp",
            binary=binary,
            args=(
                "-y",
                f"@microsoft/powerbi-modeling-mcp@{version}",
                "--start",
                "--accept-eula",
            ),
            env=effective_env,
            mcp_handshake=True,
        )
        self._version = version
        self._mock_responses = mock_responses or {}
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
        raw_tables = result.get("tables") or result.get("data", [])
        return [
            Table(
                name=t["name"],
                is_hidden=t.get("isHidden") if "isHidden" in t else t.get("is_hidden", False),
            )
            for t in raw_tables
        ]

    async def list_measures(self, conn: ConnectionHandle) -> list[Measure]:
        result = await self._dispatch("measure_operations", "list", conn)
        raw_measures = result.get("measures") or result.get("data", [])
        return [
            Measure(
                name=m["name"],
                table=m.get("tableName") or m.get("table", ""),
                expression=m.get("expression", ""),
                format_string=m.get("formatString") or m.get("format_string"),
                description=m.get("description"),
                is_hidden=m.get("isHidden") if "isHidden" in m else m.get("is_hidden", False),
            )
            for m in raw_measures
        ]

    async def list_columns(self, conn: ConnectionHandle, table: str) -> list[Column]:
        result = await self._dispatch("column_operations", "list", conn, extra={"table": table})
        raw_columns = result.get("columns") or result.get("data", [])
        return [
            Column(
                name=c["name"],
                data_type=c.get("dataType") or c.get("data_type", "string"),
                description=c.get("description"),
                is_hidden=c.get("isHidden") if "isHidden" in c else c.get("is_hidden", False),
                is_key=c.get("isKey") if "isKey" in c else c.get("is_key", False),
                format_string=c.get("formatString") or c.get("format_string"),
            )
            for c in raw_columns
        ]

    async def list_relationships(self, conn: ConnectionHandle) -> list[Relationship]:
        result = await self._dispatch("database_operations", "list_relationships", conn)
        raw_rels = result.get("relationships") or result.get("data", [])
        return [
            Relationship(
                from_table=r.get("fromTable") or r.get("from_table", ""),
                from_column=r.get("fromColumn") or r.get("from_column", ""),
                to_table=r.get("toTable") or r.get("to_table", ""),
                to_column=r.get("toColumn") or r.get("to_column", ""),
                cardinality=r.get("cardinality", "many_to_one"),
                cross_filter=r.get("crossFilteringBehavior") or r.get("cross_filter", "single"),
                is_active=r.get("isActive") if "isActive" in r else r.get("is_active", True),
            )
            for r in raw_rels
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

    async def snapshot(self, conn: ConnectionHandle, label: str) -> SnapshotHandle:
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

    def _build_mcp_call(
        self,
        rpc_method: str,
        conn: ConnectionHandle | None,
        extra: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any]]:
        tool_name = _RPC_TO_MCP_TOOL.get(rpc_method, rpc_method)
        _ = conn
        req: dict[str, Any] = {}
        payload = extra or {}

        if rpc_method == "database_operations/list_tables":
            req["operation"] = "List"
        elif rpc_method == "column_operations/list":
            req["operation"] = "List"
            if "table" in payload:
                req["filter"] = {"tableNames": [payload["table"]]}
        elif rpc_method == "column_operations/update":
            changes = payload.get("changes", {})
            if "new_name" in changes:
                req["operation"] = "Rename"
                req["renameDefinitions"] = [
                    {
                        "tableName": payload.get("table", ""),
                        "currentName": payload.get("column", ""),
                        "newName": changes["new_name"],
                    }
                ]
            else:
                def_item = {
                    "tableName": payload.get("table", ""),
                    "name": payload.get("column", ""),
                    **changes,
                }
                req["operation"] = "Update"
                req["definitions"] = [def_item]
        elif rpc_method == "measure_operations/list":
            req["operation"] = "List"
        elif rpc_method == "measure_operations/create":
            req["operation"] = "Create"
            meas = payload.get("measure", {})
            req["definitions"] = [
                {
                    "tableName": payload.get("table", ""),
                    "name": meas.get("name", ""),
                    "expression": meas.get("expression", ""),
                }
            ]
        elif rpc_method == "measure_operations/update":
            req["operation"] = "Update"
            changes = payload.get("changes", {})
            def_item = {
                "tableName": payload.get("table", ""),
                "name": payload.get("measure", ""),
                **changes,
            }
            req["definitions"] = [def_item]
        elif rpc_method == "measure_operations/delete":
            req["operation"] = "Delete"
            req["references"] = [
                {
                    "tableName": payload.get("table", ""),
                    "name": payload.get("measure", ""),
                }
            ]
        elif rpc_method == "database_operations/list_relationships":
            req["operation"] = "List"
        elif rpc_method == "dax_query_operations/run":
            req["operation"] = "Execute"
            req["query"] = payload.get("query", "")
            req["resultMode"] = "Inline"
            if payload.get("effective_identity"):
                req["impersonation"] = payload["effective_identity"]
        elif rpc_method == "database_operations/export_tmdl":
            req["operation"] = "ExportToTmdlFolder"
            folder = payload.get("path") or payload.get("label", "")
            req["tmdlFolderPath"] = str(folder)
        elif rpc_method == "database_operations/import_tmdl":
            req["operation"] = "ImportFromTmdlFolder"
            folder = payload.get("path", "")
            req["tmdlFolderPath"] = str(folder)
        else:
            op_name = rpc_method.split("/")[-1]
            req["operation"] = op_name
            req.update(payload)

        return tool_name, {"request": req}

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
            if rpc_method in _RPC_TO_MCP_TOOL:
                mcp_tool, mcp_args = self._build_mcp_call(rpc_method, conn, extra)
                resp = await self._rpc("tools/call", {"name": mcp_tool, "arguments": mcp_args})
                if isinstance(resp, dict):
                    if resp.get("isError"):
                        err_msg = ""
                        for item in resp.get("content", []):
                            if isinstance(item, dict) and item.get("type") == "text":
                                err_msg += item.get("text", "")
                        raise EngineError(
                            f"{self._engine_name} error in {mcp_tool}: {err_msg}",
                            engine=self._engine_name,
                            code="engine_mcp_tool_error",
                            remediation_hint=err_msg,
                        )
                    if "content" in resp:
                        for item in resp.get("content", []):
                            if isinstance(item, dict) and item.get("type") == "text":
                                try:
                                    parsed = json.loads(item.get("text", "{}"))
                                    if isinstance(parsed, dict):
                                        return parsed
                                except Exception:
                                    pass
                return resp
            return await self._rpc(rpc_method, params)
        except EngineError:
            raise
        except Exception as exc:
            from powerbi_orchestrator_mcp.engines.base import map_subprocess_error

            raise map_subprocess_error(self._engine_name, exc, rpc_method) from exc
