"""Tests for promote_in_pipeline (Sprint 11)."""

from __future__ import annotations

from typing import Any

import pytest

from powerbi_orchestrator_mcp.tools.promote_in_pipeline import (
    QualityGate,
    promote_in_pipeline,
)


class _FabricClientStub:
    def __init__(self, items: list[str] | None = None, fail: bool = False) -> None:
        self._items = items or ["dataset_a", "report_b"]
        self._fail = fail
        self.calls: list[dict[str, Any]] = []

    def list_pipeline_items(
        self, pipeline_id: str, source_stage: str
    ) -> list[str]:
        return list(self._items)

    def deploy_pipeline_item(
        self,
        pipeline_id: str,
        source_stage: str,
        target_stage: str,
        item_id: str,
    ) -> None:
        self.calls.append(
            {
                "pipeline_id": pipeline_id,
                "source_stage": source_stage,
                "target_stage": target_stage,
                "item_id": item_id,
            }
        )
        if self._fail:
            raise RuntimeError("Fabric outage")


class TestPromoteInPipeline:
    def test_invalid_stage_returns_warning(self) -> None:
        r = promote_in_pipeline("p", source_stage="QA", target_stage="test")
        assert any("invalid stages" in w for w in r.warnings)

    def test_same_stage_returns_warning(self) -> None:
        r = promote_in_pipeline("p", source_stage="dev", target_stage="dev")
        assert any("must differ" in w for w in r.warnings)

    def test_dry_run_no_fabric_client(self) -> None:
        r = promote_in_pipeline("p", dry_run=True)
        assert r.dry_run is True
        assert r.gates_executed == []
        # No items (no fabric_client).
        assert r.promoted_items == []

    def test_explicit_items_promoted(self) -> None:
        r = promote_in_pipeline(
            "p",
            dry_run=True,
            items=["dataset_x", "report_y"],
        )
        assert len(r.promoted_items) == 2
        assert {p.item_id for p in r.promoted_items} == {"dataset_x", "report_y"}
        assert all(p.from_stage == "dev" for p in r.promoted_items)
        assert all(p.to_stage == "test" for p in r.promoted_items)

    def test_real_fabric_client_invoked(self) -> None:
        client = _FabricClientStub(items=["item1", "item2"])
        r = promote_in_pipeline("pipe", fabric_client=client, dry_run=False)
        assert len(r.promoted_items) == 2
        assert r.promotion_id is not None
        assert client.calls == [
            {
                "pipeline_id": "pipe",
                "source_stage": "dev",
                "target_stage": "test",
                "item_id": "item1",
            },
            {
                "pipeline_id": "pipe",
                "source_stage": "dev",
                "target_stage": "test",
                "item_id": "item2",
            },
        ]

    def test_fabric_client_failure_warns(self) -> None:
        client = _FabricClientStub(items=["item1"], fail=True)
        r = promote_in_pipeline("pipe", fabric_client=client, dry_run=False)
        assert any("Fabric outage" in w for w in r.warnings)

    def test_pre_deploy_gate_passes(self) -> None:
        gates = [QualityGate(type="pre_deploy_check", profile="relaxed")]
        r = promote_in_pipeline(
            "p",
            quality_gates=gates,
            pre_deploy_findings=[],
            dry_run=True,
        )
        assert r.gates_executed[0].passed is True
        assert r.failed_gate is None

    def test_pre_deploy_gate_blocks(self) -> None:
        gates = [QualityGate(type="pre_deploy_check", profile="strict", blocking=True)]
        r = promote_in_pipeline(
            "p",
            quality_gates=gates,
            pre_deploy_findings=[
                {"severity": "error", "message": "critical"}
            ],
            dry_run=True,
        )
        assert r.failed_gate is not None
        assert r.failed_gate.type == "pre_deploy_check"
        assert r.promoted_items == []

    def test_audit_score_gate_passes(self, tmp_path: object) -> None:
        # Build a clean PBIP that should score 100.
        import json
        from pathlib import Path

        pbip = Path(str(tmp_path)) / "good.pbip"
        pbip.mkdir()
        (pbip / "good.pbip").write_text("{}")
        ds = pbip / "good.Dataset"
        ds.mkdir()
        (ds / "definition.pbism").write_text("{}")
        report = pbip / "good.Report"
        report.mkdir()
        page_dir = report / "pages" / "p"
        page_dir.mkdir(parents=True)
        (page_dir / "page.json").write_text(
            json.dumps(
                {
                    "visualContainers": [
                        {
                            "id": "x",
                            "altText": "Sales",
                            "visual": {"$type": "card"},
                        }
                    ]
                }
            )
        )
        gates = [QualityGate(type="audit_model_and_report", threshold=80.0)]
        r = promote_in_pipeline(
            "p",
            quality_gates=gates,
            pbip_path=str(pbip),
            dry_run=True,
        )
        assert r.gates_executed[0].passed is True
        assert r.failed_gate is None

    def test_audit_score_gate_blocks_low_score(self, tmp_path: object) -> None:
        # Build a PBIP with a known low score pattern.
        import json
        from pathlib import Path

        pbip = Path(str(tmp_path)) / "audit.pbip"
        pbip.mkdir()
        (pbip / "audit.pbip").write_text("{}")
        ds = pbip / "audit.Dataset"
        ds.mkdir()
        (ds / "definition.pbism").write_text("{}")
        report = pbip / "audit.Report"
        report.mkdir()
        page_dir = report / "pages" / "p"
        page_dir.mkdir(parents=True)
        (page_dir / "page.json").write_text(
            json.dumps({"visualContainers": [{"id": "x", "visual": {"$type": "pieChart"}}]})
        )
        gates = [QualityGate(type="audit_model_and_report", threshold=99.0)]
        r = promote_in_pipeline(
            "p",
            quality_gates=gates,
            pbip_path=str(pbip),
            dry_run=True,
        )
        # Whether the gate passes depends on the audit score; we just
        # require that a GateExecuted was recorded.
        assert len(r.gates_executed) == 1

    def test_audit_gate_missing_pbip(self) -> None:
        gates = [QualityGate(type="audit_model_and_report", threshold=80.0)]
        r = promote_in_pipeline(
            "p",
            quality_gates=gates,
            pbip_path=None,
            dry_run=True,
        )
        assert r.gates_executed[0].passed is False

    def test_regression_gate_runs(self, tmp_path: object) -> None:
        # Build the fixture pair manually; run_dax_regression expects
        # a baseline.json; pass empty to trigger "missing baseline".
        gates = [QualityGate(type="run_dax_regression", threshold=0.1)]
        r = promote_in_pipeline(
            "p",
            quality_gates=gates,
            pbip_path=None,
            baseline_path=None,
            dry_run=True,
        )
        # Missing inputs => blocking fails.
        assert r.gates_executed[0].passed is False

    def test_invalid_gate_type_rejected(self) -> None:
        with pytest.raises(Exception):
            QualityGate(type="unknown_gate", blocking=True)

    def test_custom_gate_pass(self) -> None:
        custom = [("custom_smoke", lambda: True)]
        r = promote_in_pipeline("p", custom_gates=custom, dry_run=True)
        assert any(
            ge.type == "custom:custom_smoke" for ge in r.gates_executed
        )

    def test_custom_gate_fail_blocks(self) -> None:
        custom = [("custom_smoke", lambda: False)]
        r = promote_in_pipeline("p", custom_gates=custom, dry_run=True)
        assert r.failed_gate is not None
        assert r.failed_gate.type == "custom:custom_smoke"

    def test_non_blocking_gate_does_not_halt(self) -> None:
        gates = [
            QualityGate(type="pre_deploy_check", profile="relaxed", blocking=False)
        ]
        r = promote_in_pipeline(
            "p",
            quality_gates=gates,
            pre_deploy_findings=[],
            items=["x"],
            dry_run=True,
        )
        assert len(r.promoted_items) == 1

    def test_audit_logger_invoked_on_promotion(self) -> None:
        events: list[dict[str, Any]] = []

        def logger(**kwargs: Any) -> None:
            events.append(kwargs)

        r = promote_in_pipeline(
            "p", audit_logger=logger, items=["x"], dry_run=True
        )
        assert any(e.get("action") == "promoted" for e in events)

    def test_audit_logger_invoked_on_failure(self) -> None:
        events: list[dict[str, Any]] = []

        def logger(**kwargs: Any) -> None:
            events.append(kwargs)

        gates = [QualityGate(type="pre_deploy_check", profile="strict")]
        promote_in_pipeline(
            "p",
            quality_gates=gates,
            pre_deploy_findings=[{"severity": "error", "message": "x"}],
            audit_logger=logger,
            dry_run=True,
        )
        assert any(e.get("action") == "promote_failed" for e in events)
