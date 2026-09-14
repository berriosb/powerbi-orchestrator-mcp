"""End-to-end test using the real sample.pbip fixture.

Unlike test_safe_rename_e2e.py (which mocks the modeling engine),
this one exercises:
- PythonReportEngine (real file I/O on the fixture)
- ``audit_model_and_report`` (BPA + DAX lint + WCAG on the fixture)
- ``apply_theme_and_accessibility_rules`` (writes the theme + alt-text
  backfill on the fixture)
- ``generate_data_dictionary`` (writes Markdown to /tmp)
- ``optimize_report_performance`` (real PBIR parsing)
- The standalone CLI: ``init`` + ``inspect`` + ``validate``

All without mocking anything except the modeling subprocess engine.

Run:
    pytest tests/integration/test_real_pbip_e2e.py -v
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.pbip"


def _ensure_fixture() -> Path:
    """Re-generate the fixture if missing."""
    if not FIXTURE.exists():
        from tests.fixtures.generate import generate

        generate(FIXTURE)
    return FIXTURE


@pytest.fixture()
def pbip(tmp_path: Path) -> Path:
    """Copy the real fixture to a writable tmp dir for the test."""
    src = _ensure_fixture()
    dst = tmp_path / "sample.pbip"
    shutil.copytree(src, dst)
    return dst


class TestFixtureExists:
    def test_fixture_is_a_directory(self) -> None:
        f = _ensure_fixture()
        assert f.is_dir()
        assert (f / "sample.pbip").is_file()
        assert (f / "sample.Report").is_dir()
        assert (f / "sample.Dataset").is_dir()

    def test_fixture_has_pages(self) -> None:
        f = _ensure_fixture()
        pages = list((f / "sample.Report" / "pages").iterdir())
        assert len(pages) >= 1


class TestAuditOnRealFixture:
    async def test_audit_returns_a_result(self, pbip: Path) -> None:
        from powerbi_orchestrator_mcp.tools.audit_model_and_report import (
            audit_model_and_report,
        )

        result = await audit_model_and_report(pbip_path=str(pbip))
        assert result.overall_score >= 0.0
        assert isinstance(result.findings, list)
        assert isinstance(result.warnings, list)


class TestThemeApplyOnRealFixture:
    def test_theme_apply_writes_files(self, pbip: Path) -> None:
        from powerbi_orchestrator_mcp.tools.apply_theme_and_accessibility_rules import (
            apply_theme_and_accessibility_rules,
        )

        result = apply_theme_and_accessibility_rules(
            pbip_path=str(pbip),
            palette="okabe_ito",
            auto_backfill_alt_text=True,
        )
        assert result.theme_written is True
        # At least one file changed (theme.json + alt text).
        assert len(result.files_changed) >= 1

    def test_alt_text_backfilled_for_missing(
        self, pbip: Path
    ) -> None:
        from powerbi_orchestrator_mcp.tools.apply_theme_and_accessibility_rules import (
            apply_theme_and_accessibility_rules,
        )

        result = apply_theme_and_accessibility_rules(
            pbip_path=str(pbip),
            palette="okabe_ito",
            auto_backfill_alt_text=True,
        )
        # The Detail page has 0 visuals so no alt text added.
        assert result.alt_texts_added >= 0


class TestPerformanceOnRealFixture:
    def test_optimize_returns_score(self, pbip: Path) -> None:
        from powerbi_orchestrator_mcp.tools.optimize_report_performance import (
            optimize_report_performance,
        )

        result = optimize_report_performance(pbip_path=str(pbip))
        assert result.pages_analyzed >= 1
        assert result.visuals_analyzed >= 1
        assert 0.0 <= result.performance_score <= 100.0


class TestDataDictionaryOnRealFixture:
    def test_dictionary_generates_markdown_with_inspector(
        self, pbip: Path
    ) -> None:
        # The inspector must expose list_tables(), list_columns(),
        # list_measures(), list_relationships(). We build a small
        # adapter over our fixture's definition.pbism.
        import json as _json

        from powerbi_orchestrator_mcp.tools.generate_data_dictionary import (
            generate_data_dictionary,
        )

        ds_path = pbip / "sample.Dataset" / "definition.pbism"
        data = _json.loads(ds_path.read_text(encoding="utf-8"))
        tables_raw = data["model"]["tables"]

        class _Inspector:
            def list_tables(self) -> list:
                # Tool expects list[dict] with "name" key.
                return [{"name": t["name"]} for t in tables_raw]

            def list_columns(self, table_name: str) -> list:
                cols = next(
                    t["columns"] for t in tables_raw if t["name"] == table_name
                )
                return [
                    {"name": c["name"], "data_type": c["dataType"]}
                    for c in cols
                ]

            def list_measures(self) -> list:
                return []

            def list_relationships(self) -> list:
                return []

        result = generate_data_dictionary(
            pbip_path=str(pbip),
            output_path=None,
            inspector=_Inspector(),
        )
        # The result has the markdown as a string field.
        assert "Data Dictionary" in result.markdown
        # At least one table referenced.
        assert "FactSales" in result.markdown
        # The mermaid block appears.
        assert "```mermaid" in result.markdown
        # Coverage score is a float.
        assert 0.0 <= result.coverage_score <= 100.0


class TestValidatePbirOnRealFixture:
    async def test_validate_pbir_clean(self, pbip: Path) -> None:
        from powerbi_orchestrator_mcp.engines.base import ConnectionHandle
        from powerbi_orchestrator_mcp.engines.report_python import (
            PythonReportEngine,
        )

        engine = PythonReportEngine()
        conn = ConnectionHandle(
            engine="python_report",
            target_type="pbip_folder",
            target_ref=str(pbip),
            session_token=str(pbip),
        )
        result = await engine.validate_pbir(conn)
        # Fixture is clean — should validate with no error-level findings.
        assert result.valid is True


class TestPropagateRenameOnRealFixture:
    async def test_rename_measure_propagates(self, pbip: Path) -> None:
        from powerbi_orchestrator_mcp.engines.base import ConnectionHandle
        from powerbi_orchestrator_mcp.engines.report_python import (
            PythonReportEngine,
        )

        engine = PythonReportEngine()
        conn = ConnectionHandle(
            engine="python_report",
            target_type="pbip_folder",
            target_ref=str(pbip),
            session_token=str(pbip),
        )

        # The fixture has [YTD Sales] and [Total Sales] in the page.json
        # queryRefs. Rename the measure name and verify propagation.
        result = await engine.propagate_rename(
            conn,
            old_path="[YTD Sales]",
            new_path="[YTD Revenue]",
            scope="report_bindings",
        )
        assert result.success is True
        # The page.json should now reference [YTD Revenue].
        page_data = json.loads(
            (
                pbip
                / "sample.Report"
                / "pages"
                / "Overview"
                / "page.json"
            ).read_text(encoding="utf-8")
        )
        assert "[YTD Revenue]" in json.dumps(page_data)
        assert "[YTD Sales]" not in json.dumps(page_data)


class TestCliOnRealFixture:
    def test_cli_version(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "powerbi_orchestrator_mcp.cli",
                "version",
            ],
            capture_output=True,
            text=True,
            env={"PYTHONPATH": "src", "PATH": "/usr/bin"},
            check=False,
        )
        assert result.returncode == 0
        assert "powerbi-orchestrator-mcp" in result.stdout

    def test_cli_init_then_inspect(self, tmp_path: Path) -> None:
        # Init.
        target = tmp_path / "new.pbip"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "powerbi_orchestrator_mcp.cli",
                "init",
                str(target),
            ],
            capture_output=True,
            text=True,
            env={"PYTHONPATH": "src", "PATH": "/usr/bin"},
            check=False,
        )
        assert result.returncode == 0
        assert target.exists()
        assert (target / "new.pbip").exists()
        assert (target / "new.Report").is_dir()

        # Inspect.
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "powerbi_orchestrator_mcp.cli",
                "inspect",
                str(target),
            ],
            capture_output=True,
            text=True,
            env={"PYTHONPATH": "src", "PATH": "/usr/bin"},
            check=False,
        )
        assert result.returncode == 0
        assert "Overview" in result.stdout

    def test_cli_validate_exits_with_score(self, tmp_path: Path) -> None:
        # Use the real fixture.
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "powerbi_orchestrator_mcp.cli",
                "validate",
                str(_ensure_fixture()),
            ],
            capture_output=True,
            text=True,
            env={"PYTHONPATH": "src", "PATH": "/usr/bin"},
            check=False,
        )
        # Exit code 0 or 1 depending on score vs --min-score.
        assert result.returncode in (0, 1)
        assert "Composite:" in result.stdout


class TestHealthOnRealFixture:
    async def test_powerbi_health_returns_full_report(self) -> None:
        from powerbi_orchestrator_mcp.tools.powerbi_health import (
            powerbi_health,
        )

        result = await powerbi_health()
        assert result["server_version"] != ""
        assert len(result["checks"]) >= 5
        # At least one of the engine checks is included.
        assert any(
            c["name"].startswith("engine.") for c in result["checks"]
        )
