"""DAX regression testing — run queries vs baseline, diff results.

Implements ``specs/03-validation.md`` §5 (input/output schema for
``run_dax_regression``).

The orchestrator runs a set of DAX queries against a model (local PBIP
or remote workspace) and compares results to a saved baseline JSON.
Findings are surfaced per query: passed if row count + values match
within a tolerance (default 0.1%); otherwise the actual diff is
reported.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class BaselineQuery(BaseModel):
    """Single query in the baseline."""

    name: str
    query: str
    expected_rows: list[dict[str, Any]] = Field(default_factory=list)


class BaselineFile(BaseModel):
    """Baseline file schema (spec §5.2)."""

    version: str = "1.0"
    created_at: str
    model_hash: str | None = None
    queries: list[BaselineQuery] = Field(default_factory=list)


class QueryDiff(BaseModel):
    """Diff for a single query."""

    name: str
    query: str
    passed: bool
    expected_row_count: int
    actual_row_count: int
    tolerance_pct: float
    diff_summary: str = ""
    sample_diff: list[dict[str, Any]] = Field(default_factory=list)
    compared: bool = True
    """False when the baseline records the query but no expected rows.

    Such a query has nothing to compare against, so it is reported
    separately instead of being counted as a pass or a failure.
    """


class RegressionResult(BaseModel):
    """Top-level result of run_dax_regression."""

    passed: bool
    total_queries: int
    passed_queries: int
    failed_queries: int
    executed: bool = True
    message: str | None = None
    diffs: list[QueryDiff] = Field(default_factory=list)
    compared_queries: int = 0
    """Queries that were actually compared against a recorded expectation."""


class BaselineUnavailableError(RuntimeError):
    """Raised when the baseline file is missing or cannot be parsed.

    A regression run without a baseline compares nothing. Treating the
    empty result as "passed" would report a green check that never ran,
    so the run fails instead.
    """


class DaxRegressionRunner:
    """Execute DAX queries and diff vs baseline.

    MVP design: the actual DAX execution is delegated to an injected
    ``query_executor`` callable (in production, this wraps the modeling
    engine's ``execute_dax`` method). Tests inject a mock that returns
    canned rows.
    """

    DEFAULT_TOLERANCE_PCT = 0.1

    def __init__(
        self,
        query_executor: Any,  # Callable[[str], list[dict[str, Any]]]
        *,
        tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
    ) -> None:
        self._executor = query_executor
        self._tolerance = tolerance_pct

    async def run(self, baseline_path: Path) -> RegressionResult:
        """Run all baseline queries and compare against expected rows."""
        try:
            baseline = self._load_baseline(baseline_path)
        except BaselineUnavailableError as exc:
            # Nothing was compared, so nothing passed.
            return RegressionResult(
                passed=False,
                total_queries=0,
                passed_queries=0,
                failed_queries=0,
                executed=False,
                message=str(exc),
                diffs=[],
            )
        diffs: list[QueryDiff] = []
        for q in baseline.queries:
            actual_rows = await self._run_query(q.query)
            diff = self._compare(q, actual_rows)
            diffs.append(diff)
        compared = [d for d in diffs if d.compared]
        failed = sum(1 for d in compared if not d.passed)
        passed = len(compared) - failed
        return RegressionResult(
            passed=(failed == 0),
            total_queries=len(diffs),
            compared_queries=len(compared),
            passed_queries=passed,
            failed_queries=failed,
            executed=True,
            message=(
                "one or more queries drifted"
                if failed
                else (
                    "no query had a recorded expectation; nothing was compared"
                    if not compared
                    else None
                )
            ),
            diffs=diffs,
        )

    async def execute(self, query: str) -> list[dict[str, Any]]:
        """Run a single query through the configured executor."""
        return await self._run_query(query)

    async def _run_query(self, query: str) -> list[dict[str, Any]]:
        # In production, calls modeling_engine.execute_dax(query).
        # Async path for future compatibility.
        result = self._executor(query)
        if hasattr(result, "__await__"):
            return await result  # type: ignore[no-any-return]
        return result  # type: ignore[no-any-return]

    @staticmethod
    def _load_baseline(path: Path) -> BaselineFile:
        """Load the baseline file, failing loudly when it is unusable.

        A missing or corrupt baseline is not an empty baseline: it means
        there is nothing to compare against, so the run cannot pass.
        """
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise BaselineUnavailableError(
                f"baseline file not found: {path}. "
                "Create it with the run_dax_regression tool before running a regression."
            ) from exc
        except OSError as exc:
            raise BaselineUnavailableError(
                f"baseline file could not be read: {path} ({exc})"
            ) from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BaselineUnavailableError(
                f"baseline file is not valid JSON: {path} ({exc})"
            ) from exc
        try:
            return BaselineFile.model_validate(data)
        except Exception as exc:
            raise BaselineUnavailableError(
                f"baseline file does not match the expected schema: {path} ({exc})"
            ) from exc

    @staticmethod
    def passed(diffs: list[QueryDiff]) -> bool:
        """True if no diff has sample entries (i.e. all passed)."""
        return all(not d.sample_diff for d in diffs)

    def _compare(self, query: BaselineQuery, actual_rows: list[dict[str, Any]]) -> QueryDiff:
        """Compare actual vs expected with tolerance."""
        expected = query.expected_rows
        if not expected:
            # The baseline records the query but no expected rows. There
            # is nothing to compare against, so this is reported as "not
            # compared" rather than as a row-count mismatch against an
            # expectation that was never recorded.
            return QueryDiff(
                name=query.name,
                query=query.query,
                passed=True,
                expected_row_count=0,
                actual_row_count=len(actual_rows),
                tolerance_pct=self._tolerance,
                diff_summary=(
                    "no expected rows recorded in the baseline; the result was not compared"
                ),
                sample_diff=[],
                compared=False,
            )
        if len(expected) != len(actual_rows):
            return QueryDiff(
                name=query.name,
                query=query.query,
                passed=False,
                expected_row_count=len(expected),
                actual_row_count=len(actual_rows),
                tolerance_pct=self._tolerance,
                diff_summary=(
                    f"row count mismatch: expected {len(expected)}, got {len(actual_rows)}"
                ),
                sample_diff=actual_rows[:5],
            )

        sample: list[dict[str, Any]] = []
        for i, (exp, act) in enumerate(zip(expected, actual_rows, strict=False)):
            for k, v_exp in exp.items():
                v_act = act.get(k)
                if not _values_close(v_exp, v_act, self._tolerance):
                    sample.append(
                        {
                            "row": i,
                            "column": k,
                            "expected": v_exp,
                            "actual": v_act,
                        }
                    )
        return QueryDiff(
            name=query.name,
            query=query.query,
            passed=not sample,
            expected_row_count=len(expected),
            actual_row_count=len(actual_rows),
            tolerance_pct=self._tolerance,
            diff_summary=("OK" if not sample else f"{len(sample)} value diff(s)"),
            sample_diff=sample[:5],
        )


def _values_close(expected: Any, actual: Any, tolerance_pct: float) -> bool:
    """Compare two values; numeric uses tolerance, others use equality."""
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if expected == 0:
            return actual == 0
        return abs(actual - expected) / abs(expected) <= (tolerance_pct / 100.0)
    return bool(expected == actual)


__all__ = [
    "BaselineFile",
    "BaselineQuery",
    "DaxRegressionRunner",
    "QueryDiff",
    "RegressionResult",
    "_values_close",
]
