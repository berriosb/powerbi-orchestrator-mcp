"""Plan builder - NL intent to versionable Plan YAML.

Implements specs/01-orchestrator.md §2.2 + §3.2.

In MVP, ``plan_change`` only accepts the 4 predefined templates listed
below. NL-to-plan decomposition is out of scope (per spec §2.2 step 4).
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, ClassVar

import yaml

from powerbi_orchestrator_mcp.orchestrator.identifiers import new_plan_id
from powerbi_orchestrator_mcp.orchestrator.plan_models import (
    EstimatedChanges,
    Plan,
    PlanOptions,
    PlanStep,
    PlanTemplate,
    PlanValidationError,
    RiskThresholdExceededError,
    UnknownTemplateError,
)

# Header keys included at the top of every plan YAML (spec §5 + §5.1).
_PLAN_HEADER_KEYS = (
    "plan_id",
    "generated_by",
    "target",
    "created_at",
    "risk_score",
    "plan_schema_version",
)


class PlanBuilder:
    """Builds ``Plan`` instances from template names + args.

    Each template method returns a fully-populated ``Plan`` (steps +
    rollback_steps + estimated_changes + risk_score). The YAML form is
    serializable, Git-friendly, and consumable by ``apply_plan``.
    """

    GENERATED_BY: ClassVar[str] = "powerbi-orchestrator-mcp v0.1.0"
    PLAN_SCHEMA_VERSION: ClassVar[str] = "0.1.0"

    TEMPLATES: ClassVar[frozenset[str]] = frozenset(
        {
            PlanTemplate.SAFE_RENAME,
            PlanTemplate.AUDIT,
            PlanTemplate.DEPLOY,
            PlanTemplate.DAX_REGRESSION,
        }
    )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        intent: str,
        options: PlanOptions | None = None,  # noqa: ARG002
    ) -> Plan:
        """Build a Plan from a template name.

        In MVP, ``build()`` only validates that ``intent`` is a recognized
        template name and raises — the actual args-required templates
        have their own ``build_*()`` methods. This dispatch exists so the
        orchestrator can pre-flight a template name before calling the
        specialized builder.

        Args:
            intent: A template name (one of ``self.TEMPLATES``).
            options: Tunable knobs. ``None`` uses defaults.

        Returns:
            Never returns — always raises.

        Raises:
            UnknownTemplateError: ``intent`` is not a recognized template.
            PlanValidationError: args are missing; caller should use the
                specialized ``build_*()`` method instead.
        """
        if intent not in self.TEMPLATES:
            raise UnknownTemplateError(
                f"unknown template {intent!r}; "
                f"supported: {sorted(self.TEMPLATES)}"
            )
        raise PlanValidationError(
            f"template {intent!r} requires structured args; use the "
            f"corresponding build_*() method (e.g. build_safe_rename)"
        )

    # ------------------------------------------------------------------
    # Templates (MVP)
    # ------------------------------------------------------------------
    #
    # Each build_*() method is called directly by the orchestrator with
    # the full set of args. The build() dispatch above exists for
    # pre-flight validation only (spec §3.2 contract: plan_change returns
    # a Plan or a structured error).

    def build_safe_rename(
        self,
        *,
        old_path: str,
        new_path: str,
        scope: str = "report_bindings",
        options: PlanOptions | None = None,
    ) -> Plan:
        """Build the ``safe_rename`` plan (spec §3.2 + tools/safe-rename.md)."""
        opts = options or PlanOptions()
        self._require_nonempty("old_path", old_path)
        self._require_nonempty("new_path", new_path)
        self._require_nonempty("scope", scope)

        plan_id = new_plan_id()

        snapshot_step = PlanStep(
            id=f"{plan_id}:s1",
            engine="validation",
            action="create_snapshot",
            args={"label": f"pre-rename-{plan_id}"},
        )

        rename_model = PlanStep(
            id=f"{plan_id}:s2",
            engine="modeling",
            action="column.update",
            args={"old_path": old_path, "new_name": _extract_name(new_path)},
            depends_on=[snapshot_step.id],
            validators=["pbip_validate_model"],
            rollback_step=PlanStep(
                id=f"{plan_id}:s2-revert",
                engine="modeling",
                action="column.update",
                args={
                    "old_path": new_path,
                    "new_name": _extract_name(old_path),
                },
            ),
        )

        propagate_bindings = PlanStep(
            id=f"{plan_id}:s3",
            engine="report",
            action="propagate_rename",
            args={
                "old_path": old_path,
                "new_path": new_path,
                "scope": scope,
            },
            depends_on=[rename_model.id],
            validators=["pbir_validate"],
            rollback_step=PlanStep(
                id=f"{plan_id}:s3-revert",
                engine="report",
                action="propagate_rename",
                args={
                    "old_path": new_path,
                    "new_path": old_path,
                    "scope": scope,
                },
            ),
        )

        validate_all = PlanStep(
            id=f"{plan_id}:s4",
            engine="validation",
            action="pbip_validate_full",
            args={},
            depends_on=[propagate_bindings.id],
        )

        plan = Plan(
            id=plan_id,
            steps=[snapshot_step, rename_model, propagate_bindings, validate_all],
            rollback_steps=[
                rb
                for rb in (rename_model.rollback_step, propagate_bindings.rollback_step)
                if rb is not None
            ],
            risk_score=0.3 if scope == "report_bindings" else 0.5,
            estimated_changes=EstimatedChanges(
                files_affected=2,
                measures_affected=1,
                visuals_affected=1,
                rollback_complexity="moderate",
            ),
        )
        plan.yaml = self._serialize(plan)
        self._enforce_risk_threshold(plan, opts)
        return plan

    def build_audit(
        self,
        *,
        target: str,
        checks: list[str],
        options: PlanOptions | None = None,
    ) -> Plan:
        """Build the ``audit`` plan (spec §3.2)."""
        opts = options or PlanOptions()
        self._require_nonempty("target", target)
        if not checks:
            raise PlanValidationError("audit requires at least one check")

        plan_id = new_plan_id()

        gather = PlanStep(
            id=f"{plan_id}:s1",
            engine="validation",
            action="gather_metadata",
            args={"target": target, "checks": checks},
        )

        run_checks = PlanStep(
            id=f"{plan_id}:s2",
            engine="validation",
            action="run_checks_parallel",
            args={"checks": checks},
            depends_on=[gather.id],
        )

        aggregate = PlanStep(
            id=f"{plan_id}:s3",
            engine="validation",
            action="aggregate_score",
            args={},
            depends_on=[run_checks.id],
        )

        risk = min(0.1 + 0.05 * len(checks), 0.6)
        plan = Plan(
            id=plan_id,
            steps=[gather, run_checks, aggregate],
            rollback_steps=[],
            risk_score=risk,
            estimated_changes=EstimatedChanges(
                files_affected=0,
                measures_affected=0,
                visuals_affected=0,
                rollback_complexity="trivial",
            ),
        )
        plan.yaml = self._serialize(plan)
        self._enforce_risk_threshold(plan, opts)
        return plan

    def build_deploy(
        self,
        *,
        pbip_path: str,
        workspace_id: str,
        refresh_daily_hour: int = 6,
        options: PlanOptions | None = None,
    ) -> Plan:
        """Build the ``deploy`` plan (spec §3.2 + tools/deploy-to-workspace.md)."""
        opts = options or PlanOptions()
        self._require_nonempty("pbip_path", pbip_path)
        self._require_nonempty("workspace_id", workspace_id)
        if not (0 <= refresh_daily_hour <= 23):
            raise PlanValidationError(
                f"refresh_daily_hour must be 0..23, got {refresh_daily_hour}"
            )

        plan_id = new_plan_id()

        pre = PlanStep(
            id=f"{plan_id}:s1",
            engine="validation",
            action="pre_deploy_check",
            args={"profile": "standard"},
        )

        publish = PlanStep(
            id=f"{plan_id}:s2",
            engine="cloud",
            action="workspace.publish_pbip",
            args={"pbip_path": pbip_path, "workspace_id": workspace_id},
            depends_on=[pre.id],
            rollback_step=PlanStep(
                id=f"{plan_id}:s2-revert",
                engine="cloud",
                action="workspace.delete_items",
                args={"workspace_id": workspace_id},
            ),
        )

        bind = PlanStep(
            id=f"{plan_id}:s3",
            engine="cloud",
            action="workspace.bind_gateway",
            args={"workspace_id": workspace_id},
            depends_on=[publish.id],
        )

        schedule = PlanStep(
            id=f"{plan_id}:s4",
            engine="cloud",
            action="workspace.set_refresh_schedule",
            args={
                "workspace_id": workspace_id,
                "hour": refresh_daily_hour,
            },
            depends_on=[bind.id],
        )

        refresh = PlanStep(
            id=f"{plan_id}:s5",
            engine="cloud",
            action="workspace.refresh",
            args={"workspace_id": workspace_id, "wait": True},
            depends_on=[schedule.id],
        )

        plan = Plan(
            id=plan_id,
            steps=[pre, publish, bind, schedule, refresh],
            rollback_steps=(
                [publish.rollback_step] if publish.rollback_step else []
            ),
            risk_score=0.7,
            estimated_changes=EstimatedChanges(
                files_affected=4,
                measures_affected=0,
                visuals_affected=0,
                rollback_complexity="complex",
            ),
        )
        plan.yaml = self._serialize(plan)
        self._enforce_risk_threshold(plan, opts)
        return plan

    def build_dax_regression(
        self,
        *,
        baseline_path: str,
        queries: list[str],
        options: PlanOptions | None = None,
    ) -> Plan:
        """Build the ``dax_regression`` plan (spec §3.2)."""
        opts = options or PlanOptions()
        self._require_nonempty("baseline_path", baseline_path)
        if not queries:
            raise PlanValidationError("dax_regression requires at least one query")

        plan_id = new_plan_id()

        load = PlanStep(
            id=f"{plan_id}:s1",
            engine="validation",
            action="load_baseline",
            args={"path": baseline_path},
        )

        exec_q = PlanStep(
            id=f"{plan_id}:s2",
            engine="modeling",
            action="execute_queries_parallel",
            args={"queries": queries},
            depends_on=[load.id],
        )

        diff = PlanStep(
            id=f"{plan_id}:s3",
            engine="validation",
            action="diff_baseline",
            args={"tolerance_pct": 0.1},
            depends_on=[exec_q.id],
        )

        risk = min(0.2 + 0.05 * len(queries), 0.6)
        plan = Plan(
            id=plan_id,
            steps=[load, exec_q, diff],
            rollback_steps=[],
            risk_score=risk,
            estimated_changes=EstimatedChanges(
                files_affected=1,
                measures_affected=0,
                visuals_affected=0,
                rollback_complexity="trivial",
            ),
        )
        plan.yaml = self._serialize(plan)
        self._enforce_risk_threshold(plan, opts)
        return plan

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_nonempty(name: str, value: str) -> None:
        if not value:
            raise PlanValidationError(f"{name} must be non-empty")

    @staticmethod
    def _enforce_risk_threshold(plan: Plan, opts: PlanOptions) -> None:
        threshold = opts.max_impact_threshold / 100.0
        if plan.risk_score > threshold:
            raise RiskThresholdExceededError(
                f"plan {plan.id} risk_score {plan.risk_score:.2f} exceeds "
                f"threshold {threshold:.2f}"
            )

    def _serialize(self, plan: Plan) -> str:
        """Serialize a Plan to YAML with the spec-mandated header comments."""
        body = plan.model_dump(mode="json", exclude={"yaml"})
        # Reorder keys to match spec §5 example.
        header: dict[str, Any] = {
            "plan_id": plan.id,
            "generated_by": self.GENERATED_BY,
            "target": _target_to_str(body.get("target")),
            "created_at": _now_iso(),
            "risk_score": plan.risk_score,
            "plan_schema_version": self.PLAN_SCHEMA_VERSION,
        }

        steps = body.pop("steps", [])
        rollback_steps = body.pop("rollback_steps", [])

        rendered = yaml.safe_dump(
            {**header, "metadata": {}, "steps": steps},
            sort_keys=False,
            allow_unicode=True,
        )
        if rollback_steps:
            rendered += (
                "# rollback_steps are encoded in each step.rollback_step above\n"
            )
        return rendered


def _extract_name(path: str) -> str:
    """Extract the trailing identifier from a path like ``Table[Column]``."""
    if "[" in path and path.endswith("]"):
        return path.rsplit("[", 1)[1][:-1]
    return path.split(".")[-1]


def _target_to_str(target: Any) -> str:
    if target is None:
        return ""
    if isinstance(target, str):
        return target
    return str(target)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat()


def write_plan_to_file(plan: Plan, path: Path) -> None:
    """Convenience: write a plan's YAML to disk (used by Git commit workflows)."""
    if not plan.yaml:
        raise PlanValidationError(
            f"plan {plan.id} has empty yaml; call PlanBuilder.build* first"
        )
    path.write_text(plan.yaml, encoding="utf-8")
