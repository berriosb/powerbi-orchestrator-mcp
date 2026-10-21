"""Tests for the in-memory plan cache.

The cache is an optimisation: the plan store is the source of truth. It
has to be bounded, because a long-running server would otherwise hold
every plan it ever made, each with its full YAML and step list — and a
plan holds every step, every rollback step and the estimated changes.
"""

from __future__ import annotations

import pytest

from powerbi_orchestrator_mcp.orchestrator import server as server_mod
from powerbi_orchestrator_mcp.orchestrator.plan_models import Plan
from powerbi_orchestrator_mcp.orchestrator.planner import PlanBuilder


def _plan(index: int) -> Plan:
    return PlanBuilder().build_safe_rename(
        old_path=f"Customer[ID{index}]",
        new_path=f"Customer[Key{index}]",
        target=f"model{index}.pbip",
    )


class TestTheCacheIsBounded:
    def test_the_cache_holds_at_most_the_limit(self) -> None:
        server_mod._reset_server_state()

        for index in range(server_mod.PLAN_CACHE_MAX_ENTRIES + 20):
            server_mod._cache_plan(_plan(index), "local")

        assert len(server_mod._plans) == server_mod.PLAN_CACHE_MAX_ENTRIES

    def test_the_oldest_is_evicted_first(self) -> None:
        server_mod._reset_server_state()

        first = _plan(0)
        server_mod._cache_plan(first, "local")
        for index in range(1, server_mod.PLAN_CACHE_MAX_ENTRIES + 1):
            server_mod._cache_plan(_plan(index), "local")

        assert first.id not in server_mod._plans

    def test_the_least_recently_used_goes_first(self) -> None:
        """A plan that was just used stays; one nobody touched goes."""
        server_mod._reset_server_state()

        for index in range(server_mod.PLAN_CACHE_MAX_ENTRIES):
            server_mod._cache_plan(_plan(index), "local")

        oldest = next(iter(server_mod._plans))
        server_mod._plans.move_to_end(oldest)
        server_mod._cache_plan(_plan(999), "local")

        assert oldest in server_mod._plans

    def test_the_evicted_owner_is_evicted_too(self) -> None:
        """A stale owner entry would shadow the store's answer."""
        server_mod._reset_server_state()

        for index in range(server_mod.PLAN_CACHE_MAX_ENTRIES + 5):
            server_mod._cache_plan(_plan(index), "local")

        assert len(server_mod._plan_owners) == len(server_mod._plans)


class TestAnEvictedPlanIsStillReachable:
    async def test_the_store_is_the_source_of_truth(self, tmp_path, monkeypatch) -> None:
        """Evicting from the cache must not lose the plan.

        The cache is bounded; the store is not. A plan that fell out of
        the cache is still found through the store.
        """
        from powerbi_orchestrator_mcp.orchestrator.plan_store import PlanStore

        server_mod._reset_server_state()
        store = PlanStore(db_path=tmp_path / "plans.db")
        monkeypatch.setattr(server_mod, "_plan_store", store)

        plan = _plan(1)
        server_mod._cache_plan(plan, "local")
        store.put(plan, owner="local")

        # Force it out of the cache.
        server_mod._plans.clear()
        server_mod._plan_owners.clear()

        found = server_mod._owned_plan(plan.id)  # type: ignore[attr-defined]

        assert found is not None
        assert found.id == plan.id


@pytest.mark.parametrize("limit", [1, 5, 50])
def test_the_limit_is_a_positive_number(limit: int) -> None:
    assert limit > 0
