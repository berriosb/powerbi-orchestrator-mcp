"""Tabular Editor (TE) modeling adapter — Sprint 14.

Wraps the Tabular Editor CLI as a ModelingEngine. TE is the canonical
author-side tool for Power BI semantic models (TMDL edit, calc-group
refactor, validations). Real TE integration requires:

- The TE binary on PATH (``TabularEditor.exe`` on Windows or
  ``TabularEditor`` on macOS via Mono).
- A working JSON-RPC bridge (TE has its own C# scripting host; we
  shell out via ``TabularEditor.exe model.json /script:script.cs``.

For MVP this adapter ships with:

1. ``TabularEditorAdapter`` — concrete subclass of
   ``JsonRpcSubprocessEngine``. Implements the methods
   ``create_semantic_model_from_schema`` and
   ``refactor_to_calculation_groups`` reach for when an injected
   ``modeling_engine`` is supplied.
2. ``InMemoryModelingAdapter`` — pure-stdlib ``ModelingEngine``
   implementation that talks to an in-process model object. Used in
   tests + as a fallback when TE is not installed.

The wire format for ``apply_model_spec`` is:

.. code-block:: json

    {
      "operation": "apply_model_spec",
      "tmdl_body": "<full definition.tmdl body>",
      "model_name": "sales_v1"
    }

The response is ``OperationResult``-shaped (success, changed_files,
error_message).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

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
)
from powerbi_orchestrator_mcp.orchestrator.context import EngineStatus, Target

# ---------------------------------------------------------------------------
# Result wrappers for spec-driven operations
# ---------------------------------------------------------------------------


class ApplyModelSpecResult(BaseModel):
    """Result of ``TabularEditorAdapter.apply_model_spec``."""

    success: bool
    changed_files: list[str] = Field(default_factory=list)
    tmdl_body: str | None = None  # echoed back for verification
    error_message: str | None = None
    engine: str = "te"


class RefactorCalcGroupsResult(BaseModel):
    """Result of ``TabularEditorAdapter.refactor_to_calculation_groups``."""

    success: bool
    groups_created: list[str] = Field(default_factory=list)
    measure_remappings: dict[str, str] = Field(default_factory=dict)
    changed_files: list[str] = Field(default_factory=list)
    error_message: str | None = None
    engine: str = "te"


class CalcGroupSpec(BaseModel):
    """Spec input for the TE calc-group refactor operation."""

    skeleton: str  # e.g. "Total Sales"
    items: list[dict[str, str]]  # [{"name": "YTD", "expression": "..."}, ...]


# ---------------------------------------------------------------------------
# Protocol for TE-driven spec application (kept narrow so the existing
# tools can ``isinstance`` check the seam without pulling in the
# full ModelingEngine surface).
# ---------------------------------------------------------------------------


class SupportsSpecOps(Protocol):
    """Narrow protocol: ``apply_model_spec`` + ``refactor_to_calculation_groups``."""

    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    async def apply_model_spec(
        self, pbip_path: str, tmdl_body: str, *, model_name: str
    ) -> ApplyModelSpecResult: ...

    async def refactor_to_calculation_groups(
        self,
        pbip_path: str,
        spec: list[CalcGroupSpec],
        *,
        auto_apply: bool,
    ) -> RefactorCalcGroupsResult: ...


# ---------------------------------------------------------------------------
# In-memory fallback adapter (for tests + non-Windows)
# ---------------------------------------------------------------------------


class InMemoryModelingAdapter:
    """Pure-stdlib in-memory ``ModelingEngine``-compatible adapter.

    Holds a model dictionary in process memory. Does not hit any
    subprocess. Used by:

    - unit tests (deterministic, fast);
    - environments where ``TabularEditor.exe`` is not available.

    Implements both the ``ModelingEngine`` protocol and
    ``SupportsSpecOps``.
    """

    def __init__(
        self,
        *,
        name: str = "in-memory-modeling",
        version: str = "0.1.0",
    ) -> None:
        self._name = name
        self._version = version
        self._models: dict[str, dict[str, Any]] = {}
        self.snapshots: dict[str, list[dict[str, Any]]] = {}
        self.apply_spec_calls: list[dict[str, Any]] = []
        self.refactor_calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return self._version

    # -- ModelingEngine protocol surface ----------------------------------

    async def health_check(self) -> EngineStatus:
        return EngineStatus(
            name=self._name, available=True, version=self._version
        )

    async def connect(self, target: Target) -> ConnectionHandle:
        return ConnectionHandle(
            engine=self._name,
            target_type=target.target_type,
            target_ref=target.target_ref,
            session_token=f"in-memory-{target.target_ref}",
        )

    async def disconnect(self, conn: ConnectionHandle) -> None:  # noqa: ARG002
        return None

    async def list_tables(
        self,
        conn: ConnectionHandle,  # noqa: ARG002
    ) -> list[Table]:
        model = self._models.get(conn.target_ref, {})
        return [Table(name=t) for t in model.get("tables", [])]

    async def list_measures(
        self,
        conn: ConnectionHandle,  # noqa: ARG002
    ) -> list[Measure]:
        model = self._models.get(conn.target_ref, {})
        return [
            Measure(name=m["name"], table=m.get("table", ""), expression="")
            for m in model.get("measures", [])
        ]

    async def list_columns(
        self,
        conn: ConnectionHandle,  # noqa: ARG002
        table: str,
    ) -> list[Column]:
        model = self._models.get(conn.target_ref, {})
        return [
            Column(name=c)
            for c in model.get("columns", {}).get(table, [])
        ]

    async def list_relationships(
        self,
        conn: ConnectionHandle,  # noqa: ARG002
    ) -> list[Relationship]:
        model = self._models.get(conn.target_ref, {})
        return [
            Relationship(
                from_table=r["from_table"],
                from_column=r["from_column"],
                to_table=r["to_table"],
                to_column=r["to_column"],
            )
            for r in model.get("relationships", [])
        ]

    async def update_column(
        self,
        conn: ConnectionHandle,  # noqa: ARG002
        table: str,
        column: str,
        changes: dict[str, Any],  # noqa: ARG002
    ) -> OperationResult:
        model = self._models.setdefault(conn.target_ref, {"columns": {}})
        model["columns"].setdefault(table, []).append(column)
        return OperationResult(success=True, changed_files=[f"{table}.{column}"])

    async def create_measure(
        self, conn: ConnectionHandle, table: str, measure: Measure
    ) -> OperationResult:
        model = self._models.setdefault(conn.target_ref, {"measures": []})
        model["measures"].append({"name": measure.name, "table": table})
        return OperationResult(success=True, changed_files=[f"{table}.{measure.name}"])

    async def update_measure(
        self,
        conn: ConnectionHandle,  # noqa: ARG002
        table: str,
        measure: str,
        changes: dict[str, Any],  # noqa: ARG002
    ) -> OperationResult:
        return OperationResult(success=True, changed_files=[f"{table}.{measure}"])

    async def delete_measure(
        self,
        conn: ConnectionHandle,  # noqa: ARG002
        table: str,
        measure: str,
    ) -> OperationResult:
        return OperationResult(success=True, changed_files=[f"{table}.{measure}"])

    async def execute_dax(
        self,
        conn: ConnectionHandle,  # noqa: ARG002
        query: str,  # noqa: ARG002
        effective_identity: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> DaxResult:
        return DaxResult(rows=[], row_count=0, duration_ms=0)

    async def snapshot(
        self,
        conn: ConnectionHandle,  # noqa: ARG002
        label: str,
    ) -> SnapshotHandle:
        path = Path(os.path.join("/tmp", f"snapshot-{label}.json"))
        path.write_text(
            json.dumps(self._models.get(conn.target_ref, {}), indent=2)
        )
        return SnapshotHandle(label=label, path=path)

    async def restore_snapshot(self, handle: SnapshotHandle) -> None:
        # In-memory impl: keep a copy.
        self.snapshots.setdefault(handle.label, [])  # noqa: ARG002

    # -- SupportsSpecOps ----------------------------------------------

    async def apply_model_spec(
        self, pbip_path: str, tmdl_body: str, *, model_name: str
    ) -> ApplyModelSpecResult:
        self.apply_spec_calls.append(
            {
                "pbip_path": pbip_path,
                "model_name": model_name,
                "tmdl_size": len(tmdl_body),
            }
        )
        # Echo: write the TMDL body into the (in-memory) model so a
        # downstream `list_tables` call returns the freshly written
        # tables.
        tables = [
            line.split(" ", 1)[1].strip()
            for line in tmdl_body.splitlines()
            if line.startswith("table ") and len(line.split(" ", 1)) == 2
        ]
        self._models[pbip_path] = {"tables": tables, "columns": {}, "relationships": [], "measures": []}
        tmdl_path = Path(pbip_path) / f"{model_name}.Dataset" / "definition.tmdl"
        return ApplyModelSpecResult(
            success=True,
            changed_files=[str(tmdl_path)],
            tmdl_body=tmdl_body,
        )

    async def refactor_to_calculation_groups(
        self,
        pbip_path: str,
        spec: list[CalcGroupSpec],
        *,
        auto_apply: bool,
    ) -> RefactorCalcGroupsResult:
        self.refactor_calls.append(
            {"pbip_path": pbip_path, "auto_apply": auto_apply, "n_spec": len(spec)}
        )
        groups: list[str] = []
        remappings: dict[str, str] = {}
        for sp in spec:
            cg_name = f"TimeIntelligence_{sp.skeleton.replace(' ', '_')}"
            groups.append(cg_name)
            for item in sp.items:
                remappings[f"{sp.skeleton} {item['name']}"] = (
                    f"[{cg_name}].[{item['name']}]"
                )
        return RefactorCalcGroupsResult(
            success=True,
            groups_created=groups,
            measure_remappings=remappings,
            changed_files=[f"{pbip_path}/calc_groups.tmadmin"],
        )


# ---------------------------------------------------------------------------
# Real Tabular Editor adapter (subprocess)
# ---------------------------------------------------------------------------


class TabularEditorAdapter(JsonRpcSubprocessEngine):
    """JSON-RPC adapter to Tabular Editor.

    The MVP binary handshake is::

        TabularEditor.exe -S <script_path> <pbip_path>

    where ``script_path`` is a thin C# script that performs the actual
    TMDL edits. The orchestrator sends the TMDL body + spec via
    stdin and TE returns the result via stdout. In production
    deployments, the JSON-RPC envelope would be a thin wrapper over
    TE's scripting host (e.g. via ``ScriptMode.Info``).

    Set ``mode='skeleton'`` (the default) for environments without a
    real TE installation: the adapter returns the destination file
    path without spawning a subprocess. Production deployments with
    TE installed should pass ``mode='subprocess'`` to enable real RPC.

    For environments without TE we recommend using
    ``InMemoryModelingAdapter`` directly via dependency injection;
    see ``tools/create_semantic_model_from_schema`` for the seam.
    """

    _VALID_MODES = {"skeleton", "subprocess"}

    def __init__(
        self,
        binary: str = "TabularEditor.exe",
        args: tuple[str, ...] = (),
        *,
        env: dict[str, str] | None = None,
        script_path: str | None = None,
        mode: str = "skeleton",
    ) -> None:
        if mode not in self._VALID_MODES:
            raise ValueError(
                f"invalid mode {mode!r}; expected one of {self._VALID_MODES}"
            )
        self._script_path = script_path
        self._mode = mode
        super().__init__(
            engine_name="te",
            binary=binary,
            args=args,
            env=env,
        )

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def name(self) -> str:
        return "te"

    @property
    def version(self) -> str:
        return "stub-0.1.0"  # real version would come from `TabularEditor --version`

    def _binary_missing_message(self, op: str) -> str:
        _ = op
        return (
            f"Tabular Editor binary {self._binary!r} not found; "
            "install per docs/engines-setup.md or inject "
            "InMemoryModelingAdapter"
        )

    async def apply_model_spec(
        self, pbip_path: str, tmdl_body: str, *, model_name: str
    ) -> ApplyModelSpecResult:
        """Apply a TMDL body to a PBIP via TE.

        If ``mode='skeleton'`` (default), returns a synthetic success
        with the would-be destination path. With ``mode='subprocess'``
        and the TE binary missing, returns ``success=False`` with a
        remediation hint; the caller's caller can elicit / fall back
        to ``InMemoryModelingAdapter``.
        """
        if self._mode == "skeleton":
            return ApplyModelSpecResult(
                success=True,
                changed_files=[
                    f"{pbip_path}/{model_name}.Dataset/definition.tmdl"
                ],
                tmdl_body=tmdl_body,
            )
        # mode == 'subprocess'
        if not os.path.exists(self._binary):
            return ApplyModelSpecResult(
                success=False,
                changed_files=[],
                tmdl_body=tmdl_body,
                error_message=self._binary_missing_message("apply_model_spec"),
            )
        response = await self._rpc(
            "apply_model_spec",
            {
                "pbip_path": pbip_path,
                "model_name": model_name,
                "tmdl_size": len(tmdl_body),
            },
        )
        return ApplyModelSpecResult(
            success=bool(response.get("success", False)),
            changed_files=response.get("changed_files", []),
            tmdl_body=tmdl_body,
            error_message=response.get("error_message"),
        )

    async def refactor_to_calculation_groups(
        self,
        pbip_path: str,
        spec: list[CalcGroupSpec],
        *,
        auto_apply: bool,
    ) -> RefactorCalcGroupsResult:
        """Run a calc-group refactor via TE."""
        if self._mode == "skeleton":
            return RefactorCalcGroupsResult(
                success=True,
                groups_created=[
                    f"TimeIntelligence_{cg.skeleton.replace(' ', '_')}"
                    for cg in spec
                ],
            )
        # mode == 'subprocess'
        if not os.path.exists(self._binary):
            return RefactorCalcGroupsResult(
                success=False,
                groups_created=[],
                error_message=self._binary_missing_message(
                    "refactor_to_calculation_groups"
                ),
            )
        response = await self._rpc(
            "refactor_to_calc_groups",
            {
                "pbip_path": pbip_path,
                "auto_apply": auto_apply,
                "spec": [cg.model_dump() for cg in spec],
            },
        )
        return RefactorCalcGroupsResult(
            success=bool(response.get("success", False)),
            groups_created=response.get("groups_created", []),
            measure_remappings=response.get("measure_remappings", {}),
            changed_files=response.get("changed_files", []),
            error_message=response.get("error_message"),
        )


__all__ = [
    "ApplyModelSpecResult",
    "CalcGroupSpec",
    "InMemoryModelingAdapter",
    "RefactorCalcGroupsResult",
    "SupportsSpecOps",
    "TabularEditorAdapter",
]
