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
    """
    baseline = validate_write_path(baseline_path)
    if not baseline.exists() and queries:
        # First run — save baseline.
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "created_at": "",
                    "queries": [
                        BaselineQuery(name=q["name"], query=q["query"]).model_dump()
                        for q in queries
                    ],
                },
                indent=2,
            )
        )
        return RegressionResult(
            passed=True,
            total_queries=0,
            passed_queries=0,
            failed_queries=0,
            diffs=[],
        )

    if query_executor is None:
        return RegressionResult(
            passed=False,
            executed=False,
            total_queries=0,
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
