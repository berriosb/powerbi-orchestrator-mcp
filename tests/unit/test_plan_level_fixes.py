"""Tests for the plan-level fixes that were still open after v1.19.0.

- The snapshot label was built from the plan id without normalising it,
  so it violated the project's own identifier contract — and whether it
  did depended on the random suffix, which is what made the failure
  intermittent.
- The orphan cutoff was shorter than the longest plausible step, so a
  live execution was reported as abandoned.
"""

from __future__ import annotations

import re

import pytest

from powerbi_orchestrator_mcp.orchestrator.identifiers import (
    SNAPSHOT_LABEL_PATTERN,
    make_snapshot_label,
)
from powerbi_orchestrator_mcp.orchestrator.plan_executions import (
    ORPHAN_HEARTBEAT_CUTOFF_S,
)
from powerbi_orchestrator_mcp.orchestrator.planner import PlanBuilder


class TestSnapshotLabel:
    def test_the_label_satisfies_the_project_contract(self) -> None:
        plan = PlanBuilder().build_safe_rename(
            old_path="Customer[ID]",
            new_path="Customer[CustomerKey]",
            target="model.pbip",
        )
        label = str(plan.steps[0].args["label"])

        assert SNAPSHOT_LABEL_PATTERN.match(label)
        assert make_snapshot_label(label) == label

    def test_the_label_is_valid_for_every_suffix(self) -> None:
        """The suffix is random, so the label must not depend on it.

        The nanoid alphabet contains hyphens, so a suffix can end with
        one — which made the label invalid some of the time.
        """
        for suffix in ("abcdefgh", "abc-defg", "abc-def-", "a-b-c-d-e"):
            plan_id = f"plan_2026-10-20_{suffix}"
            raw = re.sub(r"-+", "-", f"pre-rename-{plan_id}".lower().replace("_", "-"))
            label = make_snapshot_label(raw.strip("-"))

            assert SNAPSHOT_LABEL_PATTERN.match(label), label
            assert not label.startswith("-")
            assert not label.endswith("-")


class TestOrphanCutoff:
    def test_the_cutoff_outlasts_the_longest_engine_timeout(self) -> None:
        """A live execution must not be reported as abandoned.

        The engine timeouts are the longest a step can legitimately run:
        300 s for Tabular Editor, 180 s for superbi-mcp, 120 s for the
        modeling MCP server.
        """
        from powerbi_orchestrator_mcp.engines.timeouts import DEFAULT_TIMEOUTS

        longest = max(cfg.max_s for cfg in DEFAULT_TIMEOUTS.values())
        assert longest < ORPHAN_HEARTBEAT_CUTOFF_S

    def test_the_cutoff_is_not_a_minute(self) -> None:
        """60 seconds was shorter than a publish or a DAX query."""
        assert ORPHAN_HEARTBEAT_CUTOFF_S >= 1800


@pytest.mark.parametrize("suffix", ["abcdefgh", "abc-defg", "abc-def-"])
def test_the_planner_accepts_any_suffix(suffix: str) -> None:
    import powerbi_orchestrator_mcp.orchestrator.identifiers as identifiers_mod

    original = identifiers_mod.new_plan_id
    identifiers_mod.new_plan_id = lambda *a, **k: f"plan_2026-10-20_{suffix}"
    try:
        plan = PlanBuilder().build_safe_rename(
            old_path="Customer[ID]",
            new_path="Customer[CustomerKey]",
            target="model.pbip",
        )
        label = str(plan.steps[0].args["label"])
        assert SNAPSHOT_LABEL_PATTERN.match(label)
    finally:
        identifiers_mod.new_plan_id = original
    assert identifiers_mod.new_plan_id()
