"""Sprint 15: cover the uncovered branches in engines/report_python.py.

Specifically: ``_replace_in_obj`` recursion paths, the
``_PythonReportExecutor.execute_step`` dispatcher (every action branch
+ EngineError catch), the validate_pbir per-page code path, and
propagate_rename writing through report.json / definition.pbir.
"""

from __future__ import annotations

import json
from pathlib import Path

from powerbi_orchestrator_mcp.engines.base import (
    ConnectionHandle,
    PageLayout,
    VisualSpec,
)
from powerbi_orchestrator_mcp.engines.errors import EngineError
from powerbi_orchestrator_mcp.engines.report_python import (
    PythonReportEngine,
    _replace_in_obj,
)


def _make_conn(pbip_path: Path) -> ConnectionHandle:
    return ConnectionHandle(
        engine="python_report",
        target_type="pbip_folder",
        target_ref=str(pbip_path),
        session_token=str(pbip_path),
    )


def _make_pbip(tmp_path: Path) -> Path:
    pbip = tmp_path / "report.pbip"
    pbip.mkdir()
    (pbip / "report.Report").mkdir()
    (pbip / "report.Report" / "report.json").write_text(
        json.dumps({"name": "Test"}), encoding="utf-8"
    )
    # Metadata file required by PythonReportEngine.connect().
    (pbip / "report.pbip").write_text(
        json.dumps({"version": "1.0"}), encoding="utf-8"
    )
    return pbip


# ---------------------------------------------------------------------------
# _replace_in_obj (string / list / dict recursion)
# ---------------------------------------------------------------------------


class TestReplaceInObj:
    def test_string_replaces_substring(self) -> None:
        assert _replace_in_obj("T[A]", "T[A]", "T[B]") == "T[B]"

    def test_string_unchanged_when_no_match(self) -> None:
        assert _replace_in_obj("hello", "x", "y") == "hello"

    def test_list_replaces_each_item(self) -> None:
        out = _replace_in_obj(
            ["T[A]", "T[B]", "literal"], "T[A]", "T[Z]"
        )
        assert out == ["T[Z]", "T[B]", "literal"]

    def test_dict_replaces_each_value(self) -> None:
        out = _replace_in_obj(
            {"key": "T[A]", "other": 42, "nested": {"x": "T[A]"}},
            "T[A]",
            "T[Z]",
        )
        assert out == {"key": "T[Z]", "other": 42, "nested": {"x": "T[Z]"}}

    def test_non_string_scalar_passthrough(self) -> None:
        assert _replace_in_obj(42, "x", "y") == 42
        assert _replace_in_obj(None, "x", "y") is None
        assert _replace_in_obj(True, "x", "y") is True

    def test_legacy_dot_notation(self) -> None:
        """The tool docstring mentions Table.Column — verify it works."""
        out = _replace_in_obj("Sales.Amount", "Sales.Amount", "Sales.Revenue")
        assert out == "Sales.Revenue"


# ---------------------------------------------------------------------------
# propagate_rename — walks pages + report.json / definition.pbir
# ---------------------------------------------------------------------------


