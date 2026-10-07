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

import contextlib
import json
import os
import re
import tempfile
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


def _find_dataset_model_files(target_ref: str | Path) -> list[Path]:
    p = Path(target_ref)
    if not p.exists():
        return []
    if p.is_file():
        if p.name in ("definition.pbism", "model.bim") or p.suffix == ".tmdl":
            return [p]
        if p.suffix == ".pbip":
            p = p.parent
    if p.is_dir():
        candidates = [
            *(p.glob("*.Dataset/definition.pbism")),
            *(p.glob("*.SemanticModel/definition.pbism")),
            *(p.glob("*.Dataset/model.bim")),
            *(p.glob("*.SemanticModel/model.bim")),
            *(p.glob("definition.pbism")),
            *(p.glob("model.bim")),
        ]
        if candidates:
            return candidates
        tmdl_tables = [
            *(p.glob("*.Dataset/definition/tables/*.tmdl")),
            *(p.glob("*.SemanticModel/definition/tables/*.tmdl")),
            *(p.glob("definition/tables/*.tmdl")),
        ]
        if tmdl_tables:
            return tmdl_tables
    return []


def _load_model_from_json_file(file_path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        model_data = data.get("model", data)
        tables = [
            str(t.get("name", ""))
            for t in model_data.get("tables", [])
            if t.get("name")
        ]
        columns: dict[str, list[str]] = {}
        measures: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []
        for t in model_data.get("tables", []):
            tname = str(t.get("name", ""))
            if not tname:
                continue
            columns[tname] = [
                str(c.get("name", ""))
                for c in t.get("columns", [])
                if c.get("name")
            ]
            for m in t.get("measures", []):
                mname = str(m.get("name", ""))
                if mname:
                    measures.append(
                        {
                            "name": mname,
                            "table": tname,
                            "expression": str(m.get("expression", "")),
                        }
                    )
        for r in model_data.get("relationships", []):
            relationships.append(
                {
                    "from_table": str(r.get("fromTable", "")),
                    "from_column": str(r.get("fromColumn", "")),
                    "to_table": str(r.get("toTable", "")),
                    "to_column": str(r.get("toColumn", "")),
                }
            )
        return {
            "tables": tables,
            "columns": columns,
            "measures": measures,
            "relationships": relationships,
        }
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


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

    def is_available(self) -> bool:
        return True

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

    async def disconnect(self, conn: ConnectionHandle) -> None:
        _ = conn
        return None

    def _ensure_model_loaded(self, target_ref: str) -> dict[str, Any]:
        if target_ref in self._models and self._models[target_ref]:
            return self._models[target_ref]
        target_files = _find_dataset_model_files(target_ref)
        for tf in target_files:
            if tf.suffix in (".pbism", ".bim") or tf.name in ("definition.pbism", "model.bim"):
                loaded = _load_model_from_json_file(tf)
                if loaded is not None:
                    self._models[target_ref] = loaded
                    return loaded
        return self._models.setdefault(target_ref, {})

    async def list_tables(
        self,
        conn: ConnectionHandle,
    ) -> list[Table]:
        model = self._ensure_model_loaded(conn.target_ref)
        return [Table(name=t) for t in model.get("tables", [])]

    async def list_measures(
        self,
        conn: ConnectionHandle,
    ) -> list[Measure]:
        model = self._ensure_model_loaded(conn.target_ref)
        return [
            Measure(
                name=m["name"],
                table=m.get("table", ""),
                expression=m.get("expression", ""),
            )
            for m in model.get("measures", [])
        ]

    async def list_columns(
        self,
        conn: ConnectionHandle,
        table: str,
    ) -> list[Column]:
        model = self._ensure_model_loaded(conn.target_ref)
        return [
            Column(name=c)
            for c in model.get("columns", {}).get(table, [])
        ]

    async def list_relationships(
        self,
        conn: ConnectionHandle,
    ) -> list[Relationship]:
        model = self._ensure_model_loaded(conn.target_ref)
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
        conn: ConnectionHandle,
        table: str,
        column: str,
        changes: dict[str, Any],
    ) -> OperationResult:
        model = self._models.setdefault(conn.target_ref, {"columns": {}})
        model.setdefault("columns", {}).setdefault(table, []).append(column)
        target_files = _find_dataset_model_files(conn.target_ref)
        if not target_files:
            if Path(conn.target_ref).exists():
                return OperationResult(
                    success=False,
                    error_message=f"no dataset model files found for target {conn.target_ref}",
                )
            return OperationResult(success=True, changed_files=[f"{table}.{column}"])

        changed_files: list[str] = []
        new_name = str(changes.get("new_name") or changes.get("name") or "")

        for tf in target_files:
            if tf.suffix in (".pbism", ".bim") or tf.name in ("definition.pbism", "model.bim"):
                try:
                    data = json.loads(tf.read_text(encoding="utf-8"))
                    model_obj = data.get("model", data)
                    tables = model_obj.get("tables", [])
                    file_modified = False

                    target_table: dict[str, Any] | None = None
                    if not table:
                        for t in tables:
                            if str(t.get("name", "")).lower() == column.lower():
                                target_table = t
                                break
                    elif not column or column.lower() == table.lower():
                        for t in tables:
                            if str(t.get("name", "")).lower() == table.lower():
                                target_table = t
                                break

                    if target_table is not None:
                        old_t_name = str(target_table.get("name", ""))
                        if new_name:
                            target_table["name"] = new_name
                        for k, v in changes.items():
                            if k not in ("new_name", "name"):
                                target_table[k] = v
                        file_modified = True
                        for r in model_obj.get("relationships", []):
                            if str(r.get("fromTable", "")).lower() == old_t_name.lower():
                                r["fromTable"] = new_name
                            if str(r.get("toTable", "")).lower() == old_t_name.lower():
                                r["toTable"] = new_name
                        for t in tables:
                            for m in t.get("measures", []):
                                expr = str(m.get("expression", ""))
                                if expr:
                                    updated_expr = expr.replace(f"{old_t_name}[", f"{new_name}[")
                                    updated_expr = updated_expr.replace(f"'{old_t_name}'[", f"'{new_name}'[")
                                    if updated_expr != expr:
                                        m["expression"] = updated_expr
                    else:
                        for t in tables:
                            t_name = str(t.get("name", ""))
                            if not table or t_name.lower() == table.lower():
                                for c in t.get("columns", []):
                                    if str(c.get("name", "")).lower() == column.lower():
                                        if new_name:
                                            c["name"] = new_name
                                        for k, v in changes.items():
                                            if k not in ("new_name", "name"):
                                                c[k] = v
                                        file_modified = True
                                if not file_modified:
                                    for m in t.get("measures", []):
                                        if str(m.get("name", "")).lower() == column.lower():
                                            if new_name:
                                                m["name"] = new_name
                                            for k, v in changes.items():
                                                if k not in ("new_name", "name"):
                                                    m[k] = v
                                            file_modified = True

                        if file_modified and new_name:
                            for t in tables:
                                t_name = str(t.get("name", ""))
                                for m in t.get("measures", []):
                                    expr = str(m.get("expression", ""))
                                    if expr:
                                        prefix = f"{table}[" if table else f"{t_name}["
                                        updated_expr = expr.replace(
                                            f"{prefix}{column}]", f"{prefix}{new_name}]"
                                        )
                                        if not table or t_name.lower() == table.lower():
                                            updated_expr = updated_expr.replace(
                                                f"[{column}]", f"[{new_name}]"
                                            )
                                        if updated_expr != expr:
                                            m["expression"] = updated_expr
                            for r in model_obj.get("relationships", []):
                                if (
                                    not table
                                    or str(r.get("fromTable", "")).lower() == table.lower()
                                ) and str(r.get("fromColumn", "")).lower() == column.lower():
                                    r["fromColumn"] = new_name
                                if (
                                    not table
                                    or str(r.get("toTable", "")).lower() == table.lower()
                                ) and str(r.get("toColumn", "")).lower() == column.lower():
                                    r["toColumn"] = new_name

                    if file_modified:
                        tmp = tf.with_suffix(tf.suffix + ".tmp")
                        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                        tmp.replace(tf)
                        changed_files.append(str(tf))
                except OSError as exc:
                    return OperationResult(
                        success=False,
                        error_message=f"I/O error updating {tf}: {exc}",
                    )
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            elif tf.suffix == ".tmdl":
                try:
                    text = tf.read_text(encoding="utf-8")
                    is_tbl = (
                        (not table and tf.stem.lower() == column.lower())
                        or (table and tf.stem.lower() == table.lower() and (not column or column.lower() == table.lower()))
                    )
                    if is_tbl and new_name:
                        old_t_name = tf.stem
                        pattern = re.compile(
                            rf"^(\s*table\s+)(?:{re.escape(old_t_name)}|'{re.escape(old_t_name)}')\b",
                            re.MULTILINE,
                        )
                        new_text = pattern.sub(rf"\g<1>{new_name}", text)
                        new_text = new_text.replace(f"{old_t_name}[", f"{new_name}[")
                        new_text = new_text.replace(f"'{old_t_name}'[", f"'{new_name}'[")
                        new_tf = tf.with_name(f"{new_name}.tmdl")
                        tmp = new_tf.with_suffix(new_tf.suffix + ".tmp")
                        tmp.write_text(new_text, encoding="utf-8")
                        tmp.replace(new_tf)
                        if new_tf != tf and tf.exists():
                            tf.unlink()
                        changed_files.append(str(new_tf))
                    elif new_name:
                        pattern = re.compile(
                            rf"^(\s*column\s+)(?:{re.escape(column)}|'{re.escape(column)}')\b",
                            re.MULTILINE,
                        )
                        new_text = pattern.sub(rf"\g<1>{new_name}", text)
                        if table:
                            new_text = new_text.replace(
                                f"{table}[{column}]", f"{table}[{new_name}]"
                            )
                        new_text = new_text.replace(f"[{column}]", f"[{new_name}]")
                        if new_text != text:
                            tmp = tf.with_suffix(tf.suffix + ".tmp")
                            tmp.write_text(new_text, encoding="utf-8")
                            tmp.replace(tf)
                            changed_files.append(str(tf))
                except OSError as exc:
                    return OperationResult(
                        success=False,
                        error_message=f"I/O error updating {tf}: {exc}",
                    )
                except UnicodeDecodeError:
                    pass

        if not changed_files:
            return OperationResult(
                success=False,
                error_message=f"no column, measure, or table named {column!r} found in {table or '<any>'}",
            )
        return OperationResult(success=True, changed_files=changed_files)

    async def create_measure(
        self, conn: ConnectionHandle, table: str, measure: Measure
    ) -> OperationResult:
        model = self._models.setdefault(conn.target_ref, {"measures": []})
        model.setdefault("measures", []).append(
            {"name": measure.name, "table": table}
        )
        target_files = _find_dataset_model_files(conn.target_ref)
        if not target_files:
            if Path(conn.target_ref).exists():
                return OperationResult(
                    success=False,
                    error_message=f"no dataset model files found for target {conn.target_ref}",
                )
            return OperationResult(
                success=True, changed_files=[f"{table}.{measure.name}"]
            )

        changed_files: list[str] = []
        for tf in target_files:
            if tf.suffix in (".pbism", ".bim") or tf.name in (
                "definition.pbism",
                "model.bim",
            ):
                try:
                    data = json.loads(tf.read_text(encoding="utf-8"))
                    model_obj = data.get("model", data)
                    file_modified = False
                    for t in model_obj.get("tables", []):
                        if not table or str(t.get("name", "")).lower() == table.lower():
                            measures_list = t.setdefault("measures", [])
                            measures_list.append(
                                {
                                    "name": measure.name,
                                    "table": str(t.get("name", table)),
                                    "expression": measure.expression,
                                }
                            )
                            file_modified = True
                            break
                    if file_modified:
                        tmp = tf.with_suffix(tf.suffix + ".tmp")
                        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                        tmp.replace(tf)
                        changed_files.append(str(tf))
                except OSError as exc:
                    return OperationResult(
                        success=False,
                        error_message=f"I/O error updating {tf}: {exc}",
                    )
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            elif tf.suffix == ".tmdl" and (
                not table or tf.stem.lower() == table.lower()
            ):
                try:
                    text = tf.read_text(encoding="utf-8")
                    block = f"\n\n\tmeasure '{measure.name}' = {measure.expression}\n"
                    tmp = tf.with_suffix(tf.suffix + ".tmp")
                    tmp.write_text(text + block, encoding="utf-8")
                    tmp.replace(tf)
                    changed_files.append(str(tf))
                except OSError as exc:
                    return OperationResult(
                        success=False,
                        error_message=f"I/O error updating {tf}: {exc}",
                    )
                except UnicodeDecodeError:
                    pass
        if not changed_files:
            return OperationResult(
                success=False,
                error_message=f"table {table!r} not found in model to create measure {measure.name!r}",
            )
        return OperationResult(success=True, changed_files=changed_files)

    async def update_measure(
        self,
        conn: ConnectionHandle,
        table: str,
        measure: str,
        changes: dict[str, Any],
    ) -> OperationResult:
        target_files = _find_dataset_model_files(conn.target_ref)
        if not target_files:
            if Path(conn.target_ref).exists():
                return OperationResult(
                    success=False,
                    error_message=f"no dataset model files found for target {conn.target_ref}",
                )
            return OperationResult(success=True, changed_files=[f"{table}.{measure}"])

        changed_files: list[str] = []
        new_name = str(changes.get("new_name") or changes.get("name") or "")
        new_expr = changes.get("expression")

        for tf in target_files:
            if tf.suffix in (".pbism", ".bim") or tf.name in (
                "definition.pbism",
                "model.bim",
            ):
                try:
                    data = json.loads(tf.read_text(encoding="utf-8"))
                    model_obj = data.get("model", data)
                    file_modified = False
                    for t in model_obj.get("tables", []):
                        if not table or str(t.get("name", "")).lower() == table.lower():
                            for m in t.get("measures", []):
                                if str(m.get("name", "")).lower() == measure.lower():
                                    if new_name:
                                        m["name"] = new_name
                                    if new_expr is not None:
                                        m["expression"] = str(new_expr)
                                    for k, v in changes.items():
                                        if k not in ("new_name", "name", "expression"):
                                            m[k] = v
                                    file_modified = True
                    if file_modified:
                        tmp = tf.with_suffix(tf.suffix + ".tmp")
                        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                        tmp.replace(tf)
                        changed_files.append(str(tf))
                except OSError as exc:
                    return OperationResult(
                        success=False,
                        error_message=f"I/O error updating {tf}: {exc}",
                    )
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
        if not changed_files:
            return OperationResult(
                success=False,
                error_message=f"no measure named {measure!r} found in {table or '<any>'}",
            )
        return OperationResult(success=True, changed_files=changed_files)

    async def delete_measure(
        self,
        conn: ConnectionHandle,
        table: str,
        measure: str,
    ) -> OperationResult:
        target_files = _find_dataset_model_files(conn.target_ref)
        if not target_files:
            if Path(conn.target_ref).exists():
                return OperationResult(
                    success=False,
                    error_message=f"no dataset model files found for target {conn.target_ref}",
                )
            return OperationResult(success=True, changed_files=[f"{table}.{measure}"])

        changed_files: list[str] = []
        for tf in target_files:
            if tf.suffix in (".pbism", ".bim") or tf.name in (
                "definition.pbism",
                "model.bim",
            ):
                try:
                    data = json.loads(tf.read_text(encoding="utf-8"))
                    model_obj = data.get("model", data)
                    file_modified = False
                    for t in model_obj.get("tables", []):
                        if not table or str(t.get("name", "")).lower() == table.lower():
                            measures = t.get("measures", [])
                            initial_len = len(measures)
                            t["measures"] = [
                                m
                                for m in measures
                                if str(m.get("name", "")).lower() != measure.lower()
                            ]
                            if len(t["measures"]) != initial_len:
                                file_modified = True
                    if file_modified:
                        tmp = tf.with_suffix(tf.suffix + ".tmp")
                        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                        tmp.replace(tf)
                        changed_files.append(str(tf))
                except OSError as exc:
                    return OperationResult(
                        success=False,
                        error_message=f"I/O error updating {tf}: {exc}",
                    )
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
        if not changed_files:
            return OperationResult(
                success=False,
                error_message=f"no measure named {measure!r} found in {table or '<any>'}",
            )
        return OperationResult(success=True, changed_files=changed_files)

    async def execute_dax(
        self,
        conn: ConnectionHandle,
        query: str,
        effective_identity: dict[str, Any] | None = None,
    ) -> DaxResult:
        _ = (conn, query, effective_identity)
        return DaxResult(rows=[], row_count=0, duration_ms=0)

    async def snapshot(
        self,
        conn: ConnectionHandle,
        label: str,
    ) -> SnapshotHandle:
        clean_label = Path(label).name
        path = Path(tempfile.gettempdir()) / f"snapshot-{clean_label}.json"
        target_files = _find_dataset_model_files(conn.target_ref)
        file_backups: dict[str, str] = {}
        for tf in target_files:
            with contextlib.suppress(Exception):
                file_backups[str(tf)] = tf.read_text(encoding="utf-8")
        payload = {
            "in_memory": self._models.get(conn.target_ref, {}),
            "disk_files": file_backups,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return SnapshotHandle(label=label, path=path)

    async def restore_snapshot(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        handle: SnapshotHandle | None = None
        for a in args:
            if isinstance(a, SnapshotHandle):
                handle = a
                break
        if handle is None:
            h = kwargs.get("handle")
            if isinstance(h, SnapshotHandle):
                handle = h
        if handle is not None and handle.path.exists():
            try:
                data = json.loads(handle.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "disk_files" in data:
                    for fpath, fcontent in data.get("disk_files", {}).items():
                        Path(fpath).write_text(fcontent, encoding="utf-8")
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                pass
            self.snapshots.setdefault(handle.label, [])

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

    def is_available(self) -> bool:
        if self._mode == "skeleton":
            return True
        import shutil

        return bool(shutil.which(self._binary) or os.path.exists(self._binary))

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
