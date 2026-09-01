"""Tests for the 8 MVP tools implemented in Sprint 7."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from powerbi_orchestrator_mcp.tools.apply_theme_and_accessibility_rules import (
    OKABE_ITO_PALETTE,
    apply_theme_and_accessibility_rules,
)
from powerbi_orchestrator_mcp.tools.audit_model_and_report import (
    audit_model_and_report,
)
from powerbi_orchestrator_mcp.tools.diff_models import diff_models
from powerbi_orchestrator_mcp.tools.generate_data_dictionary import (
    generate_data_dictionary,
)
from powerbi_orchestrator_mcp.tools.pre_deploy_check import pre_deploy_check
from powerbi_orchestrator_mcp.tools.run_dax_regression import run_dax_regression

# ---------------------------------------------------------------------------
# pre_deploy_check
# ---------------------------------------------------------------------------


class TestPreDeployCheck:
    def test_strict_blocks_on_any_error(self) -> None:
        findings = [{"severity": "error", "message": "x"}]
        result = pre_deploy_check(findings, profile="strict")
        assert result.passed is False

    def test_standard_allows_warnings(self) -> None:
        findings = [{"severity": "warning", "message": "x"}] * 5
        result = pre_deploy_check(findings, profile="standard")
        assert result.passed is True

    def test_unknown_profile_raises(self) -> None:
        with pytest.raises(ValueError):
            pre_deploy_check([], profile="nonexistent")


# ---------------------------------------------------------------------------
# diff_models
# ---------------------------------------------------------------------------


def _inspector_with(snapshot: dict[str, Any]):
    """Return an inspector that returns ``snapshot`` for any path."""

    def inspector(path: str) -> dict[str, Any]:
        return snapshot

    return inspector


class TestDiffModels:
    def test_returns_empty_when_no_inspector(self) -> None:
        result = diff_models(before="A", after="B")
        assert result.total_changes == 0

    def test_returns_diff_when_inspector_provided(self) -> None:
        a = {
            "tables": [{"name": "T1"}],
            "measures": [],
            "relationships": [],
        }
        b = {
            "tables": [{"name": "T2"}],
            "measures": [],
            "relationships": [],
        }
        result = diff_models("A", "B", inspector=_inspector_with(a))
        # Override to use the OTHER snapshot — but with one snapshot we
        # only see one side of the diff. Use a smarter inspector.
        def inspector2(path: str) -> dict[str, Any]:
            return a if "A" in path else b
        result = diff_models("modelA", "modelB", inspector=inspector2)
        assert result.total_changes == 2
        assert result.added_objects == 1
        assert result.removed_objects == 1


# ---------------------------------------------------------------------------
# run_dax_regression
# ---------------------------------------------------------------------------


class TestRunDaxRegression:
    async def test_creates_baseline_when_missing(self, tmp_path: Path) -> None:
        baseline = tmp_path / "baseline.json"
        queries = [{"name": "Q1", "query": "EVALUATE ROW(\"x\", 1)"}]
        result = await run_dax_regression(
            baseline_path=str(baseline),
            queries=queries,
        )
        assert baseline.exists()
        assert result.passed is True
        assert result.total_queries == 0  # baseline created, no comparison

    async def test_existing_baseline_passes_with_injected_executor(
        self, tmp_path: Path
    ) -> None:
        baseline = tmp_path / "baseline.json"
        baseline.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "created_at": "",
                    "queries": [
                        {
                            "name": "Q1",
                            "query": "EVALUATE ROW(\"x\", 100)",
                            "expected_rows": [{"x": 100}],
                        }
                    ],
                }
            )
        )

        def executor(q: str) -> list[dict[str, Any]]:
            return [{"x": 100}]

        result = await run_dax_regression(
            baseline_path=str(baseline),
            query_executor=executor,
        )
        assert result.passed is True

    async def test_existing_baseline_fails_on_drift(
        self, tmp_path: Path
    ) -> None:
        baseline = tmp_path / "baseline.json"
        baseline.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "created_at": "",
                    "queries": [
                        {
                            "name": "Q1",
                            "query": "EVALUATE ROW(\"x\", 100)",
                            "expected_rows": [{"x": 100}],
                        }
                    ],
                }
            )
        )

        def executor(q: str) -> list[dict[str, Any]]:
            return [{"x": 200}]  # 100% drift > 0.1% tolerance

        result = await run_dax_regression(
            baseline_path=str(baseline),
            query_executor=executor,
        )
        assert result.passed is False


# ---------------------------------------------------------------------------
# run_refresh
# ---------------------------------------------------------------------------


class TestRunRefresh:
    async def test_run_refresh_requires_credentials_or_fails_gracefully(self) -> None:
        # No env vars set → auth fails → AuthModeError raised.
        from powerbi_orchestrator_mcp.cloud.auth import AuthModeError
        from powerbi_orchestrator_mcp.tools.run_refresh import run_refresh

        with pytest.raises(AuthModeError):
            await run_refresh(
                workspace_id="ws-abc",
                dataset_id="ds-xyz",
                auth_mode="service_principal",
            )


# ---------------------------------------------------------------------------
# audit_model_and_report
# ---------------------------------------------------------------------------


class TestAuditModelAndReport:
    async def test_runs_with_clean_pbip(self, tmp_path: Path) -> None:
        # Create a minimal PBIP with one page + one visual with good alt.
        pbip = tmp_path / "test.pbip"
        pbip.mkdir()
        (pbip / "test.pbip").write_text("{}")
        report_dir = pbip / "test.Report"
        report_dir.mkdir()
        page_dir = report_dir / "pages" / "Overview"
        page_dir.mkdir(parents=True)
        (page_dir / "page.json").write_text(
            json.dumps(
                {
                    "visualContainers": [
                        {
                            "id": "v1",
                            "altText": "Sales by Region for Q4 2024",
                            "visual": {"$type": "card"},
                        }
                    ]
                }
            )
        )
        result = await audit_model_and_report(pbip_path=str(pbip))
        assert result.wcag_score == 100.0
        assert result.wcag_findings_count == 0
        # BPA is mocked; no findings expected.
        assert result.bpa_findings_count == 0
        assert result.overall_score > 0.0

    async def test_runs_with_dax_lint_findings(self, tmp_path: Path) -> None:
        pbip = tmp_path / "test.pbip"
        pbip.mkdir()
        (pbip / "test.pbip").write_text("{}")
        report_dir = pbip / "test.Report"
        report_dir.mkdir()
        page_dir = report_dir / "pages" / "Overview"
        page_dir.mkdir(parents=True)
        (page_dir / "page.json").write_text("{}")

        result = await audit_model_and_report(
            pbip_path=str(pbip),
            dax_measures={
                "M1": "X := [A] / [B]",  # BP_DIVIDE_VS_SLASH
            },
        )
        assert result.dax_lint_findings_count >= 1
        assert any(
            f["rule_id"] == "BP_DIVIDE_VS_SLASH" for f in result.findings
        )

    async def test_warns_on_missing_path(self, tmp_path: Path) -> None:
        result = await audit_model_and_report(
            pbip_path=str(tmp_path / "nonexistent.pbip"),
        )
        assert any("does not exist" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# generate_data_dictionary
# ---------------------------------------------------------------------------


class TestGenerateDataDictionary:
    def test_no_inspector_returns_warning(self, tmp_path: Path) -> None:
        result = generate_data_dictionary(pbip_path=str(tmp_path / "x"))
        assert "no inspector provided" in " ".join(result.warnings).lower()
        assert result.coverage_score == 0.0

    def test_writes_markdown_with_mermaid_diagram(self, tmp_path: Path) -> None:
        class FakeInspector:
            def list_tables(self) -> list[dict[str, Any]]:
                return [
                    {"name": "T1", "description": "Test table"},
                    {"name": "T2", "description": None},
                ]

            def list_columns(self, table: str) -> list[dict[str, Any]]:
                if table == "T1":
                    return [{"name": "id", "data_type": "int64", "description": None}]
                return [{"name": "name", "data_type": "string", "description": None}]

            def list_measures(self) -> list[dict[str, Any]]:
                return []

            def list_relationships(self) -> list[dict[str, Any]]:
                return []

        result = generate_data_dictionary(
            pbip_path=str(tmp_path),
            inspector=FakeInspector(),
        )
        assert "T1" in result.markdown
        assert "T2" in result.markdown
        assert "```mermaid" in result.markdown
        assert "erDiagram" in result.markdown
        # Coverage: 0 with description / 2 total = 0.0
        assert result.coverage_score == 0.0
        # T2 has no description → warning.
        assert any("missing description" in w for w in result.warnings)
        # Columns missing description: 2 (one per table).
        assert len(result.columns_missing_description) == 2

    def test_writes_to_output_path(self, tmp_path: Path) -> None:
        out_path = tmp_path / "out.md"

        class FakeInspector:
            def list_tables(self) -> list[dict[str, Any]]:
                return []

            def list_columns(self, t: str) -> list[dict[str, Any]]:
                return []

            def list_measures(self) -> list[dict[str, Any]]:
                return []

            def list_relationships(self) -> list[dict[str, Any]]:
                return []

        generate_data_dictionary(
            pbip_path=str(tmp_path),
            output_path=str(out_path),
            inspector=FakeInspector(),
        )
        assert out_path.exists()
        assert "# Data Dictionary" in out_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# apply_theme_and_accessibility_rules
# ---------------------------------------------------------------------------


@pytest.fixture()
def pbip_with_pages(tmp_path: Path) -> Path:
    """PBIP with one page + 3 visuals: 1 with alt, 2 without."""
    pbip = tmp_path / "test.pbip"
    pbip.mkdir()
    (pbip / "test.pbip").write_text("{}")
    report_dir = pbip / "test.Report"
    report_dir.mkdir()
    page_dir = report_dir / "pages" / "Overview"
    page_dir.mkdir(parents=True)
    (page_dir / "page.json").write_text(
        json.dumps(
            {
                "visualContainers": [
                    {
                        "id": "v1",
                        "altText": "Total Sales by Region for 2024 Q4",
                        "visual": {
                            "$type": "card",
                            "projections": {
                                "Values": [{"queryRef": "Sales[Total Sales]"}]
                            },
                        },
                    },
                    {
                        "id": "v2_no_alt",
                        "visual": {"$type": "barChart"},
                    },
                    {
                        "id": "v3_no_alt",
                        "visual": {"$type": "lineChart"},
                    },
                ]
            }
        )
    )
    return pbip


class TestApplyThemeAndAccessibility:
    def test_palette_constants(self) -> None:
        # 8-color Okabe-Ito palette.
        assert len(OKABE_ITO_PALETTE) == 8

    def test_applies_theme_to_pbip(self, pbip_with_pages: Path) -> None:
        result = apply_theme_and_accessibility_rules(
            pbip_path=str(pbip_with_pages),
            palette="okabe_ito",
            auto_backfill_alt_text=False,
        )
        # theme.json should be written.
        assert result.theme_written is True
        theme_files = [f for f in result.files_changed if "theme.json" in f]
        assert len(theme_files) == 1
        # WCAG score should be low (2 visuals without alt).
        assert result.wcag_score_before < 100.0

    def test_backfills_alt_text(self, pbip_with_pages: Path) -> None:
        result = apply_theme_and_accessibility_rules(
            pbip_path=str(pbip_with_pages),
            palette="okabe_ito",
            auto_backfill_alt_text=True,
        )
        # 2 visuals had no alt → should be backfilled.
        assert result.alt_texts_added == 2
        # After backfill, WCAG score should improve (still not 100%
        # because backfill uses placeholder template, but warnings go away).
        page_path = (
            pbip_with_pages / "test.Report" / "pages" / "Overview" / "page.json"
        )
        data = json.loads(page_path.read_text(encoding="utf-8"))
        for vc in data["visualContainers"]:
            assert vc.get("altText"), f"visual {vc['id']} still missing alt"

    def test_warns_on_missing_path(self, tmp_path: Path) -> None:
        result = apply_theme_and_accessibility_rules(
            pbip_path=str(tmp_path / "nonexistent"),
        )
        assert any("does not exist" in w for w in result.warnings)
        assert result.theme_written is False


# ---------------------------------------------------------------------------
# deploy_to_workspace (mock mode)
# ---------------------------------------------------------------------------


class TestDeployToWorkspace:
    async def test_blocks_on_gate_failure(self, tmp_path: Path) -> None:
        from powerbi_orchestrator_mcp.tools.deploy_to_workspace import (
            deploy_to_workspace,
        )

        pbip = tmp_path / "test.pbip"
        pbip.mkdir()
        (pbip / "test.pbip").write_text("{}")

        findings = [{"severity": "error", "message": "x"}]
        result = await deploy_to_workspace(
            pbip_path=str(pbip),
            workspace_id="ws-abc",
            findings=findings,
            gate_profile="strict",
            mock=True,
        )
        assert result.publish_ok is False
        assert any("gate_blocked" in e for e in result.errors)

    async def test_passes_gate_and_publishes_in_mock(
        self, tmp_path: Path
    ) -> None:
        from powerbi_orchestrator_mcp.tools.deploy_to_workspace import (
            deploy_to_workspace,
        )

        pbip = tmp_path / "test.pbip"
        pbip.mkdir()
        (pbip / "test.pbip").write_text("{}")

        result = await deploy_to_workspace(
            pbip_path=str(pbip),
            workspace_id="ws-abc",
            findings=[],
            gate_profile="standard",
            mock=True,
        )
        assert result.publish_ok is True
        assert result.schedule_ok is True
        assert result.refresh_id == "mock-refresh-id"