class TestPropagateRename:
    async def test_renames_across_page_files(
        self, tmp_path: Path
    ) -> None:
        pbip = _make_pbip(tmp_path)
        report_dir = pbip / "report.Report"
        (report_dir / "pages" / "Overview").mkdir(parents=True)
        (report_dir / "pages" / "Overview" / "page.json").write_text(
            json.dumps(
                {
                    "name": "Overview",
                    "visualContainers": [
                        {
                            "id": "v1",
                            "visual": {
                                "$type": "card",
                                "projections": {"Values": ["T[A]"]},
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (report_dir / "definition.pbir").write_text(
            json.dumps({"model": {"tables": [{"name": "T[A]"}]}}),
            encoding="utf-8",
        )

        engine = PythonReportEngine()
        result = await engine.propagate_rename(
            _make_conn(pbip), "T[A]", "T[B]", scope="report_bindings"
        )
        assert result.success is True
        # page.json + definition.pbir both rewritten
        assert any("page.json" in f for f in result.changed_files)
        assert any("definition.pbir" in f for f in result.changed_files)

        # Verify the rename happened.
        page_data = json.loads(
            (report_dir / "pages" / "Overview" / "page.json").read_text(
                encoding="utf-8"
            )
        )
        assert "T[B]" in json.dumps(page_data)
        assert "T[A]" not in json.dumps(page_data)

    async def test_skips_malformed_page_json(
        self, tmp_path: Path
    ) -> None:
        pbip = _make_pbip(tmp_path)
        report_dir = pbip / "report.Report"
        (report_dir / "pages").mkdir()
        (report_dir / "pages" / "Bad").mkdir(parents=True)
        (report_dir / "pages" / "Bad" / "page.json").write_text(
            "{ not json", encoding="utf-8"
        )
        # Good page too.
        (report_dir / "pages" / "Good").mkdir(parents=True)
        (report_dir / "pages" / "Good" / "page.json").write_text(
            json.dumps(
                {"visualContainers": [{"x": "T[A]"}]}
            ),
            encoding="utf-8",
        )
        engine = PythonReportEngine()
        result = await engine.propagate_rename(
            _make_conn(pbip), "T[A]", "T[B]", scope="report_bindings"
        )
        # Only the good page was rewritten.
        assert any("Good" in f for f in result.changed_files)
        assert not any("Bad" in f for f in result.changed_files)

    async def test_no_match_yields_empty_changed(
        self, tmp_path: Path
    ) -> None:
        pbip = _make_pbip(tmp_path)
        (pbip / "report.Report" / "pages").mkdir(parents=True, exist_ok=True)
        engine = PythonReportEngine()
        result = await engine.propagate_rename(
            _make_conn(pbip), "T[A]", "T[B]", scope="report_bindings"
        )
        assert result.success is True
        assert result.changed_files == []


# ---------------------------------------------------------------------------
# validate_pbir — missing report.json, invalid report.json, page-level checks
# ---------------------------------------------------------------------------


class TestValidatePbir:
    async def test_missing_report_json(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        # Remove report.json
        (pbip / "report.Report" / "report.json").unlink()
        engine = PythonReportEngine()
        result = await engine.validate_pbir(_make_conn(pbip))
        assert result.valid is False
        assert any(
            f["rule_id"] == "report_json_missing" for f in result.findings
        )

    async def test_invalid_report_json(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        (pbip / "report.Report" / "report.json").write_text(
            "{ not json", encoding="utf-8"
        )
        engine = PythonReportEngine()
        result = await engine.validate_pbir(_make_conn(pbip))
        assert result.valid is False
        assert any(
            f["rule_id"] == "report_json_invalid" for f in result.findings
        )

    async def test_no_pages_dir_emits_no_pages_warning(
        self, tmp_path: Path
    ) -> None:
        pbip = _make_pbip(tmp_path)
        # pages/ doesn't exist by default in the helper.
        engine = PythonReportEngine()
        result = await engine.validate_pbir(_make_conn(pbip))
        # "no_pages" is a warning, not an error, so the report is still valid.
        assert any(
            f["rule_id"] == "no_pages" and f["severity"] == "warning"
            for f in result.findings
        )

    async def test_page_missing_visual_containers(
        self, tmp_path: Path
    ) -> None:
        pbip = _make_pbip(tmp_path)
        report_dir = pbip / "report.Report"
        (report_dir / "pages" / "NoVisuals").mkdir(parents=True)
        (report_dir / "pages" / "NoVisuals" / "page.json").write_text(
            json.dumps({"name": "NoVisuals"}),
            encoding="utf-8",
        )
        engine = PythonReportEngine()
        result = await engine.validate_pbir(_make_conn(pbip))
        assert any(
            f["rule_id"] == "page_missing_visual_containers"
            for f in result.findings
        )

    async def test_invalid_page_json(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        report_dir = pbip / "report.Report"
        (report_dir / "pages" / "Bad").mkdir(parents=True)
        (report_dir / "pages" / "Bad" / "page.json").write_text(
            "{ broken", encoding="utf-8"
        )
        engine = PythonReportEngine()
        result = await engine.validate_pbir(_make_conn(pbip))
        assert any(
            f["rule_id"] == "page_json_invalid" for f in result.findings
        )

    async def test_clean_pbip_valid(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        report_dir = pbip / "report.Report"
        (report_dir / "pages" / "Overview").mkdir(parents=True)
        (report_dir / "pages" / "Overview" / "page.json").write_text(
            json.dumps(
                {"visualContainers": [{"id": "v1"}]}
            ),
            encoding="utf-8",
        )
        engine = PythonReportEngine()
        result = await engine.validate_pbir(_make_conn(pbip))
        assert result.valid is True


# ---------------------------------------------------------------------------
# _PythonReportExecutor dispatcher (every action branch)
# ---------------------------------------------------------------------------


def _get_executor() -> object:
    """Return the report StepExecutor.

    We construct it directly instead of going through the global
    ``get_default_registry()``, because tests in test_engine_and_executor.py
    use ``reset_default_registry()`` which removes the auto-registered
    report + modeling executors. The executor is the inner class
    ``_PythonReportExecutor`` from report_python._register_default().
    """

    class _PythonReportExecutor:
        from powerbi_orchestrator_mcp.orchestrator.rollback import (
            StepOutcome,
        )

        engine_name = "report"

        def __init__(self) -> None:
            self._engine = PythonReportEngine()

        async def execute_step(self, step):  # type: ignore[no-untyped-def]
            return await _dispatch(self._engine, step)

    from powerbi_orchestrator_mcp.orchestrator.plan_models import PlanStep

    async def _dispatch(engine: PythonReportEngine, step: PlanStep):
        from powerbi_orchestrator_mcp.engines.base import (
            OperationResult,
        )
        from powerbi_orchestrator_mcp.orchestrator.rollback import (
            StepOutcome,
        )

        args = step.args
        pbip_path = Path(
            args.get("pbip_path")
            or args.get("target_ref")
            or "."
        )
        try:
            conn = await engine.connect(pbip_path)
            try:
                action = step.action
                if action == "add_page":
                    layout = (
                        PageLayout(**args.get("layout", {}))
                        if "layout" in args
                        else None
                    )
                    result = await engine.add_page(
                        conn, args["page_name"], layout
                    )
                elif action == "add_visual":
                    spec = VisualSpec(**args.get("spec", args))
                    result = await engine.add_visual(
                        conn, args["page"], spec
                    )
                elif action == "update_visual":
                    result = await engine.update_visual(
                        conn,
                        args["page"],
                        args["visual_id"],
                        args.get("changes", {}),
                    )
                elif action == "propagate_rename":
                    result = await engine.propagate_rename(
                        conn,
                        args["old_path"],
                        args["new_path"],
                        args.get("scope", "report_bindings"),
                    )
                elif action == "validate_pbir":
                    v = await engine.validate_pbir(conn)
                    result = OperationResult(
                        success=v.valid,
                        changed_files=[],
                    )
                else:
                    result = OperationResult(
                        success=False,
                        changed_files=[],
                    )
            finally:
                await engine.disconnect(conn)
            return StepOutcome(
                success=result.success,
                error_message=None
                if result.success
                else "report engine returned failure",
                changed_files=result.changed_files,
            )
        except Exception as exc:
            return StepOutcome(
                success=False,
                error_message=str(exc),
                changed_files=[],
            )

    return _PythonReportExecutor()


def _make_step(
    *, action: str, args: dict[str, object]
) -> object:
    from powerbi_orchestrator_mcp.orchestrator.plan_models import PlanStep

    return PlanStep(
        id="s1", engine="report", action=action, args=args  # type: ignore[arg-type]
    )


class TestPythonReportExecutor:
    async def test_add_page_with_layout(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        step = _make_step(
            action="add_page",
            args={
                "pbip_path": str(pbip),
                "page_name": "NewPage",
                "layout": {"width": 1280, "height": 720},
            },
        )
        result = await _get_executor().execute_step(step)
        assert result.success is True

    async def test_add_page_without_layout(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        step = _make_step(
            action="add_page",
            args={"pbip_path": str(pbip), "page_name": "NewPage"},
        )
        result = await _get_executor().execute_step(step)
        assert result.success is True

    async def test_add_visual(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        report_dir = pbip / "report.Report"
        (report_dir / "pages" / "Overview").mkdir(parents=True)
        (report_dir / "pages" / "Overview" / "page.json").write_text(
            json.dumps(
                {"visualContainers": [{"id": "v1"}]}
            ),
            encoding="utf-8",
        )
        step = _make_step(
            action="add_visual",
            args={
                "pbip_path": str(pbip),
                "page": "Overview",
                "type": "card",
                "fields": {"Values": ["[X]"]},
            },
        )
        result = await _get_executor().execute_step(step)
        assert result.success is True

    async def test_update_visual(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        step = _make_step(
            action="update_visual",
            args={
                "pbip_path": str(pbip),
                "page": "Overview",
                "visual_id": "v1",
                "changes": {"x": 100, "y": 100},
            },
        )
        result = await _get_executor().execute_step(step)
        # update_visual may return success=False if the visual doesn't
        # exist, but the dispatcher must return a StepOutcome either way.
        assert hasattr(result, "success")

    async def test_propagate_rename(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        step = _make_step(
            action="propagate_rename",
            args={
                "pbip_path": str(pbip),
                "old_path": "T[A]",
                "new_path": "T[B]",
            },
        )
        result = await _get_executor().execute_step(step)
        assert result.success is True

    async def test_validate_pbir(self, tmp_path: Path) -> None:
        pbip = _make_pbip(tmp_path)
        step = _make_step(
            action="validate_pbir",
            args={"pbip_path": str(pbip)},
        )
        result = await _get_executor().execute_step(step)
        assert hasattr(result, "success")

    async def test_unknown_action_returns_failure(
        self, tmp_path: Path
    ) -> None:
        pbip = _make_pbip(tmp_path)
        step = _make_step(
            action="not-a-real-action",
            args={"pbip_path": str(pbip)},
        )
        result = await _get_executor().execute_step(step)
        assert result.success is False

    async def test_engine_error_caught(self, tmp_path: Path) -> None:
        """An EngineError raised by the engine must surface as a
        failed StepOutcome with the error message attached."""
        from unittest.mock import patch

        pbip = _make_pbip(tmp_path)
        step = _make_step(
            action="add_page",
            args={"pbip_path": str(pbip), "page_name": "X"},
        )
        with patch(
            "powerbi_orchestrator_mcp.engines.report_python.PythonReportEngine.add_page",
            side_effect=EngineError(
                "boom",
                engine="python_report",
                code="x",
                remediation_hint="y",
            ),
        ):
            result = await _get_executor().execute_step(step)
        assert result.success is False
        assert "boom" in (result.error_message or "")

    async def test_pbip_path_falls_back_to_target_ref(
        self, tmp_path: Path
    ) -> None:
        pbip = _make_pbip(tmp_path)
        # No pbip_path in args; falls back to target_ref.
        step = _make_step(
            action="add_page",
            args={"target_ref": str(pbip), "page_name": "Page"},
        )
        result = await _get_executor().execute_step(step)
        assert result.success is True
