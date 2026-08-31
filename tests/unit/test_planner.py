"""Tests for orchestrator.planner — PlanBuilder + 4 MVP templates (spec §2.2)."""

from __future__ import annotations

import re

import pytest
import yaml

from powerbi_orchestrator_mcp.orchestrator.plan_models import (
    PlanOptions,
    PlanTemplate,
    PlanValidationError,
    RiskThresholdExceededError,
    UnknownTemplateError,
)
from powerbi_orchestrator_mcp.orchestrator.planner import PlanBuilder, write_plan_to_file

# Default thresholds in MVP are conservative; high-risk templates like
# `deploy` (risk_score=0.7) need an explicit relaxed threshold.
_RELAXED = PlanOptions(max_impact_threshold=80)


@pytest.fixture()
def builder() -> PlanBuilder:
    return PlanBuilder()


class TestBuildDispatch:
    def test_unknown_template_raises(self, builder: PlanBuilder) -> None:
        with pytest.raises(UnknownTemplateError):
            builder.build("not_a_real_template")

    @pytest.mark.parametrize(
        "template",
        [
            PlanTemplate.SAFE_RENAME,
            PlanTemplate.AUDIT,
            PlanTemplate.DEPLOY,
            PlanTemplate.DAX_REGRESSION,
        ],
    )
    def test_named_template_without_args_raises(
        self, builder: PlanBuilder, template: str
    ) -> None:
        """build() with a known template name (but no specialized args) errors out."""
        with pytest.raises(PlanValidationError):
            builder.build(template)

    def test_known_templates_listed(self, builder: PlanBuilder) -> None:
        assert PlanTemplate.SAFE_RENAME in builder.TEMPLATES
        assert PlanTemplate.AUDIT in builder.TEMPLATES
        assert PlanTemplate.DEPLOY in builder.TEMPLATES
        assert PlanTemplate.DAX_REGRESSION in builder.TEMPLATES


class TestSafeRename:
    def test_produces_four_steps_with_rollback(self, builder: PlanBuilder) -> None:
        plan = builder.build_safe_rename(
            old_path="Customer[ID]",
            new_path="Customer[CustomerKey]",
            scope="report_bindings",
        )
        assert len(plan.steps) == 4
        assert len(plan.rollback_steps) == 2  # rename-model + propagate-bindings

        # Step order matches spec §3.2 example
        assert plan.steps[0].action == "create_snapshot"
        assert plan.steps[1].action == "column.update"
        assert plan.steps[2].action == "propagate_rename"
        assert plan.steps[3].action == "pbip_validate_full"

        # Dependencies form a chain
        assert plan.steps[1].depends_on == [plan.steps[0].id]
        assert plan.steps[2].depends_on == [plan.steps[1].id]
        assert plan.steps[3].depends_on == [plan.steps[2].id]

        # Each reversible step has a rollback_step
        assert plan.steps[1].rollback_step is not None
        assert plan.steps[2].rollback_step is not None

    def test_validators_attached(self, builder: PlanBuilder) -> None:
        plan = builder.build_safe_rename(
            old_path="T[A]",
            new_path="T[B]",
        )
        assert "pbip_validate_model" in plan.steps[1].validators
        assert "pbir_validate" in plan.steps[2].validators

    def test_risk_score_by_scope(self, builder: PlanBuilder) -> None:
        rb = builder.build_safe_rename(old_path="T[A]", new_path="T[B]", scope="report_bindings")
        m = builder.build_safe_rename(old_path="T[A]", new_path="T[B]", scope="model_only")
        assert rb.risk_score < m.risk_score

    def test_yaml_contains_required_headers(self, builder: PlanBuilder) -> None:
        plan = builder.build_safe_rename(old_path="T[A]", new_path="T[B]")
        assert "plan_id:" in plan.yaml
        assert "generated_by:" in plan.yaml
        assert "powerbi-orchestrator-mcp" in plan.yaml
        assert "created_at:" in plan.yaml
        assert "risk_score:" in plan.yaml
        assert "plan_schema_version:" in plan.yaml

    def test_yaml_round_trips(self, builder: PlanBuilder) -> None:
        plan = builder.build_safe_rename(old_path="T[A]", new_path="T[B]")
        parsed = yaml.safe_load(plan.yaml)
        assert parsed["plan_id"] == plan.id
        assert parsed["plan_schema_version"] == "0.1.0"
        assert isinstance(parsed["steps"], list)
        assert len(parsed["steps"]) == 4

    def test_yaml_step_ids_match_plan_ids(self, builder: PlanBuilder) -> None:
        plan = builder.build_safe_rename(old_path="T[A]", new_path="T[B]")
        parsed = yaml.safe_load(plan.yaml)
        for step_dict, plan_step in zip(parsed["steps"], plan.steps, strict=True):
            assert step_dict["id"] == plan_step.id

    def test_blank_args_rejected(self, builder: PlanBuilder) -> None:
        with pytest.raises(PlanValidationError):
            builder.build_safe_rename(old_path="", new_path="T[B]")
        with pytest.raises(PlanValidationError):
            builder.build_safe_rename(old_path="T[A]", new_path="")


