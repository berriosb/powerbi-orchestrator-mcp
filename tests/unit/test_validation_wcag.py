"""Tests for validation.accessibility.wcag_auditor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.validation.accessibility.wcag_auditor import WcagAuditor


@pytest.fixture()
def pbip_with_pages(tmp_path: Path) -> Path:
    """Create a PBIP folder with two pages, one with a visual that has
    alt text, one without."""
    pbip = tmp_path / "test.pbip"
    pbip.mkdir()
    (pbip / "test.pbip").write_text("{}")
    report_dir = pbip / "test.Report"
    report_dir.mkdir()

    # Page 1: visual with good alt text.
    p1 = report_dir / "pages" / "Overview"
    p1.mkdir(parents=True)
    (p1 / "page.json").write_text(
        json.dumps(
            {
                "visualContainers": [
                    {
                        "id": "v1",
                        "altText": "Total Sales by Region for 2024 Q4",
                        "visual": {"$type": "card"},
                    }
                ]
            }
        )
    )

    # Page 2: visual with no alt text + placeholder alt text + decorative.
    p2 = report_dir / "pages" / "Detail"
    p2.mkdir(parents=True)
    (p2 / "page.json").write_text(
        json.dumps(
            {
                "visualContainers": [
                    {"id": "v_no_alt", "visual": {"$type": "barChart"}},
                    {"id": "v_short", "altText": "Sales", "visual": {"$type": "card"}},
                    {
                        "id": "v_placeholder",
                        "altText": "Visual v1 placeholder text",
                        "visual": {"$type": "lineChart"},
                    },
                ]
            }
        )
    )

    return pbip


class TestWcagAuditor:
    def test_audit_pbip(self, pbip_with_pages: Path) -> None:
        auditor = WcagAuditor()
        result = auditor.audit_pbip(pbip_with_pages)
        assert result.pages_audited == 2
        assert result.visuals_audited == 4  # 1 + 3
        assert result.score < 100.0  # at least one issue

    def test_no_alt_text_emits_error(self, pbip_with_pages: Path) -> None:
        auditor = WcagAuditor()
        result = auditor.audit_pbip(pbip_with_pages)
        errors = [f for f in result.findings if f.severity == "error"]
        assert any(f.rule_id == "WCAG_1_1_1_NON_TEXT_CONTENT" for f in errors)

    def test_short_alt_text_emits_warning(self, pbip_with_pages: Path) -> None:
        auditor = WcagAuditor()
        result = auditor.audit_pbip(pbip_with_pages)
        warnings = [f for f in result.findings if f.severity == "warning"]
        assert any(f.rule_id == "WCAG_1_1_1_ALT_TEXT_TOO_SHORT" for f in warnings)

    def test_placeholder_alt_text_emits_warning(self, pbip_with_pages: Path) -> None:
        auditor = WcagAuditor()
        result = auditor.audit_pbip(pbip_with_pages)
        warnings = [f for f in result.findings if f.severity == "warning"]
        assert any(f.rule_id == "WCAG_1_1_1_PLACEHOLDER_ALT" for f in warnings)

    def test_missing_pages_dir_emits_error(self, tmp_path: Path) -> None:
        # Empty PBIP with the .Report dir but no pages/ inside.
        empty_pbip = tmp_path / "empty.pbip"
        empty_pbip.mkdir()
        (empty_pbip / "empty.Report").mkdir()
        auditor = WcagAuditor()
        result = auditor.audit_pbip(empty_pbip)
        assert any(f.rule_id == "WCAG_NO_PAGES" for f in result.findings)
        assert result.score == 0.0

    def test_clean_pbip_scores_100(self, tmp_path: Path) -> None:
        pbip = tmp_path / "clean.pbip"
        pbip.mkdir()
        (pbip / "clean.pbip").write_text("{}")
        report_dir = pbip / "clean.Report"
        report_dir.mkdir()
        page_dir = report_dir / "pages" / "Overview"
        page_dir.mkdir(parents=True)
        (page_dir / "page.json").write_text(
            json.dumps(
                {
                    "visualContainers": [
                        {
                            "id": "v1",
                            "altText": "Sales by Region, Q4 2024, in USD",
                            "visual": {"$type": "card"},
                        }
                    ]
                }
            )
        )
        auditor = WcagAuditor()
        result = auditor.audit_pbip(pbip)
        assert result.score == 100.0
        assert result.findings == []
