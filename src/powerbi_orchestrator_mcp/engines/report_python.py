"""Cross-platform PBIR editor using direct JSON file manipulation.

Implements the Python fallback from ``specs/05-engines-adapters.md`` §2.3:

> Script propio (cross-platform) — Regex/JSON patches sobre PBIR cuando no
> hay skill ni Desktop.

This adapter is the default for ``ReportEngine`` on Linux/macOS because
it has no external binary dependency. Week 2 adds ``superbi-mcp`` (Win)
and ``skills-for-fabric/powerbi-report-authoring`` (cross-platform via
LLM) as upgrades that slot into the same selector chain.

Operations supported (per ``specs/05-engines-adapters.md`` §2.3):
- ``add_page`` — write a new ``pages/<name>/page.json`` with defaults.
- ``add_visual`` — append a visual to an existing page.
- ``update_visual`` — mutate fields on an existing visual.
- ``propagate_rename`` — walk all PBIR JSON and rewrite references.
- ``validate_pbir`` — structural sanity check (call out to ``pbip-validator``
  if available; otherwise check JSON parses + visualContainer exists).

This adapter operates ENTIRELY on local files (no subprocess), so the
``StepExecutor`` integration is straightforward — we register it in the
default registry at module load.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from powerbi_orchestrator_mcp.engines.base import (
    ConnectionHandle,
    JsonRpcSubprocessEngine,  # noqa: F401  (used in _register_default)
    OperationResult,
    PageLayout,
    ReportEngine,  # noqa: F401
    ValidationResult,
    VisualSpec,
)
from powerbi_orchestrator_mcp.engines.errors import (
    EngineError,
    EngineValidationError,
)
from powerbi_orchestrator_mcp.orchestrator.context import EngineStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON file, raising EngineValidationError if invalid."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except FileNotFoundError as exc:
        raise EngineValidationError(
            f"PBIR file not found: {path}",
            engine="python_report",
            code="engine_validation_failed",
            remediation_hint=f"Check that {path} exists in the PBIP folder",
        ) from exc
    except json.JSONDecodeError as exc:
        raise EngineValidationError(
            f"PBIR file is not valid JSON: {path}",
            engine="python_report",
            code="engine_validation_failed",
            remediation_hint=(
                f"Fix the JSON syntax in {path} or restore from a backup"
            ),
        ) from exc


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Write a JSON file atomically (write-then-rename)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def _resolve_pbip_root(conn_or_path: ConnectionHandle | Path) -> Path:
    """Get the PBIP root directory from a connection handle or path."""
    if isinstance(conn_or_path, Path):
        return conn_or_path
    # ConnectionHandle.target_ref is the path to the .pbip folder.
    return Path(conn_or_path.target_ref)


def _resolve_report_dir(pbip_root: Path) -> Path:
    """Find the report directory (could be `<name>.Report/`)."""
    candidates = list(pbip_root.glob("*.Report"))
    if not candidates:
        raise EngineValidationError(
            f"no .Report directory found in {pbip_root}",
            engine="python_report",
            code="engine_validation_failed",
            remediation_hint=(
                f"Ensure {pbip_root} is a valid PBIP folder with a "
                f".Report/ subfolder"
            ),
        )
    if len(candidates) > 1:
        # Ambiguous; use the first but warn.
        # (In practice, a PBIP has exactly one .Report/ dir.)
        pass
    return candidates[0]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class PythonReportEngine:
    """Cross-platform ``ReportEngine`` implementation using direct file I/O.

    Operates on a PBIP folder structure:
    ::

        <pbip_root>/
            sample.pbip                # metadata
            sample.Dataset/            # model (TMDL)
            sample.Report/             # <-- this is what we edit
                definition.pbir
                report.json
                pages/<page_name>/page.json

    No subprocess. All operations are sync I/O wrapped in async methods
    (the orchestrator expects async).
    """

    @property
    def name(self) -> str:
        return "python_report"

    async def health_check(self) -> EngineStatus:
        # No external dependency → always available.
        return EngineStatus(
            name=self.name,
            available=True,
            version="0.1.0",
            reason_unavailable=None,
        )

    async def connect(self, pbip_path: Path) -> ConnectionHandle:
        """Open a logical connection by validating the PBIP layout."""
        pbip_root = _resolve_pbip_root(pbip_path)
        report_dir = _resolve_report_dir(pbip_root)
        # Validate the .pbip metadata file exists.
        metadata_files = list(pbip_root.glob("*.pbip"))
        if not metadata_files:
            raise EngineValidationError(
                f"no .pbip metadata file in {pbip_root}",
                engine=self.name,
                code="engine_validation_failed",
                remediation_hint=(
                    f"Ensure {pbip_root} contains a .pbip metadata file"
                ),
            )
        return ConnectionHandle(
            engine=self.name,
            target_type="pbip_folder",
            target_ref=str(pbip_root),
            session_token=str(report_dir),
        )

    async def disconnect(self, conn: ConnectionHandle) -> None:  # noqa: ARG002
        # No-op: no subprocess to kill.
        return None

    # ------------------------------------------------------------------
    # Page + visual CRUD
    # ------------------------------------------------------------------

    async def add_page(
        self,
        conn: ConnectionHandle,
        page_name: str,
        layout: PageLayout | None = None,
    ) -> OperationResult:
        layout = layout or PageLayout()
        pbip_root = _resolve_pbip_root(conn)
        report_dir = _resolve_report_dir(pbip_root)

        page_dir = report_dir / "pages" / page_name
        page_json = page_dir / "page.json"

        if page_json.exists():
            raise EngineValidationError(
                f"page {page_name!r} already exists",
                engine=self.name,
                code="engine_validation_failed",
                remediation_hint="Pick a different page name",
            )

        page_data: dict[str, Any] = {
            "$type": "page",
            "name": page_name,
            "displayName": page_name,
            "displayOption": "FitToPage",
            "width": layout.width,
            "height": layout.height,
            "visualContainers": [],
            "mobileLayout": {
                "visible": True,
                "stack": True,
                "orientation": "vertical",
            }
            if layout.mobile_first
            else None,
        }
        # Strip None values for cleaner JSON.
        page_data = {k: v for k, v in page_data.items() if v is not None}

        page_dir.mkdir(parents=True, exist_ok=True)
        _write_json(page_json, page_data)

        return OperationResult(
            success=True,
            changed_files=[str(page_json.relative_to(pbip_root))],
        )

    async def add_visual(
        self,
        conn: ConnectionHandle,
        page: str,
        visual_spec: VisualSpec,
    ) -> OperationResult:
        pbip_root = _resolve_pbip_root(conn)
        report_dir = _resolve_report_dir(pbip_root)
        page_json_path = report_dir / "pages" / page / "page.json"
        page_data = _read_json(page_json_path)

        visual_id = visual_spec.visual_id or f"v_{int(time.time() * 1000)}"
        visual_container: dict[str, Any] = {
            "$type": "visualContainer",
            "id": visual_id,
            "x": visual_spec.position.get("x", 0),
            "y": visual_spec.position.get("y", 0),
            "width": visual_spec.position.get("width", 400),
            "height": visual_spec.position.get("height", 300),
            "visual": {
                "$type": visual_spec.type,
                "id": visual_id,
                **visual_spec.fields,
                "objects": visual_spec.format,
            },
            "tabOrder": 0,
        }
        page_data.setdefault("visualContainers", []).append(visual_container)
        _write_json(page_json_path, page_data)

        return OperationResult(
            success=True,
            changed_files=[str(page_json_path.relative_to(pbip_root))],
        )

    async def update_visual(
        self,
        conn: ConnectionHandle,
        page: str,
        visual_id: str,
        changes: dict[str, Any],
    ) -> OperationResult:
        pbip_root = _resolve_pbip_root(conn)
        report_dir = _resolve_report_dir(pbip_root)
        page_json_path = report_dir / "pages" / page / "page.json"
        page_data = _read_json(page_json_path)

        containers = page_data.get("visualContainers", [])
        for vc in containers:
            if vc.get("id") == visual_id:
                vc.update(changes)
                _write_json(page_json_path, page_data)
                return OperationResult(
                    success=True,
                    changed_files=[
                        str(page_json_path.relative_to(pbip_root))
                    ],
                )

        raise EngineValidationError(
            f"visual {visual_id!r} not found on page {page!r}",
            engine=self.name,
            code="engine_validation_failed",
            remediation_hint="List page visuals first; visual_id may have changed",
        )

    # ------------------------------------------------------------------
    # Propagate rename — the core safe_rename operation
    # ------------------------------------------------------------------

    async def propagate_rename(
        self,
        conn: ConnectionHandle,
        old_path: str,
        new_path: str,
        scope: str,  # "report_bindings" | "model_only" | "full"
    ) -> OperationResult:
        """Walk all PBIR JSON files and rewrite references to ``old_path``.

        Per spec: replaces old_path with new_path in:
        - Column references in visualContainer data.
        - Measure references in visualContainer data.
        - queryRef fields.
        - Embedded DAX string expressions (best-effort substring match).

        Returns the list of changed files.
        """
        if scope not in {"report_bindings", "model_only", "full"}:
            raise EngineValidationError(
                f"unknown scope {scope!r}",
                engine=self.name,
                code="engine_validation_failed",
                remediation_hint=(
                    "Valid scopes: 'report_bindings', 'model_only', 'full'"
                ),
            )

        pbip_root = _resolve_pbip_root(conn)
        report_dir = _resolve_report_dir(pbip_root)
        changed: list[str] = []

        # Walk all JSON files under pages/.
        for json_path in (report_dir / "pages").rglob("*.json"):
            try:
                data = _read_json(json_path)
            except EngineError:
                continue  # skip malformed files; they're not our concern here

            new_data = _replace_in_obj(data, old_path, new_path)
            if new_data != data:
                _write_json(json_path, new_data)
                changed.append(str(json_path.relative_to(pbip_root)))

        # Also rewrite the top-level report.json / definition.pbir.
        for top_level in ("report.json", "definition.pbir"):
            top_path = report_dir / top_level
            if top_path.exists():
                try:
                    data = _read_json(top_path)
                except EngineError:
                    continue
                new_data = _replace_in_obj(data, old_path, new_path)
                if new_data != data:
                    _write_json(top_path, new_data)
                    changed.append(str(top_path.relative_to(pbip_root)))

        return OperationResult(success=True, changed_files=changed)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    async def validate_pbir(self, conn: ConnectionHandle) -> ValidationResult:
        """Structural PBIR validation (no pbip-validator dependency)."""
        pbip_root = _resolve_pbip_root(conn)
        report_dir = _resolve_report_dir(pbip_root)
        findings: list[dict[str, Any]] = []

        # Check report.json parses.
        report_json = report_dir / "report.json"
        if not report_json.exists():
            findings.append(
                {
                    "severity": "error",
                    "rule_id": "report_json_missing",
                    "message": f"missing {report_json}",
                }
            )
        else:
            try:
                _read_json(report_json)
            except EngineError as exc:
                findings.append(
                    {
                        "severity": "error",
                        "rule_id": "report_json_invalid",
                        "message": str(exc),
                    }
                )

        # Check pages/*/page.json files parse + have visualContainers key.
        page_files = list((report_dir / "pages").glob("*/page.json"))
        if not page_files:
            findings.append(
                {
                    "severity": "warning",
                    "rule_id": "no_pages",
                    "message": "report has no pages",
                }
            )
        for pf in page_files:
            try:
                page_data = _read_json(pf)
            except EngineError as exc:
                findings.append(
                    {
                        "severity": "error",
                        "rule_id": "page_json_invalid",
                        "message": str(exc),
                    }
                )
                continue
            if "visualContainers" not in page_data:
                findings.append(
                    {
                        "severity": "warning",
                        "rule_id": "page_missing_visual_containers",
                        "message": f"{pf} missing visualContainers key",
                    }
                )

        return ValidationResult(
            valid=not any(f["severity"] == "error" for f in findings),
            findings=findings,
        )


# ---------------------------------------------------------------------------
# String substitution (used by propagate_rename)
# ---------------------------------------------------------------------------


def _replace_in_obj(
    obj: Any, old: str, new: str
) -> Any:
    """Recursively replace ``old`` with ``new`` in all string leaves.

    Handles the path formats used by Power BI:
    - ``Table[Column]`` (column reference)
    - ``Table[Measure]`` (measure reference)
    - ``Table.Column`` (legacy TOM-style, less common in PBIR)

    Best-effort: also replaces inside DAX expression strings that contain
    ``old`` as a substring (e.g. ``[Total Sales]`` vs ``[TotalKey]`` would
    wrongly match — caller is responsible for unambiguous names).
    """
    if isinstance(obj, str):
        if old in obj:
            return obj.replace(old, new)
        return obj
    if isinstance(obj, list):
        return [_replace_in_obj(item, old, new) for item in obj]
    if isinstance(obj, dict):
        return {k: _replace_in_obj(v, old, new) for k, v in obj.items()}
    return obj


__all__ = ["PythonReportEngine"]


# ---------------------------------------------------------------------------
# One-time self-registration with the default StepExecutor registry
# ---------------------------------------------------------------------------


def _register_default() -> None:
    """Register the Python report engine with the orchestrator's registry.

    Called on module import so ``apply_plan`` can dispatch `report` steps
    without any external configuration.
    """
    from powerbi_orchestrator_mcp.orchestrator.step_executor import (
        StepExecutor,
        get_default_registry,
    )

    class _PythonReportExecutor(StepExecutor):
        """StepExecutor adapter that delegates to PythonReportEngine.

        Implements the StepExecutor Protocol (engine_name + async
        execute_step). Bridges the apply_plan world (which knows about
        engine names like 'modeling'/'report') to the ReportEngine
        world (which knows about pbip paths).
        """

        engine_name = "report"

        def __init__(self) -> None:
            self._engine = PythonReportEngine()

        async def execute_step(
            self,
            step: PlanStep,  # type: ignore[name-defined]  # noqa: F821
        ) -> StepOutcome:  # type: ignore[name-defined]  # noqa: F821
            from powerbi_orchestrator_mcp.orchestrator.rollback import (
                StepOutcome,
            )

            try:
                action = step.action
                args = step.args
                # Map plan actions to ReportEngine methods.
                # apply_plan stores the pbip_path in args["pbip_path"] or
                # in the active session's target_ref. For MVP we read from
                # args["pbip_path"] (the planner sets it for deploy plans;
                # for safe_rename we get it from connect_target).
                pbip_path = Path(
                    args.get("pbip_path")
                    or args.get("target_ref")
                    or "."
                )
                conn = await self._engine.connect(pbip_path)
                try:
                    if action == "add_page":
                        from powerbi_orchestrator_mcp.engines.base import (
                            PageLayout,
                        )

                        layout = (
                            PageLayout(**args.get("layout", {}))
                            if "layout" in args
                            else None
                        )
                        result = await self._engine.add_page(
                            conn, args["page_name"], layout
                        )
                    elif action == "add_visual":
                        from powerbi_orchestrator_mcp.engines.base import (
                            VisualSpec,
                        )

                        spec = VisualSpec(**args.get("spec", args))
                        result = await self._engine.add_visual(
                            conn, args["page"], spec
                        )
                    elif action == "update_visual":
                        result = await self._engine.update_visual(
                            conn,
                            args["page"],
                            args["visual_id"],
                            args.get("changes", {}),
                        )
                    elif action == "propagate_rename":
                        result = await self._engine.propagate_rename(
                            conn,
                            args["old_path"],
                            args["new_path"],
                            args.get("scope", "report_bindings"),
                        )
                    elif action == "validate_pbir":
                        v = await self._engine.validate_pbir(conn)
                        result = OperationResult(
                            success=v.valid,
                            changed_files=[],
                        )
                    else:
                        # Generic pass-through for other ReportEngine ops.
                        result = OperationResult(
                            success=False,
                            changed_files=[],
                        )
                finally:
                    await self._engine.disconnect(conn)
                return StepOutcome(
                    success=result.success,
                    error_message=None
                    if result.success
                    else "report engine returned failure",
                    changed_files=result.changed_files,
                )
            except EngineError as exc:
                from powerbi_orchestrator_mcp.orchestrator.rollback import (
                    StepOutcome,
                )

                return StepOutcome(
                    success=False,
                    error_message=exc.message,
                    changed_files=[],
                )

    get_default_registry().register(_PythonReportExecutor())


_register_default()