class TestAudit:
    def test_three_steps(self, builder: PlanBuilder) -> None:
        plan = builder.build_audit(
            target="pbip:./out/sales.pbip",
            checks=["bpa", "wcag", "dax_lint"],
        )
        assert [s.action for s in plan.steps] == [
            "gather_metadata",
            "run_checks_parallel",
            "aggregate_score",
        ]
        assert plan.rollback_steps == []  # read-only

    def test_more_checks_higher_risk(self, builder: PlanBuilder) -> None:
        p1 = builder.build_audit(target="x", checks=["a"])
        p2 = builder.build_audit(target="x", checks=["a", "b", "c", "d"])
        assert p2.risk_score > p1.risk_score

    def test_empty_checks_rejected(self, builder: PlanBuilder) -> None:
        with pytest.raises(PlanValidationError):
            builder.build_audit(target="x", checks=[])


class TestDeploy:
    def test_five_steps_with_one_rollback(self, builder: PlanBuilder) -> None:
        plan = builder.build_deploy(
            pbip_path="./out/sales.pbip",
            workspace_id="ws_abc",
            refresh_daily_hour=6,
            options=_RELAXED,
        )
        assert len(plan.steps) == 5
        assert [s.action for s in plan.steps] == [
            "pre_deploy_check",
            "workspace.publish_pbip",
            "workspace.bind_gateway",
            "workspace.set_refresh_schedule",
            "workspace.refresh",
        ]
        # Only the publish step has a rollback_step per spec.
        assert len(plan.rollback_steps) == 1
        assert plan.rollback_steps[0].action == "workspace.delete_items"

    def test_invalid_hour_rejected(self, builder: PlanBuilder) -> None:
        with pytest.raises(PlanValidationError):
            builder.build_deploy(
                pbip_path="x", workspace_id="y", refresh_daily_hour=24
            )
        with pytest.raises(PlanValidationError):
            builder.build_deploy(
                pbip_path="x", workspace_id="y", refresh_daily_hour=-1
            )

    def test_risk_score_high(self, builder: PlanBuilder) -> None:
        plan = builder.build_deploy(
            pbip_path="x", workspace_id="y", options=_RELAXED
        )
        assert plan.risk_score >= 0.5


class TestDaxRegression:
    def test_three_steps(self, builder: PlanBuilder) -> None:
        plan = builder.build_dax_regression(
            baseline_path="./baselines/2025-Q4.json",
            queries=["EVALUATE ROW(...)", "EVALUATE SUMMARIZE(...)"],
        )
        assert [s.action for s in plan.steps] == [
            "load_baseline",
            "execute_queries_parallel",
            "diff_baseline",
        ]
        assert plan.rollback_steps == []

    def test_more_queries_higher_risk(self, builder: PlanBuilder) -> None:
        p1 = builder.build_dax_regression(baseline_path="x", queries=["a"])
        p2 = builder.build_dax_regression(baseline_path="x", queries=["a", "b", "c", "d", "e"])
        assert p2.risk_score > p1.risk_score

    def test_empty_queries_rejected(self, builder: PlanBuilder) -> None:
        with pytest.raises(PlanValidationError):
            builder.build_dax_regression(baseline_path="x", queries=[])


class TestRiskThreshold:
    def test_risk_threshold_exceeded_raises(self, builder: PlanBuilder) -> None:
        # deploy has risk_score 0.7; max_impact_threshold=50 → threshold=0.5.
        opts = PlanOptions(max_impact_threshold=50)
        with pytest.raises(RiskThresholdExceededError):
            builder.build_deploy(pbip_path="x", workspace_id="y", options=opts)

    def test_risk_threshold_relaxed_passes(self, builder: PlanBuilder) -> None:
        opts = PlanOptions(max_impact_threshold=80)  # 0.8 threshold
        plan = builder.build_deploy(pbip_path="x", workspace_id="y", options=opts)
        assert plan.risk_score <= 0.8


class TestPlanYamlFormat:
    def test_yaml_header_has_all_spec_keys(self, builder: PlanBuilder) -> None:
        plan = builder.build_audit(target="x", checks=["a"])
        # Per spec §5: plan_id, generated_by, target, created_at, risk_score,
        # plan_schema_version.
        for key in (
            "plan_id",
            "generated_by",
            "created_at",
            "risk_score",
            "plan_schema_version",
        ):
            assert f"{key}:" in plan.yaml, f"missing {key} in plan YAML"

    def test_plan_id_matches_format(self, builder: PlanBuilder) -> None:
        plan = builder.build_audit(target="x", checks=["a"])
        m = re.match(r"^plan_\d{4}-\d{2}-\d{2}_[A-Za-z0-9_-]{6,12}$", plan.id)
        assert m is not None

    def test_plan_id_unique_across_calls(self, builder: PlanBuilder) -> None:
        seen = {builder.build_audit(target="x", checks=["a"]).id for _ in range(10)}
        assert len(seen) == 10


class TestWritePlanToFile:
    def test_round_trip(self, builder: PlanBuilder, tmp_path) -> None:
        plan = builder.build_audit(target="x", checks=["a"])
        path = tmp_path / "plan.yaml"
        write_plan_to_file(plan, path)
        assert path.exists()
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert loaded["plan_id"] == plan.id
