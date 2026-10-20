"""run_dax_regression tool — wraps DaxRegressionRunner (SPEC §6.1 tool #8)."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from powerbi_orchestrator_mcp.validation.dax_regression import (
    BaselineQuery,
    DaxRegressionRunner,
    RegressionResult,
)
from powerbi_orchestrator_mcp.validation.path_safety import validate_write_path


class RunDaxRegression(BaseModel):
    """Input schema for ``run_dax_regression`` tool (SPEC §6.1 #8).

    Two modes:
    - baseline_path: load queries + expected rows from a JSON file.
    - queries: inline queries (expected_rows empty → treated as
      regression-of-record; baseline created on first run).
    """

    baseline_path: str
    queries: list[dict[str, str]] | None = None  # [{"name": ..., "query": ...}]
    tolerance_pct: float = 0.1
    query_executor: Any = None  # injected; in production comes from modeling engine


async def run_dax_regression(
    baseline_path: str,
    queries: list[dict[str, str]] | None = None,
    *,
    tolerance_pct: float = 0.1,
    query_executor: Any = None,
) -> RegressionResult:
    """Run all baseline queries and compare against expected rows.

    If ``queries`` is provided and the baseline doesn't exist yet, the
    queries are saved as a new baseline (no comparison performed).

    When a query executor is available the new baseline records the rows
    it actually returned, so the next run has something to compare
    against instead of failing on a row-count mismatch against an
    expectation that was never stored.
    """
    baseline = validate_write_path(baseline_path)
    if not baseline.exists() and queries:
        # First run — save baseline.
        runner = DaxRegressionRunner(query_executor, tolerance_pct=tolerance_pct)
        recorded: list[dict[str, Any]] = []
        for q in queries:
            expected_rows: list[dict[str, Any]] = []
            if query_executor is not None:
                try:
                    expected_rows = await runner.execute(str(q["query"]))
                except Exception:  # noqa: BLE001 - a failed probe is not fatal here
                    expected_rows = []
            recorded.append(
                BaselineQuery(
                    name=str(q["name"]),
                    query=str(q["query"]),
                    expected_rows=expected_rows,
                ).model_dump()
            )
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "created_at": "",
                    "queries": recorded,
                },
                indent=2,
            )
        )
        with_expectations = sum(1 for r in recorded if r.get("expected_rows"))
        return RegressionResult(
            passed=True,
            total_queries=0,
            compared_queries=0,
            passed_queries=0,
            failed_queries=0,
            executed=False,
            message=(
                f"baseline created with {len(recorded)} query/queries "
                f"({with_expectations} with recorded expected rows); "
                "no comparison was performed on this run"
            ),
            diffs=[],
        )

    if query_executor is None:
        return RegressionResult(
            passed=False,
            executed=False,
            total_queries=0,
            compared_queries=0,
            passed_queries=0,
            failed_queries=0,
            diffs=[],
            message=(
                "no_query_executor: no DAX query was executed, so no regression "
                "verdict is available. Pass workspace_id and dataset_id to run "
                "against a live dataset."
            ),
        )

    runner = DaxRegressionRunner(query_executor, tolerance_pct=tolerance_pct)

    return await runner.run(baseline)
